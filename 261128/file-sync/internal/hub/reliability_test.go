package hub

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestAPIValidationAndIsolation(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	_, d := sample(t, "P")
	hashPath := "/v1/blobs/" + d.Hash
	badGen := strings.Repeat("f", 32)
	good := l.change(t, strings.Repeat("1", 32), notes, "P", 1)
	data, err := json.Marshal(good)
	must(t, err)
	text := string(data)
	for _, tc := range []struct {
		name, method, path      string
		client                  int
		generation, media, body string
		status                  int
		code                    string
	}{
		{"route", "GET", "/v2/manifest", 0, "", "", "", 404, "not_found"},
		{"method", "POST", "/v1/manifest", 0, "", "", "", 405, "method_not_allowed"},
		{"manifest_auth", "GET", "/v1/manifest", -1, "", "", "", 401, "unauthorized"},
		{"manifest_query", "GET", "/v1/manifest?x=y", 0, "", "", "", 400, "invalid_request"},
		{"manifest_body", "GET", "/v1/manifest", 0, "", "", "x", 400, "invalid_request"},
		{"blob_auth", "GET", hashPath, -1, "", "", "", 401, "unauthorized"},
		{"generation_missing", "GET", hashPath, 0, "", "", "", 400, "invalid_request"},
		{"generation_order", "GET", "/v1/blobs/bad", 0, badGen, "", "", 409, "generation_mismatch"},
		{"escaped_hash", "GET", "/v1/blobs/%62" + d.Hash[1:], 0, g, "", "", 400, "invalid_request"},
		{"missing_blob", "GET", "/v1/blobs/" + strings.Repeat("0", 64), 0, g, "", "", 404, "blob_not_found"},
		{"put_hash", "PUT", hashPath, 0, g, protocol.Binary, "xyz", 422, "hash_mismatch"},
		{"new_blob_hash", "PUT", "/v1/blobs/" + strings.Repeat("0", 64), 0, g, protocol.Binary, "xyz", 422, "hash_mismatch"},
		{"put_media", "PUT", hashPath, 0, g, protocol.JSON, "abc", 415, "unsupported_media_type"},
		{"put_oversize", "PUT", hashPath, 0, badGen, protocol.Binary, strings.Repeat("x", safefs.MaxSize+1), 413, "body_too_large"},
		{"post_unauth", "POST", "/v1/changes", -1, "", protocol.JSON, "{", 401, "unauthorized"},
		{"post_media", "POST", "/v1/changes", 0, "", protocol.Binary, text, 415, "unsupported_media_type"},
		{"post_identity", "POST", "/v1/changes", 1, "", protocol.JSON, text, 403, "client_mismatch"},
		{"post_gen", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, g, badGen, 1), 409, "generation_mismatch"},
		{"duplicate", "POST", "/v1/changes", 0, "", protocol.JSON, text[:len(text)-1] + `,"\u0073ize":3}`, 400, "invalid_request"},
		{"case_key", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, `"path"`, `"Path"`, 1), 400, "invalid_request"},
		{"unknown", "POST", "/v1/changes", 0, "", protocol.JSON, text[:len(text)-1] + `,"space_id":"elsewhere"}`, 400, "invalid_request"},
		{"bad_utf8", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, notes, "\xff", 1), 400, "invalid_request"},
		{"unpaired_surrogate", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, notes, `\ud83d`, 1), 400, "invalid_request"},
		{"integer", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, `"base_revision":1`, `"base_revision":1e0`, 1), 400, "invalid_request"},
		{"null_path", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, `"notes.txt"`, `null`, 1), 400, "invalid_request"},
		{"more_json", "POST", "/v1/changes", 0, "", protocol.JSON, text + "{}", 400, "invalid_request"},
		{"traversal", "POST", "/v1/changes", 0, "", protocol.JSON, strings.Replace(text, notes, "a/../b", 1), 400, "invalid_request"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			status, body := l.raw(t, tc.method, tc.path, tc.client, tc.generation, tc.media, []byte(tc.body))
			assertCode(t, status, body, tc.status, tc.code)
		})
	}
	t.Run("framing", func(t *testing.T) {
		for _, tc := range []struct {
			name   string
			length int64
			header bool
			raw    string
			code   string
			status int
		}{
			{"no_length", 0, false, "", "length_required", 411},
			{"short", 5, true, "abc", "length_mismatch", 422},
		} {
			t.Run(tc.name, func(t *testing.T) {
				r := httptest.NewRequest(http.MethodPut, hashPath, strings.NewReader(tc.raw))
				r.ContentLength = tc.length
				if tc.header {
					r.Header.Set("Content-Length", fmt.Sprint(tc.length))
				}
				r.Header.Set(protocol.GenerationHeader, g)
				r.Header.Set("Content-Type", protocol.Binary)
				r.Header.Set("Authorization", "Bearer "+l.provision.Clients[0].Token)
				w := httptest.NewRecorder()
				l.h.ServeHTTP(w, r)
				assertCode(t, w.Code, w.Body.Bytes(), tc.status, tc.code)
			})
		}
	})
	t.Run("candidate_order", func(t *testing.T) {
		tests := []struct {
			c      protocol.Change
			code   string
			status int
		}{
			{l.change(t, good.Operation, "unknown.txt", "Q", 1), "revision_mismatch", 409},
			{l.change(t, good.Operation, "unknown.txt", "Q", 0), "blob_missing", 422},
			{l.change(t, good.Operation, notes+"/child", "Q", 0), "namespace_conflict", 409},
			{l.change(t, good.Operation, notes, "P", 1), "blob_size_mismatch", 422},
		}
		tests[3].c.Size++
		for _, test := range tests {
			status, body := l.post(t, test.c, 0)
			assertCode(t, status, body, test.status, test.code)
			if test.code == "revision_mismatch" && !bytes.Contains(body, []byte(`"current":null`)) {
				t.Fatalf("absent path response %s", body)
			}
			if test.code == "namespace_conflict" && !bytes.Contains(body, []byte(`"conflicts":["notes.txt"]`)) {
				t.Fatalf("namespace response %s", body)
			}
		}
		for _, tc := range []struct {
			path, code string
			base       int64
		}{{notes, "delete_unsupported", 1}, {"absent.txt", "unknown_delete", 0}} {
			c := good
			c.Path, c.Base, c.Deleted, c.Hash, c.Size = tc.path, tc.base, true, nil, 0
			status, body := l.post(t, c, 0)
			assertCode(t, status, body, 422, tc.code)
		}
	})
	t.Run("another_space", func(t *testing.T) {
		c, _ := protocol.TokenDigest(token(3))
		must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
			if _, err := tx.Exec("INSERT INTO spaces VALUES (?,?)", "elsewhere", g); err != nil {
				return err
			}
			// Same IDs and generation expose any missing space restriction.
			_, err := tx.Exec("INSERT INTO clients VALUES (?,?,?)", "elsewhere", "client-a", c[:])
			return err
		}))
		l.provision.Clients[1].Token = token(3)
		l.reopen(t)
		status, body := l.raw(t, "GET", "/v1/manifest", 1, "", "", nil)
		if status != 200 || !bytes.Contains(body, []byte(`"entries":[]`)) {
			t.Fatalf("foreign manifest HTTP %d %s", status, body)
		}
		status, body = l.raw(t, "GET", hashPath, 1, g, "", nil)
		assertCode(t, status, body, 404, "blob_not_found")
		saved := l.history(t)[0]
		status, body = l.post(t, saved.Change, 1)
		assertCode(t, status, body, 422, "blob_missing")
	})
	l.hubFile(t, notes, "P", "client-a", 1)
	if l.count(t, "blobs") != 1 || len(l.history(t)) != 1 {
		t.Fatal("rejected request modified state or retained bad upload")
	}
}

func TestCommitBeforeConcurrentReplay(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	body, d := sample(t, "Q")
	must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, d, body))
	change := l.change(t, strings.Repeat("1", 32), notes, "Q", 1)
	typedBody, err := json.Marshal(change)
	must(t, err)

	entered, unblock := make(chan struct{}), make(chan struct{})
	defer func() {
		if unblock != nil {
			close(unblock)
		}
	}()
	gate := unblock
	l.h.mu.Lock()
	l.h.beforeChangeCommit = func() { close(entered); <-gate; l.h.beforeChangeCommit = nil }
	l.h.mu.Unlock()
	type response struct {
		status int
		body   []byte
		err    error
	}
	post := func() response {
		req, err := http.NewRequestWithContext(t.Context(), http.MethodPost, l.server.URL+"/v1/changes", bytes.NewReader(typedBody))
		if err != nil {
			return response{err: err}
		}
		req.Header.Set("Content-Type", protocol.JSON)
		req.Header.Set("Authorization", "Bearer "+l.provision.Clients[0].Token)
		resp, err := l.server.Client().Do(req)
		if err != nil {
			return response{err: err}
		}
		defer resp.Body.Close()
		b, err := io.ReadAll(resp.Body)
		return response{status: resp.StatusCode, body: b, err: err}
	}
	const callers = 8
	results := make(chan response, callers)
	var wg sync.WaitGroup
	for i := 0; i < callers; i++ {
		wg.Go(func() { results <- post() })
	}
	select {
	case <-entered:
	case <-time.After(15 * time.Second):
		t.Fatal("commit boundary not reached")
	}
	select {
	case got := <-results:
		t.Fatalf("response came before commit: %+v", got)
	default:
	}
	close(unblock)
	unblock = nil
	wg.Wait()
	close(results)
	var first []byte
	for got := range results {
		must(t, got.err)
		if first == nil {
			first = got.body
		}
		if got.status != 200 || !bytes.Equal(first, got.body) {
			t.Fatalf("concurrent replay HTTP %d %s", got.status, got.body)
		}
	}
	h := l.history(t)
	if len(h) != 2 || h[1].Change.Operation != change.Operation || h[1].Receipt.Entry.Revision != 2 {
		t.Fatalf("concurrent requests applied twice: %+v", h)
	}
	r, rd := sample(t, "R")
	must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, rd, r))
	latest := l.change(t, strings.Repeat("2", 32), notes, "R", 2)
	status, b := l.post(t, latest, 0)
	if status != 200 {
		t.Fatalf("advance HTTP %d %s", status, b)
	}
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		_, err := tx.Exec(`
WITH RECURSIVE counter(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM counter WHERE n<?)
INSERT INTO receipts SELECT ?,?,'client-b',printf('%032x',n),'{}','{}' FROM counter`, protocol.MaxReceipts-3, l.provision.Space, g)
		return err
	}))
	if l.count(t, "receipts") != protocol.MaxReceipts {
		t.Fatal("receipt limit fixture incorrect")
	}
	status, b = l.post(t, change, 0)
	if status != 200 || !bytes.Equal(first, b) {
		t.Fatalf("full replay HTTP %d %s", status, b)
	}
	status, b = l.post(t, l.change(t, strings.Repeat("3", 32), notes, "Q", 3), 0)
	assertCode(t, status, b, 507, "storage_limit")
	status, b = l.post(t, l.change(t, strings.Repeat("3", 32), notes, "Q", 2), 0)
	assertCode(t, status, b, 409, "revision_mismatch")
	change.Generation = strings.Repeat("e", 32)
	status, b = l.post(t, change, 0)
	assertCode(t, status, b, 409, "generation_mismatch")
	l.hubFile(t, notes, "R", "client-a", 3)
}

func TestPutRecoveryAndIntegrity(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	q, qd := sample(t, "Q")
	_, rd := sample(t, "R")
	root := filepath.Join(l.dir, "hub", "blobs")
	qName := filepath.Join(root, blobName(l.provision.Space, qd.Hash))
	rName := filepath.Join(root, blobName(l.provision.Space, rd.Hash)+".part")
	l.stop()
	must(t, os.WriteFile(qName, q, 0600))
	must(t, os.WriteFile(rName, []byte("wo"), 0600))
	l.start(t)
	l.checkBlob(t, "Q", g)
	if l.count(t, "blobs") != 2 {
		t.Fatal("verified orphan was not registered")
	}
	if _, err := os.Stat(rName); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("incomplete PUT was not recovered")
	}
	status, b := l.raw(t, "GET", "/v1/blobs/"+rd.Hash, 0, g, "", nil)
	assertCode(t, status, b, 404, "blob_not_found")
	must(t, os.WriteFile(qName, []byte("bad!!"), 0600))
	status, b = l.raw(t, "PUT", "/v1/blobs/"+qd.Hash, 0, g, protocol.Binary, q)
	assertCode(t, status, b, 503, "storage_unavailable")
	status, b = l.raw(t, "GET", "/v1/blobs/"+qd.Hash, 0, g, "", nil)
	assertCode(t, status, b, 503, "storage_unavailable")
	l.stop()
	if h, err := Open(t.Context(), l.dir, "hub"); err == nil {
		h.Close()
		t.Fatal("corrupt registered blob accepted on restart")
	}
	b, err := os.ReadFile(qName)
	must(t, err)
	if string(b) != "bad!!" {
		t.Fatal("corrupt file was overwritten")
	}
	must(t, os.WriteFile(qName, q, 0600))
	l.start(t)
	l.stop()
	dbFile := filepath.Join(l.dir, "hub", safefs.Management, "state", safefs.StateDB)
	must(t, os.Rename(dbFile, dbFile+".saved"))
	if h, err := Open(t.Context(), l.dir, "hub"); err == nil {
		h.Close()
		t.Fatal("missing DB implicitly initialized")
	}
	if err := Init(t.Context(), l.dir, "hub", l.provision); err == nil {
		t.Fatal("existing hub implicitly reinitialized")
	}
	must(t, os.Rename(dbFile+".saved", dbFile))
	l.start(t)
	l.hubFile(t, notes, "P", "client-a", 1)
}

func TestPutBodyDoesNotOccupySQLite(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	q, d := sample(t, "Q")
	r, w := io.Pipe()
	defer w.Close()
	reading := make(chan struct{})
	result := make(chan error, 1)
	go func() {
		status, failure := l.h.putBlob(t.Context(), l.h.principals[0], d.Hash, d.Size, notifyingReader{r, reading})
		if failure != nil {
			result <- failure
		} else if status != 201 {
			result <- fmt.Errorf("PUT %d", status)
		} else {
			result <- nil
		}
	}()
	select {
	case <-reading:
	case <-time.After(5 * time.Second):
		t.Fatal("PUT did not start receiving")
	}
	seen := make(chan error, 1)
	go func() {
		l.h.mu.Lock()
		defer l.h.mu.Unlock()
		_, err := l.h.readManifest(t.Context(), l.h.principals[0])
		seen <- err
	}()
	select {
	case err := <-seen:
		must(t, err)
	case <-time.After(5 * time.Second):
		t.Fatal("PUT blocked metadata read")
	}
	if _, err := w.Write(q); err != nil {
		t.Fatal(err)
	}
	must(t, w.Close())
	must(t, <-result)
	l.checkBlob(t, "Q", g)
}

type notifyingReader struct {
	io.Reader
	notify chan struct{}
}

func (r notifyingReader) Read(b []byte) (int, error) {
	select {
	case <-r.notify:
	default:
		close(r.notify)
	}
	return r.Reader.Read(b)
}

func TestClientHoldsAndManualIsolation(t *testing.T) {
	l := newLab(t)
	l.seed(t)
	status := l.status(t, "B")
	req := local.Request{Volume: l.dir, Source: "A", Destination: "B", Revision: 90}
	if err := local.Copy(t.Context(), req); err == nil {
		t.Fatal("manual copy used a registered destination")
	}
	if err := local.Recover(t.Context(), req); err == nil {
		t.Fatal("manual recovery used a registered destination")
	}
	if err := local.Sync(t.Context(), l.req(t, "B", 0)); err == nil {
		t.Fatal("root accepted another registered token")
	}
	if _, err := local.Inspect(t.Context(), l.dir, "hub"); err == nil {
		t.Fatal("client migrated a hub database")
	}
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	if err := local.Sync(t.Context(), l.req(t, "B", 1)); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("simultaneous edit: %v", err)
	}
	got := l.status(t, "B")
	data, err := os.ReadFile(filepath.Join(l.dir, "B", notes))
	must(t, err)
	want, _ := sample(t, "R")
	if got.Bases[notes] != status.Bases[notes] || !bytes.Equal(data, want) || got.Holds[notes] != "both_changed" {
		t.Fatalf("conflicting data lost: %+v", got)
	}
	l.hubFile(t, notes, "Q", "client-a", 2)
	must(t, os.Remove(filepath.Join(l.dir, "A", notes)))
	if err := local.Sync(t.Context(), l.req(t, "A", 0)); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("local deletion: %v", err)
	}
	if l.status(t, "A").Holds[notes] != "local_delete_unsupported" || len(l.history(t)) != 2 {
		t.Fatal("deletion propagated")
	}
	for _, test := range []struct {
		root   string
		client int
	}{{"A", 0}, {"B", 1}} {
		name := filepath.Join(l.dir, test.root, safefs.Management, "state", safefs.StateDB)
		_, err := os.Stat(name)
		must(t, err)
	}
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		_, err := tx.Exec("UPDATE spaces SET generation=?", strings.Repeat("9", 32))
		return err
	}))
	l.reopen(t)
	if err := local.Sync(t.Context(), l.req(t, "B", 1)); !errors.Is(err, local.ErrConflict) || len(l.history(t)) != 2 {
		t.Fatalf("old generation participated: %v", err)
	}
}

func TestOutboxResponseLossAndNewLocalBody(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	// Drop both responses after the real handler has processed the request.
	// The client must keep one original ID and preserve the exact old bytes.
	dropped := make(chan []byte, 2)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			l.h.ServeHTTP(w, r)
			return
		}
		b, _ := io.ReadAll(r.Body)
		r.Body = io.NopCloser(bytes.NewReader(b))
		log := httptest.NewRecorder()
		l.h.ServeHTTP(log, r)
		dropped <- b
		conn, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			conn.Close()
		}
	}))
	defer proxy.Close()
	netw, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[0].Token)
	must(t, err)
	if err := local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "A", Remote: netw}); err == nil {
		t.Fatal("accepted an incomplete response")
	}
	if len(dropped) != 2 || !bytes.Equal(<-dropped, <-dropped) {
		t.Fatal("response loss did not replay identical typed request exactly once")
	}
	s := l.status(t, "A")
	if len(s.Outbox) != 1 || s.Outbox[0].Status != "ready" || s.Bases[notes].Revision != 1 {
		t.Fatalf("outbox after response loss: %+v", s)
	}
	l.hubFile(t, notes, "Q", "client-a", 2)
	l.write(t, "A", notes, "R")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.file(t, "A", notes, "R", 3)
	h := l.history(t)
	if len(h) != 3 || h[1].Change.Operation != s.Outbox[0].Change.Operation || h[2].Change.Operation == h[1].Change.Operation || h[2].Change.Base != 2 {
		t.Fatalf("new edit lost or old operation duplicated: %+v", h)
	}
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
	l.checkBlob(t, "R", g)
}

func TestLoopbackTransportRejectsRedirect(t *testing.T) {
	target := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		t.Error("redirect followed")
	}))
	defer target.Close()
	redirect := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Redirect(w, r, target.URL, http.StatusTemporaryRedirect)
	}))
	defer redirect.Close()
	for _, addr := range []string{"localhost:1234", "0.0.0.0:1234", "127.0.0.1:0", "http://127.0.0.1:1234", "[::1]:1234"} {
		if _, err := protocol.NewClient(addr, token(1)); err == nil {
			t.Fatalf("accepted address %q", addr)
		}
	}
	client, err := protocol.NewClient(redirect.Listener.Addr().String(), token(1))
	must(t, err)
	t.Setenv("HTTP_PROXY", target.URL)
	if _, err := client.Manifest(t.Context()); err == nil {
		t.Fatal("accepted redirect response")
	}
}

func TestReceiveIncompleteResponseKeepsBaseline(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	_, d := sample(t, "Q")
	attempts := make(chan bool, 3)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/blobs/"+d.Hash {
			l.h.ServeHTTP(w, r)
			return
		}
		attempts <- true
		w.Header().Set("Content-Length", "5")
		w.Header().Set("Content-Type", protocol.Binary)
		w.WriteHeader(200)
		_, _ = io.WriteString(w, "he")
	}))
	defer proxy.Close()
	remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	if err := local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "B", Remote: remote}); err == nil {
		t.Fatal("partial body applied")
	}
	if len(attempts) != 2 {
		t.Fatalf("download attempts %d", len(attempts))
	}
	l.file(t, "B", notes, "P", 1)
	s := l.status(t, "B")
	if len(s.Plans) != 2 || s.Plans[1].Status != "pending" && s.Plans[0].Status != "pending" {
		t.Fatalf("incomplete download lost plan: %+v", s.Plans)
	}
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	l.file(t, "B", notes, "Q", 2)
	l.clean(t, g)
}

func TestPUTFramingHeaderOverHTTP(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	_, d := sample(t, "P")
	conn, err := net.DialTimeout("tcp4", l.server.Listener.Addr().String(), 5*time.Second)
	must(t, err)
	defer conn.Close()
	must(t, conn.SetDeadline(time.Now().Add(5*time.Second)))
	_, err = fmt.Fprintf(conn, "PUT /v1/blobs/%s HTTP/1.1\r\nHost: 127.0.0.1\r\nAuthorization: Bearer %s\r\nX-Hub-Generation: %s\r\nContent-Type: application/octet-stream\r\nConnection: close\r\n\r\n", d.Hash, l.provision.Clients[0].Token, g)
	must(t, err)
	wire, err := io.ReadAll(conn)
	must(t, err)
	if !bytes.Contains(wire, []byte("411")) || !bytes.Contains(wire, []byte(`"error":"length_required"`)) {
		t.Fatalf("invalid unframed response %q", wire)
	}
}

func TestOutboxRefillsMissingBlobFromOriginalBody(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	post := make(chan []byte, 3)
	denied := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPut {
			writeProblem(w, problem(503, "storage_unavailable"))
			return
		}
		if r.Method == http.MethodPost {
			t.Error("POST preceded the initial successful PUT")
		}
		l.h.ServeHTTP(w, r)
	}))
	defer denied.Close()
	client, err := protocol.NewClient(denied.Listener.Addr().String(), l.provision.Clients[0].Token)
	must(t, err)
	if err := local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "A", Remote: client}); err == nil {
		t.Fatal("outgoing PUT unexpectedly succeeded")
	}
	s := l.status(t, "A")
	if len(s.Outbox) != 1 || s.Outbox[0].Status != "ready" || len(l.history(t)) != 1 {
		t.Fatalf("outbox %+v", s.Outbox)
	}
	l.write(t, "A", notes, "R")
	var methods []string
	var traceMu sync.Mutex
	trace := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			body, _ := io.ReadAll(r.Body)
			post <- body
			r.Body = io.NopCloser(bytes.NewReader(body))
		}
		if r.Method != http.MethodGet {
			traceMu.Lock()
			methods = append(methods, r.Method)
			traceMu.Unlock()
		}
		l.h.ServeHTTP(w, r)
	}))
	defer trace.Close()
	client, err = protocol.NewClient(trace.Listener.Addr().String(), l.provision.Clients[0].Token)
	must(t, err)
	must(t, local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "A", Remote: client}))
	trace.Close()
	traceMu.Lock()
	defer traceMu.Unlock()
	if strings.Join(methods, ",") != "POST,PUT,POST,PUT,POST" || len(post) != 3 || !bytes.Equal(<-post, <-post) {
		t.Fatalf("unexpected refill order %v", methods)
	}
	h := l.history(t)
	if len(h) != 3 || h[1].Change.Operation != s.Outbox[0].Change.Operation || h[1].Receipt.Entry.Revision != 2 ||
		h[2].Receipt.Entry.Revision != 3 || h[2].Change.Operation == h[1].Change.Operation {
		t.Fatalf("outbox history %+v", h)
	}
	l.file(t, "A", notes, "R", 3)
	l.checkBlob(t, "Q", g)
	l.checkBlob(t, "R", g)
}

func TestClientCASFailureHoldsBothBodies(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	q, d := sample(t, "Q")
	must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, d, q))
	first := l.change(t, strings.Repeat("5", 32), notes, "Q", 1)
	var once sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			once.Do(func() {
				for _, p := range l.h.principals {
					if p.client == "client-a" {
						_, err := l.h.accept(r.Context(), p, first)
						if err != nil {
							t.Errorf("concurrent update failed: %v", err)
						}
					}
				}
			})
		}
		l.h.ServeHTTP(w, r)
	}))
	defer server.Close()
	c, err := protocol.NewClient(server.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	err = local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "B", Remote: c})
	if !errors.Is(err, local.ErrConflict) {
		t.Fatalf("CAS conflict returned %v", err)
	}
	s := l.status(t, "B")
	if s.Holds[notes] != "both_changed" || s.Bases[notes].Revision != 1 || len(s.Outbox) != 1 ||
		s.Outbox[0].Status != "rejected" || len(l.history(t)) != 2 {
		t.Fatalf("CAS conflict changed state: %+v", s)
	}
	original, err := os.ReadFile(filepath.Join(l.dir, "B", notes))
	must(t, err)
	r, rd := sample(t, "R")
	if !bytes.Equal(original, r) || s.Outbox[0].Change.Hash == nil || *s.Outbox[0].Change.Hash != rd.Hash {
		t.Fatal("CAS rejection lost the local edit")
	}
	l.checkBlob(t, "R", g)
	l.hubFile(t, notes, "Q", "client-a", 2)
}
