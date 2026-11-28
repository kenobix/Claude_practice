package hub

import (
	"bytes"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
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

const notes = "notes.txt"

type samples struct {
	Contents map[string]struct {
		Unit   string `json:"unit_hex"`
		Repeat int
		Size   int64
		Hash   string `json:"sha256"`
	}
	Cases map[string]struct {
		Path     string
		Revision int64  `json:"hub_revision"`
		Content  string `json:"hub_content"`
		Deleted  bool   `json:"hub_deleted"`
		A        string `json:"a_content"`
		B        string `json:"b_content"`
		ARev     int64  `json:"a_base_revision"`
		BRev     int64  `json:"b_base_revision"`
		Receipts []string
	}
}

func must(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func loadSamples(t *testing.T) samples {
	t.Helper()
	raw, err := os.ReadFile("../../fixtures/expected.json")
	must(t, err)
	var f samples
	must(t, json.Unmarshal(raw, &f))
	return f
}

func sample(t *testing.T, id string) ([]byte, safefs.Digest) {
	t.Helper()
	s, ok := loadSamples(t).Contents[id]
	if !ok {
		t.Fatalf("missing fixed specimen %q", id)
	}
	unit, err := hex.DecodeString(s.Unit)
	must(t, err)
	return bytes.Repeat(unit, s.Repeat), safefs.Digest{Size: s.Size, Hash: s.Hash}
}

func token(b byte) string { return base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{b}, 32)) }

type lab struct {
	dir       string
	h         *Hub
	server    *httptest.Server
	provision Provision
}

func newLab(t *testing.T) *lab {
	t.Helper()
	l := &lab{dir: t.TempDir(), provision: Provision{Space: "test", Clients: [2]Client{{"client-a", token(1)}, {"client-b", token(2)}}}}
	for _, name := range []string{"hub", "A", "B"} {
		must(t, os.Mkdir(filepath.Join(l.dir, name), 0700))
	}
	must(t, Init(t.Context(), l.dir, "hub", l.provision))
	for _, name := range []string{"A", "B"} {
		must(t, local.Init(t.Context(), l.dir, name))
	}
	l.start(t)
	t.Cleanup(l.stop)
	return l
}

func (l *lab) start(t *testing.T) {
	t.Helper()
	var err error
	l.h, err = Open(t.Context(), l.dir, "hub")
	must(t, err)
	l.server = httptest.NewServer(l.h)
}

func (l *lab) stop() {
	if l.server != nil {
		l.server.Close()
		l.server = nil
	}
	if l.h != nil {
		_ = l.h.Close()
		l.h = nil
	}
}

func (l *lab) reopen(t *testing.T) {
	t.Helper()
	l.stop()
	l.start(t)
}

func (l *lab) req(t *testing.T, root string, client int) local.NetworkRequest {
	t.Helper()
	net, err := protocol.NewClient(l.server.Listener.Addr().String(), l.provision.Clients[client].Token)
	must(t, err)
	return local.NetworkRequest{Volume: l.dir, Root: root, Remote: net}
}

func (l *lab) write(t *testing.T, root, path, id string) {
	t.Helper()
	body, _ := sample(t, id)
	must(t, os.WriteFile(filepath.Join(l.dir, root, path), body, 0600))
}

func (l *lab) status(t *testing.T, root string) local.Status {
	t.Helper()
	s, err := local.Inspect(t.Context(), l.dir, root)
	must(t, err)
	return s
}

func (l *lab) file(t *testing.T, root, path, id string, revision int64) {
	t.Helper()
	want, d := sample(t, id)
	body, err := os.ReadFile(filepath.Join(l.dir, root, path))
	must(t, err)
	s := l.status(t, root)
	expected := local.Base{}
	if revision != 0 {
		expected = local.Base{Revision: revision, Digest: d}
	}
	if base := s.Bases[path]; base != expected || !bytes.Equal(body, want) {
		t.Fatalf("%s/%s = %q %+v; want %q at revision %d (%s)", root, path, body, base, want, revision, d.Hash)
	}
}

func (l *lab) manifest(t *testing.T) protocol.Manifest {
	t.Helper()
	m, err := l.req(t, "A", 0).Remote.Manifest(t.Context())
	must(t, err)
	if !protocol.IsHex(m.Generation, 32) || m.Space != l.provision.Space || m.Client != "client-a" {
		t.Fatalf("incorrect manifest identity: %+v", m)
	}
	return m
}

func (l *lab) hubFile(t *testing.T, path, id, client string, revision int64) protocol.Entry {
	t.Helper()
	want, d := sample(t, id)
	m := l.manifest(t)
	for _, e := range m.Entries {
		if e.Path != path {
			continue
		}
		if e.Revision != revision || e.Deleted || e.Hash == nil || *e.Hash != d.Hash || e.Size != d.Size || e.Client != client {
			t.Fatalf("entry %s = %+v; want %s/%d by %s", path, e, d.Hash, revision, client)
		}
		body, err := l.req(t, "A", 0).Remote.Get(t.Context(), m.Generation, d)
		must(t, err)
		if !bytes.Equal(body, want) {
			t.Fatalf("hub %s = %q, want %q", path, body, want)
		}
		return e
	}
	t.Fatalf("missing manifest entry %q", path)
	return protocol.Entry{}
}

type history struct {
	Change  protocol.Change
	Receipt protocol.Receipt
}

func (l *lab) history(t *testing.T) []history {
	t.Helper()
	l.h.mu.Lock()
	defer l.h.mu.Unlock()
	rows, err := l.h.store.Conn.QueryContext(t.Context(), "SELECT request,response FROM receipts ORDER BY rowid")
	must(t, err)
	defer rows.Close()
	var out []history
	for rows.Next() {
		var request, response []byte
		must(t, rows.Scan(&request, &response))
		var h history
		must(t, json.Unmarshal(request, &h.Change))
		must(t, json.Unmarshal(response, &h.Receipt))
		out = append(out, h)
	}
	must(t, rows.Err())
	return out
}

func (l *lab) count(t *testing.T, table string) int {
	t.Helper()
	l.h.mu.Lock()
	defer l.h.mu.Unlock()
	var n int
	must(t, l.h.store.Conn.QueryRowContext(t.Context(), "SELECT count(*) FROM "+table).Scan(&n))
	return n
}

func (l *lab) clean(t *testing.T, generation string) {
	t.Helper()
	for _, root := range []string{"A", "B"} {
		s := l.status(t, root)
		if s.Registration == nil || !s.Registration.Complete || s.Registration.Generation != generation ||
			len(s.Outbox)+len(s.Holds) != 0 {
			t.Fatalf("%s not clean: %+v", root, s)
		}
		for _, p := range s.Plans {
			if p.Status != "complete" || p.Source != "hub-"+generation {
				t.Fatalf("invalid network plan: %+v", p)
			}
		}
		staged, err := os.ReadDir(filepath.Join(l.dir, root, safefs.Management, "stage"))
		must(t, err)
		if len(staged) != 0 {
			t.Fatalf("stale staging files in %s", root)
		}
	}
}

func (l *lab) seed(t *testing.T) string {
	t.Helper()
	l.write(t, "A", notes, "P")
	g := l.manifest(t).Generation
	must(t, local.Register(t.Context(), l.req(t, "A", 0), "seed"))
	must(t, local.Register(t.Context(), l.req(t, "B", 1), "pull"))
	return g
}

func (l *lab) checkBlob(t *testing.T, id, generation string) {
	t.Helper()
	want, d := sample(t, id)
	body, err := l.req(t, "B", 1).Remote.Get(t.Context(), generation, d)
	must(t, err)
	if !bytes.Equal(body, want) {
		t.Fatalf("preserved blob %s differs", id)
	}
}

func TestP2_01_SeedAndPull(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	want := loadSamples(t).Cases["P2-01"]
	if want.Path != notes || want.Deleted || len(want.Receipts) != 1 || want.Receipts[0] != "a1:1:applied" {
		t.Fatal("fixed seed oracle disagrees with test case")
	}
	l.file(t, "A", want.Path, want.A, want.ARev)
	l.file(t, "B", want.Path, want.B, want.BRev)
	e := l.hubFile(t, want.Path, want.Content, "client-a", want.Revision)
	h := l.history(t)
	if len(h) != 1 || h[0].Receipt.Result != "applied" || h[0].Receipt.Entry.Revision != want.Revision ||
		h[0].Change.Operation != e.Operation || h[0].Change.Base != 0 || h[0].Receipt.Generation != g {
		t.Fatalf("history = %+v", h)
	}
	l.clean(t, g)
}

func TestP2_02_UpdateAndSameContent(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	for i := 0; i < 2; i++ {
		must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
		must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
		l.write(t, "A", notes, "Q")
	}
	l.file(t, "A", notes, "Q", 2)
	l.file(t, "B", notes, "Q", 2)
	e := l.hubFile(t, notes, "Q", "client-a", 2)
	h := l.history(t)
	if len(h) != 2 || h[1].Receipt.Entry.Operation != e.Operation || h[1].Receipt.Result != "applied" ||
		h[1].Change.Base != 1 || h[1].Change.Client != "client-a" || l.count(t, "blobs") != 2 {
		t.Fatalf("history = %+v", h)
	}
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
	l.clean(t, g)
}

func (l *lab) raw(t *testing.T, method, path string, client int, generation, media string, body []byte) (int, []byte) {
	t.Helper()
	req, err := http.NewRequestWithContext(t.Context(), method, l.server.URL+path, bytes.NewReader(body))
	must(t, err)
	if client >= 0 {
		req.Header.Set("Authorization", "Bearer "+l.provision.Clients[client].Token)
	}
	if generation != "" {
		req.Header.Set(protocol.GenerationHeader, generation)
	}
	if media != "" {
		req.Header.Set("Content-Type", media)
	}
	resp, err := l.server.Client().Do(req)
	must(t, err)
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	must(t, err)
	if resp.Header.Get("Cache-Control") != "no-store" {
		t.Fatalf("missing no-store for %s %s", method, path)
	}
	return resp.StatusCode, data
}

func (l *lab) post(t *testing.T, c protocol.Change, client int) (int, []byte) {
	t.Helper()
	data, err := json.Marshal(c)
	must(t, err)
	return l.raw(t, "POST", "/v1/changes", client, "", protocol.JSON, data)
}

func (l *lab) change(t *testing.T, id, path, specimen string, base int64) protocol.Change {
	t.Helper()
	_, d := sample(t, specimen)
	return protocol.Change{Generation: l.manifest(t).Generation, Client: "client-a", Operation: id, Path: path, Base: base, Hash: &d.Hash, Size: d.Size}
}

func assertCode(t *testing.T, status int, body []byte, want int, code string) {
	t.Helper()
	var problem protocol.Problem
	must(t, json.Unmarshal(body, &problem))
	if status != want || problem.Code != code {
		t.Fatalf("got HTTP %d %s, want %d %s", status, body, want, code)
	}
}

func TestP2_03_CASAndTypedReplay(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	origin := l.hubFile(t, notes, "P", "client-a", 1)
	c := l.change(t, strings.Repeat("1", 32), notes, "P", 1)
	status, first := l.post(t, c, 0)
	if status != 200 {
		t.Fatalf("unchanged HTTP %d %s", status, first)
	}
	var result protocol.Receipt
	must(t, json.Unmarshal(first, &result))
	if result.Result != "unchanged" || result.Entry.Operation != origin.Operation || result.Operation != c.Operation {
		t.Fatalf("unchanged result %s", first)
	}
	alternate := fmt.Sprintf(`{
	  "size":3,"deleted":false,"base_revision":1,"content_hash":%q,"path":"\u006eotes.txt",
	  "op_id":%q,"client_id":"client-a","hub_generation":%q
	}`, *c.Hash, c.Operation, g)
	status, body := l.raw(t, "POST", "/v1/changes", 0, "", protocol.JSON, []byte(alternate))
	if status != 200 || !bytes.Equal(first, body) {
		t.Fatalf("typed replay HTTP %d %s", status, body)
	}
	status, body = l.post(t, l.change(t, c.Operation, notes, "Q", 1), 0)
	assertCode(t, status, body, 409, "op_id_reused")
	status, body = l.post(t, l.change(t, strings.Repeat("2", 32), notes, "P", 0), 0)
	assertCode(t, status, body, 409, "revision_mismatch")
	var mismatch struct {
		Current    protocol.Entry `json:"current"`
		Generation string         `json:"hub_generation"`
	}
	must(t, json.Unmarshal(body, &mismatch))
	if mismatch.Current.Operation != origin.Operation || mismatch.Current.Revision != 1 || mismatch.Generation != g {
		t.Fatalf("CAS mismatch response %s", body)
	}
	l.reopen(t)
	if m := l.manifest(t); m.Generation != g || m.Entries[0].Operation != origin.Operation {
		t.Fatalf("manifest changed on normal reopen: %+v", m)
	}
	status, body = l.post(t, c, 0)
	if status != 200 || !bytes.Equal(first, body) || len(l.history(t)) != 2 {
		t.Fatalf("restarted replay HTTP %d %s", status, body)
	}
	l.file(t, "A", notes, "P", 1)
	l.file(t, "B", notes, "P", 1)
	l.clean(t, g)
}

func TestP2_04_EmptyBlob(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	body, d := sample(t, "E")
	for _, want := range []int{201, 200} {
		status, response := l.raw(t, "PUT", "/v1/blobs/"+d.Hash, 0, g, protocol.Binary, body)
		if status != want {
			t.Fatalf("PUT empty HTTP %d %s, want %d", status, response, want)
		}
		if want == 201 {
			c := l.change(t, strings.Repeat("1", 32), "empty.txt", "E", 0)
			l.write(t, "A", "empty.txt", "E")
			status, response := l.post(t, c, 0)
			if status != 200 {
				t.Fatalf("POST empty HTTP %d %s", status, response)
			}
			must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
			must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
		}
	}
	l.hubFile(t, "empty.txt", "E", "client-a", 1)
	l.file(t, "A", "empty.txt", "E", 1)
	l.file(t, "B", "empty.txt", "E", 1)
	if len(l.manifest(t).Entries) != 2 || l.count(t, "blobs") != 2 || len(l.history(t)) != 2 {
		t.Fatal("empty file or its blob was lost or duplicated")
	}
	l.clean(t, g)
}

func TestP2_05_RegistrationRefusalAndSIGKILL(t *testing.T) {
	t.Run("refused", func(t *testing.T) {
		l := newLab(t)
		l.seed(t)
		must(t, os.Mkdir(filepath.Join(l.dir, "C"), 0700))
		must(t, local.Init(t.Context(), l.dir, "C"))
		l.write(t, "C", notes, "Q")
		for _, mode := range []string{"pull", "seed"} {
			if err := local.Register(t.Context(), l.req(t, "C", 1), mode); err == nil {
				t.Fatalf("%s accepted incompatible initial state", mode)
			}
		}
		l.file(t, "C", notes, "Q", 0)
		s := l.status(t, "C")
		if s.Registration != nil || len(s.Outbox)+len(s.Plans)+len(s.Holds) != 0 || len(l.history(t)) != 1 {
			t.Fatalf("refused registration changed state: %+v", s)
		}
		l.hubFile(t, notes, "P", "client-a", 1)
	})
	t.Run("restart_at_commit", func(t *testing.T) {
		l := newLab(t)
		g := l.manifest(t).Generation
		l.write(t, "A", "a.txt", "P")
		l.write(t, "A", "z.txt", "Q")
		committed, release := make(chan struct{}), make(chan struct{})
		defer func() {
			if release != nil {
				close(release)
			}
		}()
		allow := release
		l.h.mu.Lock()
		l.h.afterChangeCommit = func() {
			close(committed)
			<-allow
			l.h.afterChangeCommit = nil
		}
		l.h.mu.Unlock()
		cmd := exec.Command(os.Args[0], "-test.run=^TestSeedProcess$")
		cmd.Env = append(os.Environ(), "FILE_SYNC_CHILD_VOLUME="+l.dir, "FILE_SYNC_CHILD_ADDRESS="+l.server.Listener.Addr().String(),
			"FILE_SYNC_CHILD_TOKEN="+l.provision.Clients[0].Token)
		var log bytes.Buffer
		cmd.Stdout, cmd.Stderr = &log, &log
		must(t, cmd.Start())
		defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()
		select {
		case <-committed:
		case <-time.After(15 * time.Second):
			t.Fatal("child did not reach the first hub commit")
		}
		must(t, cmd.Process.Kill())
		waitErr := cmd.Wait()
		var killed *exec.ExitError
		if !errors.As(waitErr, &killed) || killed.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
			t.Fatalf("expected SIGKILL, got %v %s", waitErr, &log)
		}
		close(release)
		release = nil
		s := l.status(t, "A")
		h := l.history(t)
		if s.Registration == nil || s.Registration.Complete || len(s.Bases) != 0 ||
			len(s.Outbox) != 2 || s.Outbox[0].Status != "ready" || len(h) != 1 ||
			h[0].Change.Operation != s.Outbox[0].Change.Operation {
			t.Fatalf("stopped registration state %+v, history %+v", s, h)
		}
		firstID, secondID := s.Outbox[0].Change.Operation, s.Outbox[1].Change.Operation
		l.hubFile(t, "a.txt", "P", "client-a", 1)
		if err := local.Sync(t.Context(), l.req(t, "A", 0)); err == nil {
			t.Fatal("normal sync accepted incomplete registration")
		}
		l.write(t, "A", "z.txt", "R")
		l.write(t, "A", "added.txt", "R")
		l.reopen(t)
		err := local.Register(t.Context(), l.req(t, "A", 0), "seed")
		if !errors.Is(err, local.ErrConflict) {
			t.Fatalf("expected unplanned edits to hold, got %v", err)
		}
		l.hubFile(t, "a.txt", "P", "client-a", 1)
		l.hubFile(t, "z.txt", "Q", "client-a", 1)
		l.file(t, "A", "a.txt", "P", 1)
		after := l.status(t, "A")
		h = l.history(t)
		if !after.Registration.Complete || after.Registration.Generation != g || len(after.Outbox) != 0 ||
			after.Holds["added.txt"] != "registration_local_edit" || after.Holds["z.txt"] != "registration_local_edit" ||
			len(h) != 2 || h[0].Change.Operation != firstID || h[1].Change.Operation != secondID {
			t.Fatalf("resumed registration %+v, history %+v", after, h)
		}
		_, want := sample(t, "Q")
		if after.Bases["z.txt"] != (local.Base{Revision: 1, Digest: want}) {
			t.Fatal("registered edit advanced to the wrong baseline")
		}
		if err := local.Sync(t.Context(), l.req(t, "A", 0)); !errors.Is(err, local.ErrConflict) || len(l.history(t)) != 2 {
			t.Fatalf("held registration edit was sent: %v", err)
		}
		for _, path := range []string{"added.txt", "z.txt"} {
			got, err := os.ReadFile(filepath.Join(l.dir, "A", path))
			must(t, err)
			want, _ := sample(t, "R")
			if !bytes.Equal(got, want) {
				t.Fatalf("edit %q changed", path)
			}
		}
	})
}

func TestSeedProcess(t *testing.T) {
	volume := os.Getenv("FILE_SYNC_CHILD_VOLUME")
	if volume == "" {
		t.Skip("seed SIGKILL subprocess helper")
	}
	remote, err := protocol.NewClient(os.Getenv("FILE_SYNC_CHILD_ADDRESS"), os.Getenv("FILE_SYNC_CHILD_TOKEN"))
	must(t, err)
	if approval := os.Getenv("FILE_SYNC_CHILD_APPROVAL"); approval != "" {
		_, err := local.Reconcile(t.Context(), local.NetworkRequest{Volume: volume, Root: "B", Remote: remote}, approval)
		if !errors.Is(err, local.ErrConflict) {
			t.Fatalf("reconcile result %v", err)
		}
		return
	}
	must(t, local.Register(t.Context(), local.NetworkRequest{Volume: volume, Root: "A", Remote: remote}, "seed"))
}

func TestP2_06_Reverse(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "B", notes, "Q")
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.file(t, "A", notes, "Q", 2)
	l.file(t, "B", notes, "Q", 2)
	e := l.hubFile(t, notes, "Q", "client-b", 2)
	h := l.history(t)
	if len(h) != 2 || h[1].Receipt.Entry.Operation != e.Operation || h[1].Change.Client != "client-b" ||
		h[1].Change.Base != 1 || l.count(t, "blobs") != 2 {
		t.Fatalf("reverse history %+v", h)
	}
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
	l.clean(t, g)
}
