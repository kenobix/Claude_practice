package hub

import (
	"bufio"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"golang.org/x/sys/unix"
)

func withFault(t *testing.T, l *lab, f func(string) error) {
	t.Helper()
	l.server.Close()
	l.h.fault = f
	l.server = httptest.NewServer(l.h)
}

func awaitBoundary(t *testing.T, ch <-chan struct{}) {
	t.Helper()
	select {
	case <-ch:
	case <-time.After(15 * time.Second):
		t.Fatal("event not reached")
	}
}

func TestP4_07_InjectedIOFailures(t *testing.T) {
	t.Run("interrupted-upload-then-ENOSPC", func(t *testing.T) {
		l := newLab(t)
		g := l.seed(t)
		l.write(t, "A", notes, "Q")
		q, qd := sample(t, "Q")
		_, pd := sample(t, "P")
		written := make(chan struct{})
		var once sync.Once
		var full atomic.Bool
		withFault(t, l, func(at string) error {
			switch at {
			case "blob-bytes-written":
				once.Do(func() { close(written) })
			case "before-blob-create":
				if full.Load() {
					return unix.ENOSPC
				}
			}
			return nil
		})
		path := "/v1/blobs/" + qd.Hash
		conn := rawUploadStart(t, l.server.Listener.Addr().String(), g, qd.Hash, l.provision.Clients[0].Token, len(q), q[:2])
		awaitBoundary(t, written)
		must(t, conn.Close())
		full.Store(true)
		status, b := l.raw(t, "PUT", path, 0, g, protocol.Binary, q)
		assertCode(t, status, b, 507, "storage_full")
		probeHub(t, l.dir, "hub", g, 1, pd.Hash, 1, 1)
		assertDiskBytes(t, filepath.Join(l.dir, "hub", "blobs", blobName(l.provision.Space, qd.Hash)+".part"), nil)
		l.assertBody(t, "A/"+notes, "Q")
		full.Store(false)
		status, b = l.raw(t, "PUT", path, 0, g, protocol.Binary, q)
		if status != 201 {
			t.Fatalf("retry HTTP %d %s", status, b)
		}
		l.checkBlob(t, "Q", g)
	})
	for _, point := range []string{"before-change-commit", "change-committed"} {
		t.Run(point+"-then-reopen-EIO", func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			q, qd := sample(t, "Q")
			_, pd := sample(t, "P")
			must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, qd, q))
			l.write(t, "A", notes, "Q")
			withFault(t, l, func(at string) error {
				if at == point {
					return unix.EIO
				}
				return nil
			})
			c := l.change(t, strings.Repeat("a", 32), notes, "Q", 1)
			fresh := l.change(t, strings.Repeat("b", 32), "fresh", "P", 0)
			status, b := l.post(t, c, 0)
			assertCode(t, status, b, 503, "storage_unavailable")
			status, b = l.post(t, fresh, 0)
			assertCode(t, status, b, 503, "storage_unavailable")
			l.stop()
			h, err := open(t.Context(), l.dir, "hub", nil, func(at string) error {
				if at == "before-database-open" {
					return unix.EIO
				}
				return nil
			})
			if !errors.Is(err, unix.EIO) || h != nil {
				t.Fatalf("reopen after injected EIO: %v", err)
			}
			if point == "before-change-commit" {
				probeHub(t, l.dir, "hub", g, 1, pd.Hash, 2, 1)
			} else {
				probeHub(t, l.dir, "hub", g, 2, qd.Hash, 2, 2)
			}
			l.start(t)
			status, b = l.post(t, c, 0)
			if status != 200 {
				t.Fatalf("recovery %d %s", status, b)
			}
			probeHub(t, l.dir, "hub", g, 2, qd.Hash, 2, 2)
			l.assertBody(t, "A/"+notes, "Q")
			l.checkBlob(t, "P", g)
			l.checkBlob(t, "Q", g)
		})
	}
	for _, point := range []string{"before-blob-sync", "before-blob-directory-sync"} {
		t.Run(point, func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			q, qd := sample(t, "Q")
			_, pd := sample(t, "P")
			withFault(t, l, func(at string) error {
				if at == point {
					return unix.EIO
				}
				return nil
			})
			c := l.change(t, strings.Repeat("a", 32), notes, "Q", 1)
			l.write(t, "A", notes, "Q")
			status, b := l.raw(t, "PUT", "/v1/blobs/"+qd.Hash, 0, g, protocol.Binary, q)
			assertCode(t, status, b, 503, "storage_unavailable")
			status, b = l.post(t, c, 0)
			assertCode(t, status, b, 503, "storage_unavailable")
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, 1, 1)
			l.reopen(t)
			expected := 201
			if point == "before-blob-directory-sync" {
				expected = 200
			}
			status, b = l.raw(t, "PUT", "/v1/blobs/"+qd.Hash, 0, g, protocol.Binary, q)
			if status != expected {
				t.Fatalf("recovery HTTP %d %s", status, b)
			}
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, 2, 1)
			l.checkBlob(t, "P", g)
			l.checkBlob(t, "Q", g)
		})
	}
}

func TestP4_08_BodyAndCorruption(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	p, pd := sample(t, "P")
	q, qd := sample(t, "Q")
	status, b := l.raw(t, "PUT", "/v1/blobs/"+pd.Hash, 0, g, protocol.Binary, q)
	assertCode(t, status, b, 422, "hash_mismatch")
	conn := rawUploadStart(t, l.server.Listener.Addr().String(), g, qd.Hash, l.provision.Clients[0].Token, len(q), q[:2])
	must(t, conn.(*net.TCPConn).CloseWrite())
	r, err := http.ReadResponse(bufio.NewReader(conn), nil)
	must(t, err)
	b, err = io.ReadAll(r.Body)
	must(t, err)
	r.Body.Close()
	assertCode(t, r.StatusCode, b, 422, "length_mismatch")
	status, b = l.post(t, l.change(t, strings.Repeat("a", 32), notes, "Q", 1), 0)
	assertCode(t, status, b, 422, "blob_missing")
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		actual := httptest.NewRecorder()
		l.h.ServeHTTP(actual, r)
		if actual.Code != 200 || actual.Body.String() != "abc" {
			t.Errorf("unexpected upstream GET: %d %s", actual.Code, actual.Body.String())
			return
		}
		w.Header().Set("Content-Type", protocol.Binary)
		w.Header().Set("Content-Length", "3")
		w.WriteHeader(200)
		_, _ = io.WriteString(w, "ab")
	}))
	remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	body, err := remote.Get(t.Context(), g, pd)
	proxy.Close()
	if err == nil || len(body) != 0 {
		t.Fatalf("truncated GET published %q: %v", body, err)
	}
	probeHub(t, l.dir, "hub", g, 1, pd.Hash, 1, 1)
	// Independent corruption of a registered file. The wrong bytes must be kept.
	l.write(t, "hub", filepath.Join("blobs", blobName(l.provision.Space, pd.Hash)), "R")
	status, b = l.raw(t, "GET", "/v1/blobs/"+pd.Hash, 0, g, "", nil)
	assertCode(t, status, b, 503, "storage_unavailable")
	probeHub(t, l.dir, "hub", g, 1, pd.Hash, 1, 1)
	l.stop()
	if h, err := Open(t.Context(), l.dir, "hub"); err == nil {
		h.Close()
		t.Fatal("corrupt object opened")
	}
	l.assertBody(t, filepath.Join("hub", "blobs", blobName(l.provision.Space, pd.Hash)), "R")
	assertDiskBytes(t, filepath.Join(l.dir, "A", notes), p)
	assertDiskBytes(t, filepath.Join(l.dir, "B", notes), p)
	// The regression TestReceiveIncompleteResponseKeepsBaseline exercises a
	// truncated GET through the production receiving path with a valid Q row.
}

func TestP4_09_CombinedRequestOrder(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	history := l.history(t)
	original := history[0].Change
	_, pd := sample(t, "P")
	badGen := strings.Repeat("e", 32)
	third, _ := protocol.TokenDigest(token(3))
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		if _, err := tx.Exec("INSERT INTO spaces VALUES (?,?)", "foreign", g); err != nil {
			return err
		}
		if _, err := tx.Exec("INSERT INTO clients VALUES ('foreign','client-b',?)", third[:]); err != nil {
			return err
		}
		_, err := tx.Exec(`
WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM counter WHERE n<?)
INSERT INTO receipts SELECT ?,?,'client-b',printf('%032x',n),'{}','{}' FROM counter`, protocol.MaxReceipts-1, l.provision.Space, g)
		return err
	}))
	l.reopen(t)
	test := func(c protocol.Change, client, status int, code string) {
		t.Helper()
		actual, b := l.post(t, c, client)
		assertCode(t, actual, b, status, code)
		if l.count(t, "entries") != 1 || l.count(t, "receipts") != protocol.MaxReceipts {
			t.Fatal("rejection changed row counts")
		}
		l.hubFile(t, notes, "P", "client-a", 1)
	}
	c := original
	c.Generation = badGen
	c.Base = 9
	test(c, -1, 401, "unauthorized")
	test(c, 1, 403, "client_mismatch")
	test(c, 0, 409, "generation_mismatch")
	c.Generation = g
	test(c, 0, 409, "op_id_reused")
	c.Operation = strings.Repeat("c", 32)
	test(c, 0, 409, "revision_mismatch")
	l.provision.Clients[1].Token = token(3)
	status, b := l.raw(t, "GET", "/v1/blobs/"+pd.Hash, 1, g, "", nil)
	assertCode(t, status, b, 404, "blob_not_found")
	status, b = l.raw(t, "GET", "/v1/manifest", 1, "", "", nil)
	var foreign protocol.Manifest
	must(t, json.Unmarshal(b, &foreign))
	if status != 200 || foreign.Space != "foreign" || len(foreign.Entries) != 0 {
		t.Fatalf("leaked foreign scope: %d %s", status, b)
	}
	l.file(t, "A", notes, "P", 1)
	l.file(t, "B", notes, "P", 1)
}

func TestP4_13_ConcurrentCAS(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	var posts atomic.Int32
	reached, release := make(chan struct{}), make(chan struct{})
	var unblock sync.Once

	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			i := posts.Add(1)
			if i == 2 {
				close(reached)
			}
			if i <= 2 {
				select {
				case <-release:
				case <-r.Context().Done():
					return
				}
			}
		}
		l.h.ServeHTTP(w, r)
	}))
	defer proxy.Close()
	defer unblock.Do(func() { close(release) })
	type result struct {
		root string
		err  error
	}
	done := make(chan result, 2)
	for i, root := range []string{"A", "B"} {
		remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[i].Token)
		must(t, err)
		go func() {
			done <- result{root, local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: root, Remote: remote})}
		}()
	}
	awaitBoundary(t, reached)
	unblock.Do(func() { close(release) })
	first, second := <-done, <-done
	if first.err != nil {
		first, second = second, first
	}
	if first.err != nil || !errors.Is(second.err, local.ErrConflict) {
		t.Fatalf("concurrent results %+v %+v", first, second)
	}
	win, lose, client := "Q", "R", "client-a"
	if first.root == "B" {
		win, lose, client = "R", "Q", "client-b"
	}
	l.hubFile(t, notes, win, client, 2)
	l.file(t, first.root, notes, win, 2)
	l.assertBase(t, second.root, notes, "P", 1)
	l.assertBody(t, second.root+"/"+notes, lose)
	s := l.status(t, second.root)
	if len(s.Conflicts) != 1 || !s.Conflicts[0].Saved || len(l.history(t)) != 2 {
		t.Fatalf("loser did not retain conflict %+v", s)
	}
	l.assertCandidate(t, second.root, s.Conflicts[0].Local, lose)
	l.assertCandidate(t, second.root, s.Conflicts[0].Hub[0], win)
	for _, id := range []string{"P", "Q", "R"} {
		l.checkBlob(t, id, g)
	}
}

func TestP4_14_HTTPBoundaries(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	_, pd := sample(t, "P")
	for _, id := range []string{"X", "M", "N"} {
		t.Run(id, func(t *testing.T) {
			body, want := sample(t, id)
			if protocol.Digest(body) != want {
				t.Fatal("fixture digest changed")
			}
			status, b := l.raw(t, "PUT", "/v1/blobs/"+want.Hash, 0, g, protocol.Binary, body)
			if id == "N" {
				assertCode(t, status, b, 413, "body_too_large")
			} else {
				if status != 201 {
					t.Fatalf("HTTP %d %s", status, b)
				}
				l.checkBlob(t, id, g)
			}
		})
	}
	valid, err := json.Marshal(l.change(t, strings.Repeat("a", 32), notes, "P", 1))
	must(t, err)
	for _, tc := range []struct {
		name, body, code string
		status           int
	}{
		{"duplicate", `{"path":"other",` + string(valid[1:]), "invalid_request", 400},
		{"surrogate", strings.Replace(string(valid), notes, `\ud800`, 1), "invalid_request", 400},
		{"oversize", string(valid) + strings.Repeat(" ", protocol.MaxJSON+1-len(valid)), "body_too_large", 413},
	} {
		t.Run(tc.name, func(t *testing.T) {
			status, b := l.raw(t, "POST", "/v1/changes", 0, "", protocol.JSON, []byte(tc.body))
			assertCode(t, status, b, tc.status, tc.code)
			probeHub(t, l.dir, "hub", g, 1, pd.Hash, 3, 1)
		})
	}
}
