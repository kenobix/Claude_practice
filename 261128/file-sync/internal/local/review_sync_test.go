package local

import (
	"database/sql"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

// These files are existing local edits already held by registration. They
// isolate the receiver's limit from the hub's independent live-file limit.
func holdLocalFiles(t *testing.T, n networkLab, count int) {
	t.Helper()
	for i := 0; i < count; i++ {
		writeAt(t, n.b, fmt.Sprintf("local-%04d", i), nil)
	}
	must(t, withDeleteEngine(t.Context(), n.lab, func(e *engine) error {
		return e.db.write(e.ctx, func(tx *sql.Tx) error {
			for i := 0; i < count; i++ {
				if err := putHold(e.ctx, tx, fmt.Sprintf("local-%04d", i), "registration_local_edit"); err != nil {
					return err
				}
			}
			return nil
		})
	}))
}

func TestReviewIncomingFileCapacity(t *testing.T) {
	for _, scenario := range []string{"full", "one_free", "pending_restart"} {
		t.Run(scenario, func(t *testing.T) {
			n := newNetworkLab(t)
			a, b := n.req(t, "A"), n.req(t, "B")
			q, qd := fixture(t, "Q")
			r, rd := fixture(t, "R")
			writeAt(t, n.a, "new-a", q)
			writeAt(t, n.a, notes, r)
			if scenario == "one_free" {
				writeAt(t, n.a, "new-b", q)
			}
			must(t, Sync(t.Context(), a))
			history, err := a.Remote.Manifest(t.Context())
			must(t, err)
			count := safefs.MaxFiles - 1
			if scenario == "one_free" {
				count--
			}
			if scenario == "pending_restart" {
				killAtBoundary(t, n.lab, "p4-incoming-two-bytes", "FILE_SYNC_TEST_HUB="+n.addr)
				p, pd := fixture(t, "P")
				assertFile(t, n.b, notes, p, pd)
				s := n.status(t)
				assertBase(t, s, notes, 1, pd)
				if len(s.Plans) != 2 || s.Plans[0].Path != "new-a" || s.Plans[0].Status != "pending" {
					t.Fatalf("missing stopped install: %+v", s.Plans)
				}
				partial, err := os.ReadFile(n.stage(s.Plans[0].Incoming()))
				must(t, err)
				if string(partial) != string(q[:2]) {
					t.Fatalf("unexpected partial body: %q", partial)
				}
			}
			holdLocalFiles(t, n, count)

			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("expected capacity hold: %v", err)
			}
			s := n.status(t)
			assertFile(t, n.b, notes, r, rd)
			assertBase(t, s, notes, 2, rd)
			firstHeld := "new-a"
			if scenario == "one_free" {
				assertFile(t, n.b, "new-a", q, qd)
				assertBase(t, s, "new-a", 1, qd)
				firstHeld = "new-b"
			}
			assertAbsent(t, n.b, firstHeld)
			assertBase(t, s, firstHeld, 0, safefs.Digest{})
			if s.Holds[firstHeld] != "incoming_file_limit" {
				t.Fatalf("wrong hold for %q: %q", firstHeld, s.Holds[firstHeld])
			}
			for _, plan := range s.Plans {
				if plan.Path == firstHeld && (scenario != "pending_restart" || plan.Status != "pending") {
					t.Fatalf("unexpected plan for held install: %+v", plan)
				}
			}
			files, err := Scan(n.volume, "B")
			must(t, err)
			if len(files) != safefs.MaxFiles {
				t.Fatalf("file count %d", len(files))
			}
			_, empty := fixture(t, "E")
			assertFile(t, n.b, "local-0001", nil, empty)
			must(t, os.Remove(filepath.Join(n.b, "local-0000")))
			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("expected only the preexisting registration holds: %v", err)
			}
			s = n.status(t)
			assertFile(t, n.b, firstHeld, q, qd)
			assertBase(t, s, firstHeld, 1, qd)
			if _, present := s.Holds[firstHeld]; present {
				t.Fatalf("stale capacity hold: %q", s.Holds[firstHeld])
			}
			end, err := a.Remote.Manifest(t.Context())
			must(t, err)
			assertSameHistory(t, n, history, end, len(history.Entries)+1)
		})
	}
}

func assertSameHistory(t *testing.T, n networkLab, before, after protocol.Manifest, want int) {
	t.Helper()
	if !reflect.DeepEqual(before, after) {
		t.Fatalf("receive changed hub state: %+v -> %+v", before, after)
	}
	name := filepath.Join(n.volume, "hub", safefs.Management, "state", safefs.StateDB)
	db, err := sql.Open("sqlite3", "file:"+name+"?mode=ro")
	must(t, err)
	defer db.Close()
	var receipts int
	must(t, db.QueryRow("SELECT count(*) FROM receipts").Scan(&receipts))
	if receipts != want {
		t.Fatalf("hub receipts %d, want %d", receipts, want)
	}
}

func TestReviewIncomingDirectoryCapacity(t *testing.T) {
	n := newNetworkLab(t)
	a, b := n.req(t, "A"), n.req(t, "B")
	q, qd := fixture(t, "Q")
	r, rd := fixture(t, "R")
	for i := 0; i < safefs.MaxDirs-2; i++ {
		must(t, os.Mkdir(filepath.Join(n.b, fmt.Sprintf("empty-%05d", i)), 0700))
	}
	for _, path := range []string{"new-a/a", "new-a/b", "new-b/a"} {
		must(t, os.MkdirAll(filepath.Dir(filepath.Join(n.a, path)), 0700))
		writeAt(t, n.a, path, q)
	}
	writeAt(t, n.a, notes, r)
	must(t, Sync(t.Context(), a))
	if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
		t.Fatalf("expected directory capacity hold: %v", err)
	}
	s := n.status(t)
	assertFile(t, n.b, notes, r, rd)
	assertBase(t, s, notes, 2, rd)
	for _, path := range []string{"new-a/a", "new-a/b"} {
		assertFile(t, n.b, path, q, qd)
		assertBase(t, s, path, 1, qd)
	}
	assertAbsent(t, n.b, "new-b")
	if s.Holds["new-b/a"] != "incoming_directory_limit" {
		t.Fatalf("unexpected holds: %v", s.Holds)
	}
	must(t, os.Remove(filepath.Join(n.b, "empty-00000")))
	must(t, Sync(t.Context(), b))
	assertFile(t, n.b, "new-b/a", q, qd)
	assertBase(t, n.status(t), "new-b/a", 1, qd)
}

func TestReviewStructuralDependencyAfterDeleteHoldOrRestart(t *testing.T) {
	for _, scenario := range []string{"no_trash_capacity", "restart_backed_up", "restart_unlinked"} {
		t.Run(scenario, func(t *testing.T) {
			n := newNetworkLab(t)
			a, b := n.req(t, "A"), n.req(t, "B")
			n.publish(t, "")
			p, pd := fixture(t, "P")
			q, qd := fixture(t, "Q")
			must(t, os.Mkdir(filepath.Join(n.a, notes), 0700))
			child := notes + "/child"
			writeAt(t, n.a, child, q)
			must(t, Sync(t.Context(), a))
			history, err := a.Remote.Manifest(t.Context())
			must(t, err)
			if scenario == "no_trash_capacity" {
				reserveManagedCopies(t, n)
				if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
					t.Fatalf("expected deletion capacity hold: %v", err)
				}
				s := n.status(t)
				if s.Holds[notes] != "apply_pending" || s.Holds[child] != "namespace_conflict" || len(s.Plans) != 1 {
					t.Fatalf("dependency executed despite blocked deletion: %v, %+v", s.Holds, s.Plans)
				}
				assertFile(t, n.b, notes, p, pd)
				removeReservedCopies(t, n)
			} else {
				point := "delete-backed-up"
				if scenario == "restart_unlinked" {
					point = "unlinked"
				}
				killAtBoundary(t, n.lab, "p4-"+point, "FILE_SYNC_TEST_HUB="+n.addr)
				s := n.status(t)
				pending := findPlan(t, s, 2)
				if pending.Status != "pending" || !pending.BackedUp {
					t.Fatalf("unexpected persisted deletion: %+v", pending)
				}
				assertFile(t, n.stage(""), pending.Backup(), p, pd)
				if point == "unlinked" {
					assertAbsent(t, n.b, notes)
				} else {
					assertFile(t, n.b, notes, p, pd)
				}
			}
			assertBase(t, n.status(t), notes, 1, pd)
			must(t, Sync(t.Context(), b))
			s := n.status(t)
			assertBase(t, s, notes, 2, safefs.Digest{})
			assertBase(t, s, child, 1, qd)
			assertFile(t, n.b, child, q, qd)
			plan := findPlan(t, s, 2)
			if plan.Status != "complete" || !plan.BackedUp {
				t.Fatalf("deletion did not complete: %+v", plan)
			}
			assertFile(t, n.stage(""), plan.Backup(), p, pd)
			end, err := a.Remote.Manifest(t.Context())
			must(t, err)
			assertSameHistory(t, n, history, end, 3)
		})
	}
}

func reserveManagedCopies(t *testing.T, n networkLab) {
	t.Helper()
	must(t, withDeleteEngine(t.Context(), n.lab, func(e *engine) error {
		return e.db.write(e.ctx, func(tx *sql.Tx) error {
			for i := 0; i < safefs.MaxManagedBytes/safefs.MaxSize; i++ {
				name := fmt.Sprintf("%032x.candidate", i+1)
				f, _, err := e.db.manager.Stage.Create(name)
				if err != nil {
					return err
				}
				if err := errors.Join(f.Truncate(safefs.MaxSize), f.Close()); err != nil {
					return err
				}
				if _, err := tx.Exec("INSERT INTO saved_copies VALUES (?,?)", name, safefs.MaxSize); err != nil {
					return err
				}
			}
			return nil
		})
	}))
}

func removeReservedCopies(t *testing.T, n networkLab) {
	t.Helper()
	must(t, withDeleteEngine(t.Context(), n.lab, func(e *engine) error {
		return e.db.write(e.ctx, func(tx *sql.Tx) error {
			names, err := os.ReadDir(n.stage(""))
			if err != nil {
				return err
			}
			for _, name := range names {
				if strings.HasSuffix(name.Name(), ".candidate") {
					if err := os.Remove(n.stage(name.Name())); err != nil {
						return err
					}
				}
			}
			_, err = tx.Exec("DELETE FROM saved_copies")
			return err
		})
	}))
}

func TestReviewDeferredSendDoesNotBlockIndependentReceives(t *testing.T) {
	for _, code := range []string{"storage_limit", "storage_full"} {
		t.Run(code, func(t *testing.T) {
			n := newNetworkLab(t)
			a, b := n.req(t, "A"), n.req(t, "B")
			r, rd := fixture(t, "R")
			q, qd := fixture(t, "Q")
			const localOnly = "local-only"
			writeAt(t, n.b, localOnly, q)
			n.publish(t, "R")
			var hold atomic.Bool
			hold.Store(true)
			proxy := httputil.NewSingleHostReverseProxy(&url.URL{Scheme: "http", Host: n.addr})
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost && hold.Load() {
					w.Header().Set("Content-Type", "application/json")
					w.WriteHeader(http.StatusInsufficientStorage)
					_, _ = fmt.Fprintf(w, "{\"error\":%q}", code)
					return
				}
				proxy.ServeHTTP(w, r)
			}))
			defer server.Close()
			remote, err := protocol.NewClient(server.Listener.Addr().String(), localTestToken(2))
			must(t, err)
			b.Remote = remote
			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("b did not report held send: %v", err)
			}
			s := n.status(t)
			if s.Holds[localOnly] != "send_capacity" || len(s.Outbox) != 1 {
				t.Fatalf("send was not retained: %v %+v", s.Holds, s.Outbox)
			}
			op := s.Outbox[0].Change.Operation
			assertFile(t, n.b, localOnly, q, qd)
			assertFile(t, n.b, notes, r, rd)
			assertBase(t, s, notes, 2, rd)

			writeAt(t, n.a, notes, q)
			must(t, Sync(t.Context(), a))
			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("capacity on outbox replay stopped receive: %v", err)
			}
			s = n.status(t)
			assertFile(t, n.b, notes, q, qd)
			assertBase(t, s, notes, 3, qd)
			if len(s.Outbox) != 1 || s.Outbox[0].Change.Operation != op {
				t.Fatalf("outbox replay changed the saved intent: %+v", s.Outbox)
			}
			hold.Store(false)
			must(t, Sync(t.Context(), b))
			m, err := a.Remote.Manifest(t.Context())
			must(t, err)
			if entry := hubPaths(m)[localOnly]; entry.Operation != op || entry.Revision != 1 || digestOf(entry) != qd {
				t.Fatalf("wrong receipt after replay: %+v", entry)
			}
		})
	}
}
