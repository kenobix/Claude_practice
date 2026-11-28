package hub

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func (l *lab) pending(t *testing.T, req local.NetworkRequest) {
	t.Helper()
	if err := local.Sync(t.Context(), req); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("expected a held path, got %v", err)
	}
}

func (l *lab) assertBody(t *testing.T, name, content string) {
	t.Helper()
	got, err := os.ReadFile(filepath.Join(l.dir, name))
	if content == "" {
		if !errors.Is(err, os.ErrNotExist) {
			t.Fatalf("%s: observed %q and error %v, want absence", name, got, err)
		}
		return
	}
	must(t, err)
	body, digest := sample(t, content)
	if !bytes.Equal(body, got) || protocol.Digest(got) != digest {
		t.Fatalf("%s: %q (%+v), want %s (%+v)", name, got, protocol.Digest(got), content, digest)
	}
}

func (l *lab) assertBase(t *testing.T, root, path, content string, rev int64) {
	t.Helper()
	b := local.Base{Revision: rev}
	if content != "" {
		_, b.Digest = sample(t, content)
	}
	if got := l.status(t, root).Bases[path]; got != b {
		t.Fatalf("%s %q baseline %+v, want %+v", root, path, got, b)
	}
}

func (l *lab) assertDeleted(t *testing.T, path, generation, client string, revision int64) {
	t.Helper()
	m := l.manifest(t)
	i := slices.IndexFunc(m.Entries, func(e protocol.Entry) bool { return e.Path == path })
	if i < 0 || !m.Entries[i].Deleted || m.Entries[i].Hash != nil || m.Entries[i].Size != 0 ||
		m.Entries[i].Revision != revision || m.Entries[i].Client != client || m.Generation != generation {
		t.Fatalf("expected tombstone %q/%d, got %+v", path, revision, m)
	}
}

func (l *lab) assertHistory(t *testing.T, generation string, want ...string) {
	t.Helper()
	var got []string
	ids := make(map[string]bool)
	for _, h := range l.history(t) {
		id := h.Change.Operation
		if ids[id] || h.Change.Generation != generation || h.Receipt.Generation != generation ||
			h.Receipt.Operation != id || h.Change.Path != h.Receipt.Entry.Path {
			t.Fatalf("inconsistent receipt: %+v", h)
		}
		ids[id] = true
		got = append(got, h.Change.Client+":"+h.Change.Path+":"+
			strconv.FormatInt(h.Receipt.Entry.Revision, 10)+":"+h.Receipt.Result)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("history %v, want %v", got, want)
	}
}

func (l *lab) held(t *testing.T, path, generation string, baseRevision int64, localKind, localBody,
	hubKind, hubBody string, hubRevision int64) local.Conflict {
	t.Helper()
	s := l.status(t, "B")
	i := slices.IndexFunc(s.Conflicts, func(c local.Conflict) bool { return c.Path == path && c.State != "resolved" })
	if i < 0 {
		t.Fatalf("missing conflict for %s: %+v", path, s)
	}
	c := s.Conflicts[i]
	if c.State != "pending" || !c.Saved || c.Generation != generation || c.Base.Revision != baseRevision ||
		c.Local.Kind != localKind || c.Hub[0].Kind != hubKind || c.Hub[0].Revision != hubRevision {
		t.Fatalf("incorrect conflict: %+v", c)
	}
	l.assertCandidate(t, "B", c.Local, localBody)
	l.assertCandidate(t, "B", c.Hub[0], hubBody)
	return c
}

func (l *lab) assertCandidate(t *testing.T, root string, c local.ConflictCandidate, content string) {
	t.Helper()
	if !c.Saved {
		t.Fatalf("candidate was not saved: %+v", c)
	}
	if content == "" {
		if c.Copy != "" || c.Digest != (safefs.Digest{}) {
			t.Fatalf("absence has a body: %+v", c)
		}
		return
	}
	_, d := sample(t, content)
	if c.Digest != d || !safefs.StageName(c.Copy) {
		t.Fatalf("copy mismatch %+v, want %+v", c, d)
	}
	l.assertBody(t, filepath.Join(root, safefs.Management, "stage", c.Copy), content)
}

func (l *lab) concurrentPost(t *testing.T, publish func(), observe func(int)) local.NetworkRequest {
	t.Helper()
	var once sync.Once
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			l.h.ServeHTTP(w, r)
			return
		}
		once.Do(publish)
		actual := httptest.NewRecorder()
		l.h.ServeHTTP(actual, r)
		observe(actual.Code)
		for k, values := range actual.Header() {
			w.Header()[k] = values
		}
		w.WriteHeader(actual.Code)
		_, _ = io.Copy(w, actual.Body)
	}))
	t.Cleanup(server.Close)
	remote, err := protocol.NewClient(server.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	return local.NetworkRequest{Volume: l.dir, Root: "B", Remote: remote}
}

func TestP3_01_ConcurrentCreate(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a := l.req(t, "A", 0)
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, local.Register(t.Context(), l.req(t, "B", 1), "pull"))
	l.write(t, "A", notes, "P")
	l.write(t, "B", notes, "Q")
	status := make(chan int, 1)
	req := l.concurrentPost(t, func() {
		if err := local.Sync(t.Context(), a); err != nil {
			t.Errorf("A create: %v", err)
		}
	}, func(code int) { status <- code })
	l.pending(t, req)
	if code := <-status; code != 409 {
		t.Fatalf("B CAS HTTP %d", code)
	}
	l.hubFile(t, notes, "P", "client-a", 1)
	l.file(t, "A", notes, "P", 1)
	l.assertBody(t, "B/"+notes, "Q")
	l.assertBase(t, "B", notes, "", 0)
	c := l.held(t, notes, g, 0, "file", "Q", "file", "P", 1)
	if c.Rejected == nil || c.Rejected.Base != 0 || l.status(t, "B").Outbox[0].Status != "rejected" {
		t.Fatalf("lost rejected base-zero request: %+v", c)
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied")
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g) // B's completed PUT is still retained after CAS refusal.
}

func (l *lab) conflictingEdits(t *testing.T) string {
	t.Helper()
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.pending(t, l.req(t, "B", 1))
	return g
}

func TestP3_02_EditVsEdit(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	want := loadSamples(t).Cases["P3-02"]
	l.write(t, "A", want.Path, want.A)
	l.write(t, "B", want.Path, want.B)
	a := l.req(t, "A", 0)
	status := make(chan int, 1)
	b := l.concurrentPost(t, func() {
		if err := local.Sync(t.Context(), a); err != nil {
			t.Errorf("A edit: %v", err)
		}
	}, func(code int) { status <- code })
	l.pending(t, b)
	if code := <-status; code != 409 {
		t.Fatalf("B CAS HTTP %d", code)
	}
	l.hubFile(t, want.Path, want.Content, "client-a", want.Revision)
	l.file(t, "A", want.Path, want.A, want.ARev)
	l.assertBody(t, "B/"+want.Path, want.B)
	l.assertBase(t, "B", want.Path, "P", want.BRev)
	l.held(t, want.Path, g, want.BRev, "file", want.B, "file", want.Content, want.Revision)
	if !slices.Equal(want.Receipts, []string{"a1:1:applied", "a2:2:applied"}) {
		t.Fatalf("fixture receipt sequence %+v", want.Receipts)
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
}

func TestP3_03_HubDeleteVsLocalEdit(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "B", notes, "R")
	must(t, os.Remove(filepath.Join(l.dir, "A", notes)))
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.pending(t, l.req(t, "B", 1))
	l.assertDeleted(t, notes, g, "client-a", 2)
	l.assertBody(t, "A/"+notes, "")
	l.assertBody(t, "B/"+notes, "R")
	l.assertBase(t, "A", notes, "", 2)
	l.assertBase(t, "B", notes, "P", 1)
	l.held(t, notes, g, 1, "file", "R", "deleted", "", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
}

func TestP3_04_LocalDeleteVsHubEdit(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	must(t, os.Remove(filepath.Join(l.dir, "B", notes)))
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.pending(t, l.req(t, "B", 1))
	l.hubFile(t, notes, "Q", "client-a", 2)
	l.file(t, "A", notes, "Q", 2)
	l.assertBody(t, "B/"+notes, "")
	l.assertBase(t, "B", notes, "P", 1)
	l.held(t, notes, g, 1, "deleted", "", "file", "Q", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
}

func TestP3_05_BothDelete(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	for i, root := range []string{"A", "B"} {
		must(t, os.Remove(filepath.Join(l.dir, root, notes)))
		must(t, local.Sync(t.Context(), l.req(t, root, i)))
		l.assertBody(t, root+"/"+notes, "")
		l.assertBase(t, root, notes, "", 2)
	}
	l.assertDeleted(t, notes, g, "client-a", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
	l.clean(t, g)
}

func (l *lab) offlineDelete(t *testing.T) string {
	t.Helper()
	g := l.seed(t)
	must(t, os.Remove(filepath.Join(l.dir, "A", notes)))
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.file(t, "B", notes, "P", 1)
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	return g
}

func (l *lab) assertTrash(t *testing.T, root, path, content string, rev int64) {
	t.Helper()
	s := l.status(t, root)
	i := slices.IndexFunc(s.Plans, func(p local.Plan) bool { return p.Path == path && p.Target.Revision == rev })
	if i < 0 {
		t.Fatalf("missing deletion plan: %+v", s.Plans)
	}
	p := s.Plans[i]
	if !p.BackedUp || p.Status != "complete" || p.Target.Hash != "" || !strings.HasSuffix(p.Backup(), ".trash") {
		t.Fatalf("invalid deletion plan %+v", p)
	}
	l.assertBody(t, filepath.Join(root, safefs.Management, "stage", p.Backup()), content)
}

func TestP3_06_OfflineDelete(t *testing.T) {
	l := newLab(t)
	g := l.offlineDelete(t)
	want := loadSamples(t).Cases["P3-06"]
	if !want.Deleted || want.Content != "" || !slices.Equal(want.Receipts, []string{"a1:1:applied", "a2:2:applied"}) {
		t.Fatalf("fixture deletion oracle %+v", want)
	}
	l.assertDeleted(t, want.Path, g, "client-a", want.Revision)
	l.assertBody(t, "A/"+want.Path, want.A)
	l.assertBody(t, "B/"+want.Path, want.B)
	l.assertBase(t, "A", want.Path, "", want.ARev)
	l.assertBase(t, "B", want.Path, "", want.BRev)
	l.assertTrash(t, "B", want.Path, "P", want.BRev)
	if s := l.status(t, "B"); len(s.Conflicts) != 0 || len(s.Holds) != 0 {
		t.Fatalf("delete unexpectedly held: %+v", s)
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
}

func TestP3_07_Recreate(t *testing.T) {
	l := newLab(t)
	g := l.offlineDelete(t)
	l.write(t, "B", notes, "R")
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.hubFile(t, notes, "R", "client-b", 3)
	l.file(t, "A", notes, "R", 3)
	l.file(t, "B", notes, "R", 3)
	l.assertTrash(t, "B", notes, "P", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-b:notes.txt:3:applied")
	if l.history(t)[2].Change.Base != 2 {
		t.Fatal("recreation did not use the tombstone revision")
	}
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "R", g)
}

func TestP3_08_SameEdit(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	for i, root := range []string{"A", "B"} {
		l.write(t, root, notes, "Q")
		must(t, local.Sync(t.Context(), l.req(t, root, i)))
		l.file(t, root, notes, "Q", 2)
	}
	l.hubFile(t, notes, "Q", "client-a", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
	l.clean(t, g)
}

func TestP3_09_LostDBReconcile(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	db := filepath.Join(l.dir, "B", safefs.Management, "state", safefs.StateDB)
	archived := filepath.Join(t.TempDir(), "saved.sqlite")
	old, err := os.ReadFile(db)
	must(t, err)
	must(t, os.Rename(db, archived))
	l.write(t, "B", notes, "Q")
	b := l.req(t, "B", 1)
	if err := local.Sync(t.Context(), b); err == nil {
		t.Fatal("sync accepted missing state")
	}
	plan, err := local.Reconcile(t.Context(), b, "")
	must(t, err)
	if len(plan.Paths) != 1 || plan.Paths[0].Match || len(plan.State) == 0 || !protocol.IsHex(plan.Approval, 64) ||
		plan.Manifest.Generation != g || plan.Prior != nil {
		t.Fatalf("unexpected reconciliation plan: %+v", plan)
	}
	if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("reconcile did not hold the difference: %v", err)
	}
	l.pending(t, b)
	l.hubFile(t, notes, "P", "client-a", 1)
	l.file(t, "A", notes, "P", 1)
	l.assertBody(t, "B/"+notes, "Q")
	l.assertBase(t, "B", notes, "", 0)
	l.held(t, notes, g, 0, "file", "Q", "file", "P", 1)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied")
	preserved, err := os.ReadFile(archived)
	must(t, err)
	if !bytes.Equal(old, preserved) || l.count(t, "blobs") != 1 {
		t.Fatal("old database changed or B uploaded a blob")
	}
}

func TestP3_10_RevisionMovesDuringResolution(t *testing.T) {
	l := newLab(t)
	g := l.conflictingEdits(t)
	a := l.req(t, "A", 0)
	l.write(t, "A", notes, "S")
	status := make(chan int, 1)
	bRace := l.concurrentPost(t, func() {
		if err := local.Sync(t.Context(), a); err != nil {
			t.Errorf("A edit during resolution: %v", err)
		}
	}, func(code int) { status <- code })
	choice := local.ResolveRequest{Path: notes, Generation: g, Revision: 2, Choice: "local"}
	var problem *protocol.Problem
	if err := local.Resolve(t.Context(), bRace, choice); !errors.Is(err, local.ErrConflict) ||
		!errors.As(err, &problem) || problem.Code != "revision_mismatch" {
		t.Fatalf("resolve stale choice: %v", err)
	}
	if code := <-status; code != 409 {
		t.Fatalf("stale resolve HTTP %d", code)
	}
	c := l.held(t, notes, g, 1, "file", "R", "file", "Q", 2)
	l.assertCandidate(t, "B", c.Hub[1], "S")
	failed := c.Resolution.Change.Operation
	l.hubFile(t, notes, "S", "client-a", 3)
	l.file(t, "A", notes, "S", 3)
	l.assertBody(t, "B/"+notes, "R")
	l.assertBase(t, "B", notes, "P", 1)
	b := l.req(t, "B", 1)
	choice.Revision = 3
	must(t, local.Resolve(t.Context(), b, choice))
	l.hubFile(t, notes, "R", "client-b", 4)
	l.file(t, "B", notes, "R", 4)
	done := l.status(t, "B").Conflicts[0]
	if done.State != "resolved" || done.Resolution.State != "complete" || done.Resolution.Change.Operation == failed ||
		len(done.PriorResolutions) != 1 || done.PriorResolutions[0].Change.Operation != failed {
		t.Fatalf("resolution state %+v", done)
	}
	l.assertCandidate(t, "B", done.Hub[0], "Q")
	l.assertCandidate(t, "B", done.Hub[1], "S")
	l.assertCandidate(t, "B", done.Local, "R")
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-a:notes.txt:3:applied", "client-b:notes.txt:4:applied")
	for _, h := range l.history(t) {
		if h.Change.Operation == failed {
			t.Fatal("stale resolution stored a receipt")
		}
	}
	for _, content := range []string{"P", "Q", "R", "S"} {
		l.checkBlob(t, content, g)
	}
}

func TestP3_11_StructureChange(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	l.write(t, "A", "a", "P")
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, local.Register(t.Context(), b, "pull"))
	q, d := sample(t, "Q")
	must(t, a.Remote.Put(t.Context(), g, d, q))
	c := l.change(t, strings.Repeat("f", 32), "a/b", "Q", 0)
	status, payload := l.post(t, c, 0)
	assertCode(t, status, payload, 409, "namespace_conflict")
	l.file(t, "A", "a", "P", 1)
	l.file(t, "B", "a", "P", 1)
	l.assertHistory(t, g, "client-a:a:1:applied")
	l.checkBlob(t, "Q", g)
	must(t, os.Remove(filepath.Join(l.dir, "A", "a")))
	must(t, local.Sync(t.Context(), a))
	must(t, os.Mkdir(filepath.Join(l.dir, "A", "a"), 0700))
	l.write(t, "A", "a/b", "Q")
	must(t, local.Sync(t.Context(), a))
	must(t, local.Sync(t.Context(), b))
	l.assertDeleted(t, "a", g, "client-a", 2)
	l.hubFile(t, "a/b", "Q", "client-a", 1)
	l.file(t, "B", "a/b", "Q", 1)
	l.assertBase(t, "B", "a", "", 2)
	l.assertTrash(t, "B", "a", "P", 2)
	l.assertHistory(t, g, "client-a:a:1:applied", "client-a:a:2:applied", "client-a:a/b:1:applied")
	l.checkBlob(t, "P", g)
}

func TestP3_12_HubReturnsToBaselineContent(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "B", notes, "R")
	for _, content := range []string{"Q", "P"} {
		l.write(t, "A", notes, content)
		must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	}
	l.pending(t, l.req(t, "B", 1))
	l.hubFile(t, notes, "P", "client-a", 3)
	l.file(t, "A", notes, "P", 3)
	l.assertBody(t, "B/"+notes, "R")
	l.assertBase(t, "B", notes, "P", 1)
	l.held(t, notes, g, 1, "file", "R", "file", "P", 3)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-a:notes.txt:3:applied")
	l.checkBlob(t, "P", g)
	l.checkBlob(t, "Q", g)
}

func TestP3_13_ReconcileProtectsUserStructure(t *testing.T) {
	for _, hasChild := range []bool{true, false} {
		name := "empty_user_directory"
		if hasChild {
			name = "user_file_below_hub_path"
		}
		t.Run(name, func(t *testing.T) {
			l := newLab(t)
			g := l.manifest(t).Generation
			l.write(t, "A", "a", "P")
			must(t, local.Register(t.Context(), l.req(t, "A", 0), "seed"))
			b := l.req(t, "B", 1)
			must(t, os.Mkdir(filepath.Join(l.dir, "B", "a"), 0700))
			if hasChild {
				l.write(t, "B", "a/b", "Q")
				old := filepath.Join(l.dir, "B", safefs.Management, "state", safefs.StateDB)
				must(t, os.Rename(old, filepath.Join(t.TempDir(), "lost.sqlite")))
			}
			plan, err := local.Reconcile(t.Context(), b, "")
			must(t, err)
			if !slices.Contains(plan.Blocked, "a") || (hasChild && !slices.Contains(plan.Blocked, "a/b")) {
				t.Fatalf("structure not shown: %+v", plan)
			}
			if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
				t.Fatalf("structure changed with approval: %v", err)
			}
			if hasChild {
				l.assertBody(t, "B/a/b", "Q")
			}
			st, err := os.Stat(filepath.Join(l.dir, "B", "a"))
			must(t, err)
			if !st.IsDir() {
				t.Fatal("lost user's directory")
			}
			must(t, os.Rename(filepath.Join(l.dir, "B", "a"), filepath.Join(l.dir, "B", "kept")))
			if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
				t.Fatalf("old approval survived an edit: %v", err)
			}
			plan, err = local.Reconcile(t.Context(), b, "")
			must(t, err)
			if len(plan.Blocked) != 0 {
				t.Fatalf("structural hold did not clear after explicit move: %+v", plan)
			}
			if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
				t.Fatalf("reconcile should hold unmatched files: %v", err)
			}
			l.held(t, "a", g, 0, "unregistered", "", "file", "P", 1)
			if hasChild {
				l.assertBody(t, "B/kept/b", "Q")
			}
			l.assertBase(t, "B", "a", "", 0)
			l.hubFile(t, "a", "P", "client-a", 1)
			l.file(t, "A", "a", "P", 1)
			l.assertHistory(t, g, "client-a:a:1:applied")
		})
	}
}

func TestP3DeleteReplayAndOldGenerationPrecedesReceipt(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	c := protocol.Change{Generation: g, Client: "client-a", Operation: strings.Repeat("c", 32), Path: notes, Base: 1, Deleted: true}
	code, original := l.post(t, c, 0)
	if code != 200 {
		t.Fatalf("delete %d %s", code, original)
	}
	l.reopen(t)
	if code, data := l.post(t, c, 0); code != 200 || !bytes.Equal(data, original) {
		t.Fatalf("delete replay %d %s", code, data)
	}
	unchanged := c
	unchanged.Operation, unchanged.Base = strings.Repeat("d", 32), 2
	code, data := l.post(t, unchanged, 0)
	var receipt protocol.Receipt
	must(t, json.Unmarshal(data, &receipt))
	if code != 200 || receipt.Result != "unchanged" || receipt.Entry.Revision != 2 || receipt.Entry.Operation != c.Operation {
		t.Fatalf("repeat tombstone %d %s", code, data)
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-a:notes.txt:2:unchanged")
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		_, err := tx.Exec("UPDATE spaces SET generation=?", strings.Repeat("f", 32))
		return err
	}))
	l.reopen(t)
	code, data = l.post(t, c, 0)
	assertCode(t, code, data, 409, "generation_mismatch")
	if len(l.history(t)) != 3 {
		t.Fatal("old generation changed history")
	}
}

func TestP3IncompleteCandidateDoesNotBlockAnUnrelatedPath(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	l.write(t, "B", notes, "R")
	l.write(t, "B", "usable", "P")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	_, q := sample(t, "Q")
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodGet && r.URL.Path == "/v1/blobs/"+q.Hash {
			writeProblem(w, problem(404, "blob_not_found"))
			return
		}
		l.h.ServeHTTP(w, r)
	}))
	defer proxy.Close()
	remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	l.pending(t, local.NetworkRequest{Volume: l.dir, Root: "B", Remote: remote})
	s := l.status(t, "B")
	if len(s.Conflicts) != 1 || s.Conflicts[0].Saved || s.Conflicts[0].Hub[0].Failure != "blob_unavailable" {
		t.Fatalf("reported incomplete save as complete: %+v", s)
	}
	localCopy := s.Conflicts[0].Local
	l.assertCandidate(t, "B", localCopy, "R")
	l.hubFile(t, "usable", "P", "client-b", 1)
	l.file(t, "B", "usable", "P", 1)
	l.assertBase(t, "B", notes, "P", 1)
	l.write(t, "B", notes, "S")
	l.pending(t, l.req(t, "B", 1))
	c := l.held(t, notes, g, 1, "file", "R", "file", "Q", 2)
	if c.Local != localCopy {
		t.Fatal("new edit replaced the detection-time copy")
	}
	l.assertBody(t, "B/"+notes, "S")
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-b:usable:1:applied")
}

func TestP3ResolutionReplayPreservesCandidatesAndPreimage(t *testing.T) {
	for _, choice := range []string{"hub", "delete"} {
		t.Run(choice, func(t *testing.T) {
			l := newLab(t)
			g := l.conflictingEdits(t)
			dropped := make(chan []byte, 2)
			proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost {
					l.h.ServeHTTP(w, r)
					return
				}
				b, _ := io.ReadAll(r.Body)
				r.Body = io.NopCloser(bytes.NewReader(b))
				actual := httptest.NewRecorder()
				l.h.ServeHTTP(actual, r)
				if actual.Code != 200 {
					t.Errorf("resolution POST %d: %s", actual.Code, actual.Body)
				}
				dropped <- b
				conn, _, err := w.(http.Hijacker).Hijack()
				if err == nil {
					conn.Close()
				}
			}))
			defer proxy.Close()
			remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
			must(t, err)
			selection := local.ResolveRequest{Path: notes, Generation: g, Revision: 2, Choice: choice}
			// Preserve an additional edit before asking the hub to resolve.
			l.write(t, "B", notes, "S")
			if err := local.Resolve(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "B", Remote: remote}, selection); err == nil {
				t.Fatal("response loss was reported as a completed resolution")
			}
			if len(dropped) != 2 || !bytes.Equal(<-dropped, <-dropped) {
				t.Fatal("resolution did not replay identical request bytes")
			}
			s := l.status(t, "B")
			r := s.Conflicts[0].Resolution
			if r.State != "ready" || r.Receipt != nil || s.Bases[notes].Revision != 1 {
				t.Fatalf("premature local acknowledgment: %+v", s)
			}
			l.assertBody(t, "B/"+notes, "S")
			must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
			s = l.status(t, "B")
			done := s.Conflicts[0]
			if done.State != "resolved" || done.Resolution.Change.Operation != r.Change.Operation || done.Resolution.Receipt == nil {
				t.Fatalf("failed to resume original resolution: %+v", done)
			}
			l.assertCandidate(t, "B", done.Local, "R")
			l.assertCandidate(t, "B", done.Hub[0], "Q")
			l.assertCandidate(t, "B", done.Edits[0], "S")
			if choice == "delete" {
				l.assertDeleted(t, notes, g, "client-b", 3)
				l.assertBody(t, "B/"+notes, "")
				l.assertBase(t, "B", notes, "", 3)
				l.assertTrash(t, "B", notes, "S", 3)
				l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-b:notes.txt:3:applied")
			} else {
				l.file(t, "B", notes, "Q", 2)
				l.hubFile(t, notes, "Q", "client-a", 2)
				l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-b:notes.txt:2:unchanged")
			}
		})
	}
}

func TestP3ExplicitFileCandidate(t *testing.T) {
	l := newLab(t)
	g := l.conflictingEdits(t)
	l.write(t, "B", "choice", "S")
	b := l.req(t, "B", 1)
	must(t, local.Resolve(t.Context(), b, local.ResolveRequest{Path: notes, Generation: g, Revision: 2, Choice: "file", File: "choice"}))
	l.assertBody(t, "B/choice", "S")
	l.file(t, "B", notes, "S", 3)
	l.hubFile(t, notes, "S", "client-b", 3)
	done := l.status(t, "B").Conflicts[0]
	if done.State != "resolved" || len(done.Edits) != 1 {
		t.Fatalf("explicit copy is missing: %+v", done)
	}
	l.assertCandidate(t, "B", done.Edits[0], "S")
	l.assertCandidate(t, "B", done.Local, "R")
	l.assertCandidate(t, "B", done.Hub[0], "Q")
}

func TestP3ReconcileNewGenerationQuarantinesOldWork(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "B", notes, "R")
	// The POST arrives at the transport, but never reaches the hub.
	dropped := make(chan struct{}, 2)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			dropped <- struct{}{}
			conn, _, err := w.(http.Hijacker).Hijack()
			if err == nil {
				conn.Close()
			}
			return
		}
		l.h.ServeHTTP(w, r)
	}))
	defer proxy.Close()
	remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	if err := local.Sync(t.Context(), local.NetworkRequest{Volume: l.dir, Root: "B", Remote: remote}); err == nil || len(dropped) != 2 {
		t.Fatalf("unexpected interrupted send %v", err)
	}
	old := l.status(t, "B").Outbox[0]
	if old.Status != "ready" || old.Change.Generation != g {
		t.Fatalf("no ready old operation: %+v", old)
	}
	oldFile := old.Change.Operation + ".outgoing"
	l.assertBody(t, filepath.Join("B", safefs.Management, "stage", oldFile), "R")
	newGen := strings.Repeat("e", 32)
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		_, err := tx.Exec("UPDATE spaces SET generation=?", newGen)
		return err
	}))
	l.reopen(t)
	b := l.req(t, "B", 1)
	l.pending(t, b)
	plan, err := local.Reconcile(t.Context(), b, "")
	must(t, err)
	if plan.Prior == nil || len(plan.Prior.Outbox) != 1 || len(plan.Stage) != 1 ||
		plan.Prior.Outbox[0].Change.Operation != old.Change.Operation {
		t.Fatalf("did not list old work: %+v", plan)
	}
	if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("re-register: %v", err)
	}
	l.pending(t, b)
	l.held(t, notes, newGen, 0, "file", "R", "file", "P", 1)
	if len(l.status(t, "B").Outbox) != 0 {
		t.Fatal("old operation moved to the new outbox")
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied")
	l.assertBody(t, filepath.Join("B", safefs.Management, "stage", oldFile), "R")
	mgr := filepath.Join(l.dir, "B", safefs.Management)
	siblings, err := os.ReadDir(mgr)
	must(t, err)
	var checked bool
	for _, d := range siblings {
		if !safefs.ReconcileDirName(d.Name()) {
			continue
		}
		db, err := sql.Open("sqlite3", "file:"+filepath.Join(mgr, d.Name(), "state.sqlite")+"?mode=ro")
		must(t, err)
		var id string
		must(t, db.QueryRowContext(t.Context(), "SELECT id FROM outbox").Scan(&id))
		must(t, db.Close())
		checked = id == old.Change.Operation
	}
	if !checked {
		t.Fatal("old database was not preserved")
	}
}

func TestP3ReconcileRefusesChangedManifestAndSameGenerationRegression(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	plan, err := local.Reconcile(t.Context(), b, "")
	must(t, err)
	l.write(t, "A", notes, "Q")
	must(t, local.Sync(t.Context(), a))
	if _, err := local.Reconcile(t.Context(), b, plan.Approval); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("old approval accepted despite hub change: %v", err)
	}
	must(t, local.Sync(t.Context(), b))
	first := l.history(t)[0].Receipt.Entry
	must(t, l.h.store.Write(t.Context(), func(tx *sql.Tx) error {
		_, err := tx.Exec("UPDATE entries SET revision=1,hash=?,size=?,op=? WHERE path=?", *first.Hash, first.Size, first.Operation, notes)
		return err
	}))
	l.write(t, "B", "fresh", "S")
	if err := local.Sync(t.Context(), b); err == nil || !strings.Contains(err.Error(), "regressed") {
		t.Fatalf("same-generation rollback accepted: %v", err)
	}
	l.file(t, "B", notes, "Q", 2)
	l.assertBody(t, "B/fresh", "S")
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
}

func TestP3StructureDoesNotTreatENOTDIRAsDeletion(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	must(t, os.Mkdir(filepath.Join(l.dir, "A", "a"), 0700))
	l.write(t, "A", "a/b", "P")
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, local.Register(t.Context(), b, "pull"))
	must(t, os.Remove(filepath.Join(l.dir, "B", "a/b")))
	must(t, os.Remove(filepath.Join(l.dir, "B", "a")))
	l.write(t, "B", "a", "Q")
	l.write(t, "B", "other", "R")
	l.pending(t, b)
	l.assertBody(t, "B/a", "Q")
	l.assertBase(t, "B", "a/b", "P", 1)
	l.hubFile(t, "a/b", "P", "client-a", 1)
	l.hubFile(t, "other", "R", "client-b", 1)
	l.assertHistory(t, g, "client-a:a/b:1:applied", "client-b:other:1:applied")
	list, err := local.ListConflicts(t.Context(), b)
	must(t, err)
	if len(list) != 2 || list[0].State != "structural_hold" || list[0].Path != "a" || list[1].Path != "a/b" {
		t.Fatalf("missing structural group: %+v", list)
	}
}

func TestP3ReverseStructureChangeOnlyRemovesOwnedEmptyDirectory(t *testing.T) {
	for _, owned := range []bool{true, false} {
		name := "server_created"
		if !owned {
			name = "user_replaced"
		}
		t.Run(name, func(t *testing.T) {
			l := newLab(t)
			g := l.manifest(t).Generation
			a, b := l.req(t, "A", 0), l.req(t, "B", 1)
			must(t, os.Mkdir(filepath.Join(l.dir, "A", "a"), 0700))
			l.write(t, "A", "a/b", "P")
			must(t, local.Register(t.Context(), a, "seed"))
			must(t, local.Register(t.Context(), b, "pull"))
			parent := filepath.Join(l.dir, "B", "a")
			if !owned {
				must(t, os.Rename(parent, filepath.Join(l.dir, "spare")))
				must(t, os.Mkdir(parent, 0700))
				must(t, os.Rename(filepath.Join(l.dir, "spare", "b"), filepath.Join(parent, "b")))
			}
			must(t, os.Remove(filepath.Join(l.dir, "A", "a/b")))
			must(t, local.Sync(t.Context(), a))
			must(t, os.Remove(filepath.Join(l.dir, "A", "a")))
			l.write(t, "A", "a", "Q")
			must(t, local.Sync(t.Context(), a))
			if owned {
				must(t, local.Sync(t.Context(), b))
				l.file(t, "B", "a", "Q", 1)
			} else {
				l.pending(t, b)
				st, err := os.Stat(parent)
				must(t, err)
				if !st.IsDir() {
					t.Fatal("user's directory lost")
				}
				l.assertBase(t, "B", "a", "", 0)
			}
			l.assertBase(t, "B", "a/b", "", 2)
			l.assertTrash(t, "B", "a/b", "P", 2)
			l.assertHistory(t, g, "client-a:a/b:1:applied", "client-a:a/b:2:applied", "client-a:a:1:applied")
		})
	}
}

func TestP3ReconcileSIGKILLAfterStateActivation(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "B", notes, "R")
	b := l.req(t, "B", 1)
	report, err := local.Reconcile(t.Context(), b, "")
	must(t, err)
	reached := make(chan struct{}, 1)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == "GET" && strings.HasPrefix(r.URL.Path, "/v1/blobs/") {
			reached <- struct{}{}
			<-r.Context().Done()
			return
		}
		l.h.ServeHTTP(w, r)
	}))
	defer proxy.Close()
	child := exec.CommandContext(t.Context(), os.Args[0], "-test.run=^TestSeedProcess$")
	child.Env = append(os.Environ(), "FILE_SYNC_CHILD_VOLUME="+l.dir, "FILE_SYNC_CHILD_ADDRESS="+proxy.Listener.Addr().String(),
		"FILE_SYNC_CHILD_TOKEN="+l.provision.Clients[1].Token, "FILE_SYNC_CHILD_APPROVAL="+report.Approval)
	must(t, child.Start())
	defer func() { _ = child.Process.Kill(); _ = child.Wait() }()
	select {
	case <-reached:
	case <-time.After(15 * time.Second):
		t.Fatal("child did not reach candidate GET after the DB switch")
	}
	must(t, child.Process.Kill())
	var exit *exec.ExitError
	if err := child.Wait(); !errors.As(err, &exit) || exit.Sys().(syscall.WaitStatus).Signal() != syscall.SIGKILL {
		t.Fatalf("child was not killed: %v", err)
	}
	pending := l.status(t, "B")
	if pending.Registration == nil || pending.Registration.Complete || pending.Registration.Mode != "reconcile" ||
		len(pending.Bases) != 0 || len(pending.Conflicts) != 1 || pending.Conflicts[0].Saved {
		t.Fatalf("missing incomplete reconciliation state: %+v", pending)
	}
	l.assertCandidate(t, "B", pending.Conflicts[0].Local, "R")
	if err := local.Sync(t.Context(), b); err == nil {
		t.Fatal("incomplete registration ran normal sync")
	}
	if _, err := local.Reconcile(t.Context(), b, report.Approval); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("resume result %v", err)
	}
	c := l.held(t, notes, g, 0, "file", "R", "file", "P", 1)
	if c.ID != pending.Conflicts[0].ID || c.Local.Copy != pending.Conflicts[0].Local.Copy || !l.status(t, "B").Registration.Complete {
		t.Fatal("reconcile did not resume the original approved plan")
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied")
	l.assertBody(t, "B/"+notes, "R")
}

func TestP3IdentityMismatchStopsSyncUntilApproved(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	marker := filepath.Join(l.dir, "B", safefs.Management, "state", safefs.RootID)
	bad := []byte(strings.Repeat("f", 32))
	must(t, os.WriteFile(marker, bad, 0600))
	b := l.req(t, "B", 1)
	if err := local.Sync(t.Context(), b); err == nil || !strings.Contains(err.Error(), "identity disagree") {
		t.Fatalf("different identity accepted: %v", err)
	}
	report, err := local.Reconcile(t.Context(), b, "")
	must(t, err)
	if report.OldStatus != "unverified" || report.Prior != nil || len(report.Paths) != 1 || !report.Paths[0].Match {
		t.Fatalf("unverified DB was interpreted: %+v", report)
	}
	_, err = local.Reconcile(t.Context(), b, report.Approval)
	must(t, err)
	l.file(t, "B", notes, "P", 1)
	must(t, local.Sync(t.Context(), b))
	l.assertHistory(t, g, "client-a:notes.txt:1:applied")
	files, err := os.ReadDir(filepath.Join(l.dir, "B", safefs.Management))
	must(t, err)
	var retained bool
	for _, f := range files {
		if safefs.ReconcileDirName(f.Name()) {
			old, err := os.ReadFile(filepath.Join(l.dir, "B", safefs.Management, f.Name(), safefs.RootID))
			must(t, err)
			retained = bytes.Equal(old, bad)
		}
	}
	if !retained {
		t.Fatal("unverified state marker was not retained")
	}
}

func TestP3PullRegistersTombstoneWithoutCreatingAFile(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	l.write(t, "A", notes, "P")
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, os.Remove(filepath.Join(l.dir, "A", notes)))
	must(t, local.Sync(t.Context(), a))
	must(t, local.Register(t.Context(), b, "pull"))
	l.assertBody(t, "B/"+notes, "")
	l.assertBase(t, "B", notes, "", 2)
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied")
	l.clean(t, g)
}

func TestP3OwnedNonemptyDirectoryIsNotRecursivelyRemoved(t *testing.T) {
	l := newLab(t)
	g := l.manifest(t).Generation
	a, b := l.req(t, "A", 0), l.req(t, "B", 1)
	must(t, os.MkdirAll(filepath.Join(l.dir, "A", "outer/inner"), 0700))
	l.write(t, "A", "outer/inner/doc", "P")
	must(t, local.Register(t.Context(), a, "seed"))
	must(t, local.Register(t.Context(), b, "pull"))
	must(t, os.Remove(filepath.Join(l.dir, "A", "outer/inner/doc")))
	must(t, local.Sync(t.Context(), a))
	must(t, os.Remove(filepath.Join(l.dir, "A", "outer/inner")))
	must(t, os.Remove(filepath.Join(l.dir, "A", "outer")))
	l.write(t, "A", "outer", "Q")
	must(t, local.Sync(t.Context(), a))
	l.pending(t, b)
	if st, err := os.Stat(filepath.Join(l.dir, "B", "outer/inner")); err != nil || !st.IsDir() {
		t.Fatalf("child directory was removed: %v", err)
	}
	l.assertBase(t, "B", "outer/inner/doc", "", 2)
	l.assertBase(t, "B", "outer", "", 0)
	l.assertTrash(t, "B", "outer/inner/doc", "P", 2)
	l.assertHistory(t, g, "client-a:outer/inner/doc:1:applied", "client-a:outer/inner/doc:2:applied", "client-a:outer:1:applied")
}

func TestP3EditAfterResolutionCommitIsPreserved(t *testing.T) {
	l := newLab(t)
	g := l.conflictingEdits(t)
	seen := make(chan int, 2)
	proxy := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			l.h.ServeHTTP(w, r)
			return
		}
		capture := httptest.NewRecorder()
		l.h.ServeHTTP(capture, r)
		seen <- capture.Code
		conn, _, err := w.(http.Hijacker).Hijack()
		if err == nil {
			conn.Close()
		}
	}))
	defer proxy.Close()
	remote, err := protocol.NewClient(proxy.Listener.Addr().String(), l.provision.Clients[1].Token)
	must(t, err)
	b := l.req(t, "B", 1)
	missingAck := b
	missingAck.Remote = remote
	if err := local.Resolve(t.Context(), missingAck, local.ResolveRequest{Path: notes, Generation: g, Revision: 2, Choice: "hub"}); err == nil {
		t.Fatal("missing reply appeared complete")
	}
	if len(seen) != 2 || <-seen != 200 || <-seen != 200 {
		t.Fatal("resolution was not accepted and replayed")
	}
	l.write(t, "B", notes, "S")
	l.pending(t, b)
	l.assertBody(t, "B/"+notes, "S")
	l.assertBase(t, "B", notes, "Q", 2)
	newConflict := l.held(t, notes, g, 2, "file", "S", "file", "Q", 2)
	if newConflict.Reason != "post_resolution_edit" {
		t.Fatalf("missing new conflict after accepted resolution: %+v", newConflict)
	}
	for _, previous := range l.status(t, "B").Conflicts {
		if previous.State == "resolved" {
			l.assertCandidate(t, "B", previous.Local, "R")
			l.assertCandidate(t, "B", previous.Hub[0], "Q")
		}
	}
	l.assertHistory(t, g, "client-a:notes.txt:1:applied", "client-a:notes.txt:2:applied", "client-b:notes.txt:2:unchanged")
}
