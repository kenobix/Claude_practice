package hub

import (
	"bytes"
	"crypto/sha256"
	"database/sql"
	"encoding/binary"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

func (l *lab) putSample(t *testing.T, g, id string) {
	t.Helper()
	b, d := sample(t, id)
	must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, d, b))
}

func mustAccept(t *testing.T, l *lab, c protocol.Change, revision int64, result string) []byte {
	t.Helper()
	code, b := l.post(t, c, 0)
	var got protocol.Receipt
	must(t, json.Unmarshal(b, &got))
	if code != 200 || got.Operation != c.Operation || got.Entry.Revision != revision || got.Result != result {
		t.Fatalf("HTTP %d receipt %s; want op %s revision %d result %s", code, b, c.Operation, revision, result)
	}
	return b
}

func seedEntries(t *testing.T, l *lab, total int, tombstones bool) {
	t.Helper()
	_, d := sample(t, "E")
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		var hash any = d.Hash
		revision := 1
		if tombstones {
			hash = nil
			revision = 2
		}
		s, err := tx.Prepare("INSERT INTO entries VALUES (?,?,?,?,?,?,'client-a',?)")
		if err != nil {
			return err
		}
		defer s.Close()
		for i := 1; i < total; i++ {
			if _, err := s.Exec(l.provision.Space, fmt.Sprintf("existing-%04d", i), revision, hash, 0, tombstones, fmt.Sprintf("%032x", i)); err != nil {
				return err
			}
		}
		return nil
	}))
}

func TestP4_10_ActualRowLimits(t *testing.T) {
	t.Run("live", func(t *testing.T) {
		l := newLab(t)
		g := l.seed(t)
		l.putSample(t, g, "E")
		seedEntries(t, l, safefs.MaxFiles-1, false)
		if l.count(t, "entries") != 999 {
			t.Fatal("bad 999 fixture")
		}
		created := l.change(t, strings.Repeat("a", 32), "new", "E", 0)
		response := mustAccept(t, l, created, 1, "applied")
		over := l.change(t, strings.Repeat("b", 32), "over", "E", 0)
		code, b := l.post(t, over, 0)
		assertCode(t, code, b, 507, "storage_limit")
		if l.count(t, "entries") != 1000 || l.count(t, "receipts") != 2 {
			t.Fatal("live limit mutated data")
		}
		if got := mustAccept(t, l, created, 1, "applied"); !bytes.Equal(response, got) {
			t.Fatal("replay changed")
		}
		deleted := created
		deleted.Operation = strings.Repeat("c", 32)
		deleted.Hash, deleted.Base, deleted.Deleted = nil, 1, true
		mustAccept(t, l, deleted, 2, "applied")
		l.assertDeleted(t, "new", g, "client-a", 2)
		mustAccept(t, l, over, 1, "applied")
		if l.count(t, "entries") != 1001 || l.count(t, "receipts") != 4 {
			t.Fatal("tombstone did not use exactly one full row")
		}
		l.checkBlob(t, "P", g)
		l.checkBlob(t, "E", g)
		t.Log("live=999 -> 1000 -> 507; tombstone releases one live slot and retains row")
	})
	t.Run("all-paths", func(t *testing.T) {
		l := newLab(t)
		g := l.seed(t)
		l.putSample(t, g, "E")
		seedEntries(t, l, protocol.MaxPaths-1, true)
		l.reopen(t)
		if l.count(t, "entries") != 4095 {
			t.Fatal("bad 4095 fixture")
		}
		last := l.change(t, strings.Repeat("a", 32), "new", "E", 0)
		response := mustAccept(t, l, last, 1, "applied")
		n := l.change(t, strings.Repeat("b", 32), "over", "E", 0)
		code, b := l.post(t, n, 0)
		assertCode(t, code, b, 507, "storage_limit")
		if l.count(t, "entries") != 4096 || l.count(t, "receipts") != 2 {
			t.Fatal("all-paths limit mutated data")
		}
		if got := mustAccept(t, l, last, 1, "applied"); !bytes.Equal(response, got) {
			t.Fatal("replay changed")
		}
		n.Path, n.Base = "existing-0001", 2
		mustAccept(t, l, n, 3, "applied")
		if l.count(t, "entries") != 4096 {
			t.Fatal("resurrection added a row")
		}
		l.assertDeleted(t, "existing-0002", g, "client-a", 2)
		l.checkBlob(t, "P", g)
		l.checkBlob(t, "E", g)
		t.Log("all rows=4095 -> 4096 -> 507; resurrection reused existing tombstone row")
	})
	t.Run("receipts", func(t *testing.T) {
		l := newLab(t)
		g := l.seed(t)
		l.putSample(t, g, "E")
		created := l.change(t, strings.Repeat("c", 32), "empty", "E", 0)
		mustAccept(t, l, created, 1, "applied")
		must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
			s, err := tx.Prepare("INSERT INTO receipts VALUES (?,?,?,?,?,?)")
			if err != nil {
				return err
			}
			defer s.Close()
			for i := 1; i < protocol.MaxReceipts-2; i++ {
				req := created
				req.Base, req.Operation = 1, fmt.Sprintf("%032x", i)
				r := protocol.Receipt{Generation: g, Operation: req.Operation, Result: "unchanged", Entry: protocol.Entry{
					Path: req.Path, Revision: 1, Hash: req.Hash, Size: req.Size, Client: req.Client, Operation: created.Operation,
				}}
				body, err := json.Marshal(req)
				if err != nil {
					return err
				}
				response, err := json.Marshal(r)
				if err != nil {
					return err
				}
				if _, err := s.Exec(l.provision.Space, g, req.Client, req.Operation, body, response); err != nil {
					return err
				}
			}
			return nil
		}))
		l.reopen(t)
		if l.count(t, "receipts") != 8191 {
			t.Fatal("bad 8191 fixture")
		}
		final := created
		final.Base, final.Operation = 1, strings.Repeat("a", 32)
		response := mustAccept(t, l, final, 1, "unchanged")
		over := final
		over.Operation = strings.Repeat("b", 32)
		code, b := l.post(t, over, 0)
		assertCode(t, code, b, 507, "storage_limit")
		if l.count(t, "receipts") != 8192 || l.count(t, "entries") != 2 {
			t.Fatal("receipt limit mutated data")
		}
		if got := mustAccept(t, l, final, 1, "unchanged"); !bytes.Equal(response, got) {
			t.Fatal("replay changed")
		}
		l.checkBlob(t, "P", g)
		l.checkBlob(t, "E", g)
		t.Log("receipts=8191 -> 8192 -> 507; stored replay returns unchanged/1")
	})
}

// Seed known test patterns directly to avoid thousands of HTTP handshakes.
// Open rehashes every object; the requests at the boundary use real HTTP.
func seedBlobPatterns(t *testing.T, l *lab, lengths []int) (first, last safefs.Digest) {
	t.Helper()
	root := filepath.Join(l.dir, "hub", "blobs")
	data := make([]byte, safefs.MaxSize)
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		stmt, err := tx.Prepare("INSERT INTO blobs VALUES (?,?,?)")
		if err != nil {
			return err
		}
		defer stmt.Close()
		for i, length := range lengths {
			binary.BigEndian.PutUint32(data[:4], uint32(i+1))
			hash := fmt.Sprintf("%x", sha256.Sum256(data[:length]))
			name := filepath.Join(root, blobName(l.provision.Space, hash))
			f, err := os.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
			if err != nil {
				return err
			}
			_, err = f.Write(data[:length])
			if err != nil {
				f.Close()
				return err
			}
			if err := f.Close(); err != nil {
				return err
			}
			if _, err := stmt.Exec(l.provision.Space, hash, length); err != nil {
				return err
			}
			last = safefs.Digest{Hash: hash, Size: int64(length)}
			if i == 0 {
				first = last
			}
		}
		return nil
	}))
	l.reopen(t)
	return first, last
}

func TestP4_10_ActualBlobCount(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	lengths := make([]int, safefs.MaxManagedCopies-2)
	for i := range lengths {
		lengths[i] = 4
	}
	seedBlobPatterns(t, l, lengths)
	if l.count(t, "blobs") != 4095 {
		t.Fatal("bad 4095 fixture")
	}
	l.putSample(t, g, "Q")
	r, rd := sample(t, "R")
	status, b := l.raw(t, "PUT", "/v1/blobs/"+rd.Hash, 0, g, protocol.Binary, r)
	assertCode(t, status, b, 507, "storage_limit")
	if l.count(t, "blobs") != 4096 || l.count(t, "receipts") != 1 {
		t.Fatal("blob count overflow changed metadata")
	}
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
	l.reopen(t)
	if l.count(t, "blobs") != 4096 {
		t.Fatal("blob count changed at reopen")
	}
	replay := l.history(t)[0].Change
	mustAccept(t, l, replay, 1, "applied")
	t.Log("blob count=4095 -> 4096 -> 507; GET and recorded POST replay still available")
}

func TestP4_10_ActualBlobBytes(t *testing.T) {
	if os.Getenv("FILE_SYNC_TEST_CAPACITY") != "1" {
		t.Skip("set FILE_SYNC_TEST_CAPACITY=1 for dense fixed-budget fixtures")
	}
	for _, delta := range []int{-1, 0, 1} {
		t.Run(fmt.Sprintf("remaining%+d", delta), func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			body, d := sample(t, "M")
			_, p := sample(t, "P")
			var stats unix.Statfs_t
			must(t, unix.Statfs(l.dir, &stats))
			free := stats.Bavail * uint64(stats.Bsize)
			if free < 3<<30 {
				t.Fatalf("fixed-volume test requires 3 GiB free; available %d bytes", free)
			}
			root := filepath.Join(l.dir, "hub", "blobs")
			target := safefs.MaxManagedBytes - int(p.Size) - int(d.Size) - delta
			lengths := make([]int, 0)
			for target != 0 {
				n := min(target, safefs.MaxSize)
				if n < 4 {
					t.Fatal("fixture remainder too small")
				}
				lengths = append(lengths, n)
				target -= n
			}
			first, last := seedBlobPatterns(t, l, lengths)
			for i, v := range []safefs.Digest{first, last} {
				data, err := l.req(t, "A", 0).Remote.Get(t.Context(), g, v)
				must(t, err)
				want := uint32(1)
				if i == 1 {
					want = uint32(len(lengths))
				}
				if binary.BigEndian.Uint32(data[:4]) != want || !bytes.Equal(data[4:], make([]byte, len(data)-4)) {
					t.Fatal("old pattern changed")
				}
			}
			before := l.h.blobBytes
			if safefs.MaxManagedBytes-before != d.Size+int64(delta) {
				t.Fatalf("incorrect boundary: %d", before)
			}
			status, b := l.raw(t, "PUT", "/v1/blobs/"+d.Hash, 0, g, protocol.Binary, body)
			if delta < 0 {
				assertCode(t, status, b, 507, "storage_limit")
			} else {
				if status != 201 {
					t.Fatalf("HTTP %d %s", status, b)
				}
				l.checkBlob(t, "M", g)
			}
			var physical int64
			entries, err := os.ReadDir(root)
			must(t, err)
			for _, e := range entries {
				var stat unix.Stat_t
				must(t, unix.Stat(filepath.Join(root, e.Name()), &stat))
				physical += stat.Blocks * 512
			}
			logical := l.h.blobBytes
			metadata, err := l.h.manager.MetadataSize()
			must(t, err)
			if logical+metadata > 2<<30 {
				t.Fatal("hub logical storage exceeds 2 GiB")
			}
			l.reopen(t)
			if l.h.blobBytes != logical {
				t.Fatal("reopen changed accounted blob bytes")
			}
			l.checkBlob(t, "P", g)
			mustAccept(t, l, l.history(t)[0].Change, 1, "applied")
			t.Logf("capacity bytes: free_before=%d reserved_candidate=%d before=%d logical=%d physical=%d metadata=%d hub_total=%d status=%d", free, d.Size, before, logical, physical, metadata, logical+metadata, status)
			if delta == 0 {
				change, receipt := largeReceipt(t, l)
				fillWideReceipts(t, l, change, receipt)
				full, err := measureHub(filepath.Join(l.dir, "hub"), 0)
				must(t, err)
				if full.Objects != safefs.MaxManagedBytes || full.Database < 30<<20 {
					t.Fatalf("both fixed budgets were not exercised: %+v", full)
				}
				l.hubFile(t, notes, "P", "client-a", 1)
				l.checkBlob(t, "M", g)
			}
		})
	}
}

func TestP4_13_ConcurrentLastBlobSlot(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	lengths := make([]int, safefs.MaxManagedCopies-2)
	for i := range lengths {
		lengths[i] = 4
	}
	seedBlobPatterns(t, l, lengths)
	q, qd := sample(t, "Q")
	r, rd := sample(t, "R")
	written, waiting := make(chan struct{}), make(chan struct{})
	var once sync.Once
	var requests atomic.Int32
	withFault(t, l, func(at string) error {
		switch at {
		case "before-upload-lock":
			if requests.Add(1) == 2 {
				close(waiting)
			}
		case "blob-bytes-written":
			once.Do(func() { close(written) })
		}
		return nil
	})
	conn := rawUploadStart(t, l.server.Listener.Addr().String(), g, qd.Hash, l.provision.Clients[0].Token, len(q), q[:2])
	awaitBoundary(t, written)
	other, err := protocol.NewClient(l.server.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	result := make(chan error, 1)
	go func() { result <- other.Put(t.Context(), g, rd, r) }()
	awaitBoundary(t, waiting)
	l.h.mu.Lock()
	reserved := l.h.blobCount
	l.h.mu.Unlock()
	if reserved != 4096 {
		t.Fatalf("reserved slots %d", reserved)
	}
	m, err := measureHub(filepath.Join(l.dir, "hub"), qd.Size)
	must(t, err)
	if m.Temporary != 2 || m.Reservations != qd.Size || m.Objects != 3+4*int64(len(lengths)) {
		t.Fatalf("bad in-flight reservation/accounting: %+v", m)
	}
	t.Logf("in-flight PUT logical sizes: %+v", m)
	select {
	case err := <-result:
		t.Fatalf("second PUT did not wait: %v", err)
	default:
	}
	must(t, conn.Close())
	must(t, <-result)
	if l.count(t, "blobs") != 4096 || l.count(t, "receipts") != 1 {
		t.Fatal("concurrent PUT duplicated capacity")
	}
	qpath := filepath.Join(l.dir, "hub", "blobs", blobName(l.provision.Space, qd.Hash))
	assertDiskBytes(t, qpath+".part", nil)
	assertDiskBytes(t, qpath, nil)
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "R", g)
	l.hubFile(t, notes, "P", "client-a", 1)
	l.assertBody(t, "A/"+notes, "Q")
	l.assertBody(t, "B/"+notes, "R")
}
