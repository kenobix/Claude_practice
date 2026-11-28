package hub

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

const managementBytes = 128 << 20

type hubSizes struct {
	Database, WAL, SHM, Other, Objects, Temporary, Reservations int64
}

func (v hubSizes) metadata() int64 { return v.Database + v.WAL + v.SHM + v.Other }
func (v hubSizes) total() int64    { return v.metadata() + v.Objects + v.Temporary }
func (v hubSizes) max(other hubSizes) hubSizes {
	return hubSizes{max(v.Database, other.Database), max(v.WAL, other.WAL), max(v.SHM, other.SHM),
		max(v.Other, other.Other), max(v.Objects, other.Objects), max(v.Temporary, other.Temporary), max(v.Reservations, other.Reservations)}
}

func measureHub(root string, reserved int64) (hubSizes, error) {
	var v hubSizes
	v.Reservations = reserved
	err := filepath.WalkDir(root, func(name string, e os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if e.IsDir() {
			return nil
		}
		stat, err := e.Info()
		if err != nil {
			return err
		}
		n := stat.Size()
		if filepath.Base(filepath.Dir(name)) == "blobs" {
			if strings.HasSuffix(e.Name(), ".part") {
				v.Temporary += n
			} else {
				v.Objects += n
			}
			return nil
		}
		switch e.Name() {
		case safefs.StateDB:
			v.Database += n
		case safefs.StateDB + "-wal":
			v.WAL += n
		case safefs.StateDB + "-shm":
			v.SHM += n
		default:
			v.Other += n
		}
		return nil
	})
	if err == nil && (v.metadata() > managementBytes || v.total() > 2<<30 || v.Database > 8192*4096) {
		err = fmt.Errorf("storage envelope exceeded: %+v", v)
	}
	return v, err
}

func maxWirePath() string {
	return strings.Join([]string{strings.Repeat("<", 255), strings.Repeat("<", 255), strings.Repeat("<", 255), strings.Repeat("<", 254), "<"}, "/")
}

func largeReceipt(t *testing.T, l *lab) (protocol.Change, protocol.Receipt) {
	t.Helper()
	path := maxWirePath()
	if len(path) != 1024 || safefs.Validate(path) != nil {
		t.Fatal("invalid 1024 byte test specimen")
	}
	first := l.change(t, strings.Repeat("a", 32), path, "P", 0)
	mustAccept(t, l, first, 1, "applied")
	ready := first
	ready.Base = 1
	var stored protocol.Receipt
	must(t, json.Unmarshal(mustAccept(t, l, first, 1, "applied"), &stored))
	stored.Result = "unchanged"
	return ready, stored
}

func fillWideReceipts(t *testing.T, l *lab, c protocol.Change, r protocol.Receipt) (int, hubSizes) {
	t.Helper()
	root := filepath.Join(l.dir, "hub")
	var peaks hubSizes
	var peakTotal int64
	observe := func(stage string, log bool) {
		t.Helper()
		s, err := measureHub(root, 0)
		must(t, err)
		peaks = peaks.max(s)
		peakTotal = max(peakTotal, s.total())
		if log {
			t.Logf("sqlite sizes %s: %+v metadata=%d total=%d", stage, s, s.metadata(), s.total())
		}
	}
	write := func(start, n int) error {
		return l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
			stmt, err := tx.Prepare("INSERT INTO receipts VALUES (?,?,?,?,?,?)")
			if err != nil {
				return err
			}
			defer stmt.Close()
			for i := start; i < start+n; i++ {
				c.Operation = fmt.Sprintf("%032x", i)
				r.Operation = c.Operation
				request, err := json.Marshal(c)
				if err != nil {
					return err
				}
				if len(request) > protocol.MaxJSON {
					return errors.New("request exceeds bound")
				}
				reply, err := json.Marshal(r)
				if err != nil {
					return err
				}
				if _, err := stmt.Exec(l.provision.Space, c.Generation, c.Client, c.Operation, request, reply); err != nil {
					return err
				}
			}
			observe("bulk-before-commit", false)
			return nil
		})
	}
	// Commit thousands of 1,024-byte path records in one transaction, then
	// use smaller increments to make room for subsequent real HTTP commits.
	must(t, write(1, 2250))
	observe("bulk-committed", true)
	i := 2251
	for {
		var pages int
		must(t, l.h.store.Conn.QueryRowContext(t.Context(), "PRAGMA page_count").Scan(&pages))
		if pages >= 7800 {
			break
		}
		must(t, write(i, 40))
		i += 40
		observe("increment-committed", false)
	}
	l.reopen(t)
	observe("reopened", true)
	t.Logf("wide receipt setup independent logical peaks: %+v peak_total=%d", peaks, peakTotal)
	return i, peaks
}

func TestP4_11_SQLiteLimitAndSIGKILL(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	c, r := largeReceipt(t, l)
	seedID, basePeak := fillWideReceipts(t, l, c, r)
	root := filepath.Join(l.dir, "hub")
	peak := basePeak
	observe := func(at string) {
		t.Helper()
		v, err := measureHub(root, 0)
		must(t, err)
		peak = peak.max(v)
		t.Logf("sqlite sizes %s: %+v metadata=%d total=%d", at, v, v.metadata(), v.total())
	}
	for _, point := range []string{"before-change-commit", "change-committed"} {
		next := c
		next.Operation = fmt.Sprintf("%032x", seedID)
		seedID++
		prior := l.count(t, "receipts")
		l.stop()
		child := startStoppedHub(t, l, "serve", point)
		requestBody, err := json.Marshal(next)
		must(t, err)
		req, err := http.NewRequestWithContext(t.Context(), "POST", "http://"+child.addr+"/v1/changes", bytes.NewReader(requestBody))
		must(t, err)
		req.Header.Set("Authorization", "Bearer "+l.provision.Clients[0].Token)
		req.Header.Set("Content-Type", protocol.JSON)
		done := make(chan error, 1)
		go func() {
			resp, err := http.DefaultClient.Do(req)
			if resp != nil {
				io.Copy(io.Discard, resp.Body)
				resp.Body.Close()
			}
			done <- err
		}()
		child.kill(t, point)
		if err := <-done; err == nil {
			t.Fatal("response arrived after stopped operation")
		}
		observe(point + "-on-disk")
		l.start(t)
		observe(point + "-reopened")
		want := prior
		if point == "change-committed" {
			want++
		}
		if l.count(t, "receipts") != want {
			t.Fatalf("wrong history count after %s", point)
		}
		mustAccept(t, l, next, 1, "unchanged")
		if l.count(t, "receipts") != prior+1 {
			t.Fatal("replay duplicated a receipt")
		}
	}
	var refused bool
	start := l.count(t, "receipts")
	for attempt := 0; attempt < 1000; attempt++ {
		w := c
		w.Operation = fmt.Sprintf("%032x", seedID+attempt)
		status, b := l.post(t, w, 0)
		size, err := measureHub(root, 0)
		must(t, err)
		peak = peak.max(size)
		if status == 507 {
			assertCode(t, status, b, 507, "storage_full")
			if l.count(t, "receipts") != start+attempt {
				t.Fatalf("SQLITE_FULL changed history count")
			}
			refused = true
			observe("SQLITE_FULL")
			break
		}
		if status != 200 {
			t.Fatalf("near SQLite limit HTTP %d %s", status, b)
		}
	}
	if !refused {
		t.Fatal("fixture did not reach fixed SQLite page limit")
	}
	total := l.count(t, "receipts")
	if total >= protocol.MaxReceipts {
		t.Fatalf("did not exercise earlier SQLite rejection: %d", total)
	}
	l.reopen(t)
	observe("full-after-reopen")
	l.hubFile(t, c.Path, "P", "client-a", 1)
	l.hubFile(t, notes, "P", "client-a", 1)
	l.checkBlob(t, "P", g)
	mustAccept(t, l, l.history(t)[0].Change, 1, "applied")
	if l.count(t, "receipts") != total {
		t.Fatal("replay altered full DB")
	}
	t.Logf("sqlite final: independent logical peaks %+v, accepted receipts=%d; fixed page budget exhausted before 8192 receipts", peak, total)
}
