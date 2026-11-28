package hub

import (
	"bufio"
	"bytes"
	"context"
	"database/sql"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestPhase4HubChild(t *testing.T) {
	point := os.Getenv("FILE_SYNC_P4_HUB_POINT")
	if point == "" {
		t.Skip("invoked by the process-stop tests")
	}
	volume := os.Getenv("FILE_SYNC_P4_HUB_VOLUME")
	notify := func(at string) {
		if at == point {
			fmt.Fprintln(os.Stdout, "stop", at)
			var b [1]byte
			_, err := io.ReadFull(os.Stdin, b[:])
			t.Fatalf("stop gate returned without SIGKILL: %v", err)
		}
	}
	switch mode := os.Getenv("FILE_SYNC_P4_HUB_MODE"); mode {
	case "backup":
		_, err := backup(t.Context(), volume, "hub", "stopped-backup", notify)
		t.Fatalf("backup returned without SIGKILL: %v", err)
	case "restore":
		_, err := restore(t.Context(), volume, "backup", "stopped-restore", testProvision(), notify)
		t.Fatalf("restore returned without SIGKILL: %v", err)
	case "serve":
		h, err := Open(t.Context(), volume, "hub")
		must(t, err)
		defer h.Close()
		listener, err := net.Listen("tcp4", "127.0.0.1:0")
		must(t, err)
		defer listener.Close()
		h.fault = func(at string) error { notify(at); return nil }
		fmt.Fprintln(os.Stdout, "listen", listener.Addr())
		t.Fatal(http.Serve(listener, h))
	default:
		t.Fatalf("unknown mode %q", mode)
	}
}

type stoppedHub struct {
	cmd    *exec.Cmd
	out    *bufio.Reader
	err    bytes.Buffer
	addr   string
	waited bool
}

func startStoppedHub(t *testing.T, l *lab, mode, point string) *stoppedHub {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), 30*time.Second)
	t.Cleanup(cancel)
	c := &stoppedHub{cmd: exec.CommandContext(ctx, os.Args[0], "-test.run=^TestPhase4HubChild$")}
	c.cmd.Env = append(os.Environ(), "FILE_SYNC_P4_HUB_POINT="+point, "FILE_SYNC_P4_HUB_MODE="+mode, "FILE_SYNC_P4_HUB_VOLUME="+l.dir)
	stdout, err := c.cmd.StdoutPipe()
	must(t, err)
	stdin, err := c.cmd.StdinPipe()
	must(t, err)
	c.cmd.Stderr = &c.err
	must(t, c.cmd.Start())
	t.Cleanup(func() {
		_ = stdin.Close()
		if !c.waited {
			_ = c.cmd.Process.Kill()
			_ = c.cmd.Wait()
		}
	})
	c.out = bufio.NewReader(stdout)
	if mode == "serve" {
		line, err := c.out.ReadString('\n')
		if err != nil || !strings.HasPrefix(line, "listen ") {
			t.Fatalf("hub child failed to listen: %q %v", line, err)
		}
		c.addr = strings.TrimSpace(strings.TrimPrefix(line, "listen "))
	}
	return c
}

func (c *stoppedHub) kill(t *testing.T, point string) {
	t.Helper()
	line, err := c.out.ReadString('\n')
	if err != nil || line != "stop "+point+"\n" {
		_ = c.cmd.Wait()
		c.waited = true
		t.Fatalf("expected %s, got %q: %v %s", point, line, err, c.err.String())
	}
	must(t, c.cmd.Process.Signal(syscall.SIGKILL))
	err = c.cmd.Wait()
	c.waited = true
	var exit *exec.ExitError
	if !errors.As(err, &exit) || exit.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
		t.Fatalf("expected SIGKILL: %v", err)
	}
}

func probeHub(t *testing.T, volume, root, generation string, revision int64, hash string, blobs, receipts int) {
	t.Helper()
	name := filepath.Join(volume, root, safefs.Management, "state", safefs.StateDB)
	db, err := sql.Open("sqlite3", "file:"+name+"?mode=ro&_foreign_keys=on")
	must(t, err)
	defer db.Close()
	var actualGeneration, actualHash, integrity string
	var actualRevision int64
	var actualBlobs, actualReceipts int
	must(t, db.QueryRow("SELECT generation FROM spaces").Scan(&actualGeneration))
	must(t, db.QueryRow("SELECT revision,hash FROM entries WHERE path=?", notes).Scan(&actualRevision, &actualHash))
	must(t, db.QueryRow("SELECT count(*) FROM blobs").Scan(&actualBlobs))
	must(t, db.QueryRow("SELECT count(*) FROM receipts").Scan(&actualReceipts))
	must(t, db.QueryRow("PRAGMA integrity_check").Scan(&integrity))
	rows, err := db.Query("PRAGMA foreign_key_check")
	must(t, err)
	defer rows.Close()
	if actualGeneration != generation || actualRevision != revision || actualHash != hash ||
		actualBlobs != blobs || actualReceipts != receipts || integrity != "ok" || rows.Next() {
		t.Fatalf("disk state generation=%s rev=%d hash=%s blobs=%d receipts=%d integrity=%s",
			actualGeneration, actualRevision, actualHash, actualBlobs, actualReceipts, integrity)
	}
	must(t, rows.Err())
}

func rawUploadStart(t *testing.T, addr, generation, hash, auth string, length int, prefix []byte) net.Conn {
	t.Helper()
	conn, err := net.DialTimeout("tcp4", addr, 10*time.Second)
	must(t, err)
	t.Cleanup(func() { conn.Close() })
	must(t, conn.SetDeadline(time.Now().Add(30*time.Second)))
	_, err = fmt.Fprintf(conn, "PUT /v1/blobs/%s HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer %s\r\nX-Hub-Generation: %s\r\nContent-Type: application/octet-stream\r\nContent-Length: %d\r\nConnection: close\r\n\r\n", hash, auth, generation, length)
	must(t, err)
	_, err = conn.Write(prefix)
	must(t, err)
	return conn
}

func assertDiskBytes(t *testing.T, path string, expected []byte) {
	t.Helper()
	data, err := os.ReadFile(path)
	if expected == nil {
		if !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("expected %s absent, read %d bytes: %v", path, len(data), err)
		}
		return
	}
	must(t, err)
	if !bytes.Equal(data, expected) {
		t.Fatalf("unexpected bytes at %s: got %d expected %d", path, len(data), len(expected))
	}
}

func TestP4_01_BlobSIGKILL(t *testing.T) {
	for _, tc := range []struct {
		name                       string
		partial, final, registered bool
	}{
		{"blob-bytes-written", true, false, false},
		{"blob-verified", true, false, false},
		{"before-blob-sync", true, false, false},
		{"blob-synced", true, false, false},
		{"before-blob-close", true, false, false},
		{"blob-closed", true, false, false},
		{"before-blob-rename", true, false, false},
		{"blob-renamed", false, true, false},
		{"before-blob-directory-sync", false, true, false},
		{"blob-directory-synced", false, true, false},
		{"before-blob-commit", false, true, false},
		{"blob-committed", false, true, true},
		{"before-blob-response", false, true, true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			p, pd := sample(t, "P")
			q, qd := sample(t, "Q")
			l.write(t, "A", notes, "Q")
			auth := l.provision.Clients[0].Token
			l.stop()
			child := startStoppedHub(t, l, "serve", tc.name)
			body := q
			if tc.name == "blob-bytes-written" {
				body = q[:2]
			}
			conn := rawUploadStart(t, child.addr, g, qd.Hash, auth, int(qd.Size), body)
			child.kill(t, tc.name)
			response, _ := io.ReadAll(conn)
			if len(response) != 0 {
				t.Fatalf("received success or error before stopped response: %q", response)
			}
			base := filepath.Join(l.dir, "hub", "blobs", blobName(l.provision.Space, qd.Hash))
			var part, complete []byte
			if tc.partial {
				part = body
			}
			if tc.final {
				complete = q
			}
			assertDiskBytes(t, base+".part", part)
			assertDiskBytes(t, base, complete)
			assertDiskBytes(t, filepath.Join(l.dir, "hub", "blobs", blobName(l.provision.Space, pd.Hash)), p)
			n := 1
			if tc.registered {
				n = 2
			}
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, n, 1)
			l.start(t)
			if tc.final {
				n = 2
			}
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, n, 1)
			assertDiskBytes(t, base+".part", nil)
			status, b := l.raw(t, "PUT", "/v1/blobs/"+qd.Hash, 0, g, protocol.Binary, q)
			want := 201
			if tc.final {
				want = 200
			}
			if status != want {
				t.Fatalf("re-PUT HTTP %d %s, expected %d", status, b, want)
			}
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, 2, 1)
			l.checkBlob(t, "Q", g)
			assertDiskBytes(t, filepath.Join(l.dir, "A", notes), q)
			l.assertBase(t, "A", notes, "P", 1)
			l.file(t, "B", notes, "P", 1)
			t.Logf("%s: stopped blob row=%t; re-PUT=%d, manifest=P/1 and history=1", tc.name, tc.registered, status)
		})
	}
}

func TestP4_02_ChangeSIGKILL(t *testing.T) {
	for _, point := range []string{"before-change-commit", "change-committed"} {
		t.Run(point, func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			q, qd := sample(t, "Q")
			_, pd := sample(t, "P")
			l.write(t, "A", notes, "Q")
			must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, qd, q))
			l.stop()
			child := startStoppedHub(t, l, "serve", point)
			remote, err := protocol.NewClient(child.addr, l.provision.Clients[0].Token)
			must(t, err)
			result := make(chan error, 1)
			go func() {
				result <- local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "A", Remote: remote})
			}()
			child.kill(t, point)
			if err := <-result; err == nil {
				t.Fatal("received response for killed POST")
			}
			s := l.status(t, "A")
			if len(s.Outbox) != 1 || s.Outbox[0].Status != "ready" || s.Outbox[0].Change.Hash == nil ||
				*s.Outbox[0].Change.Hash != qd.Hash || s.Bases[notes].Revision != 1 {
				t.Fatalf("interrupted client state %+v", s)
			}
			c := s.Outbox[0].Change
			l.assertBody(t, filepath.Join("A", safefs.Management, "stage", c.Operation+".outgoing"), "Q")

			if point == "before-change-commit" {
				probeHub(t, l.dir, "hub", g, 1, pd.Hash, 2, 1)
			} else {
				probeHub(t, l.dir, "hub", g, 2, qd.Hash, 2, 2)
			}
			l.start(t)
			must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
			l.file(t, "A", notes, "Q", 2)
			if len(l.status(t, "A").Outbox) != 0 {
				t.Fatal("confirmed operation remained active")
			}
			final := l.history(t)
			if len(final) != 2 || final[1].Change.Operation != c.Operation || final[1].Receipt.Entry.Revision != 2 {
				t.Fatalf("history %+v", final)
			}
			l.hubFile(t, notes, "Q", "client-a", 2)
			l.file(t, "B", notes, "P", 1)
			l.checkBlob(t, "P", g)
			assertDiskBytes(t, filepath.Join(l.dir, "A", notes), q)
			t.Logf("%s: Q/%s; a2 accepted exactly once after replay", point, qd.Hash)
		})
	}
}
