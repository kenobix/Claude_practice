package local

import (
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"reflect"
	"sync/atomic"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestReviewResolutionCapacity(t *testing.T) {
	for _, scenario := range []struct {
		name, method, code string
		stale              bool
	}{
		{name: "post_capacity_then_replay", method: http.MethodPost, code: "storage_limit"},
		{name: "put_capacity_then_replay", method: http.MethodPut, code: "storage_full"},
		{name: "post_capacity_then_reselect", method: http.MethodPost, code: "storage_limit", stale: true},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			n := newNetworkLab(t)
			a, b := n.req(t, "A"), n.req(t, "B")
			p, pd := fixture(t, "P")
			q, qd := fixture(t, "Q")
			r, rd := fixture(t, "R")
			s, sd := fixture(t, "S")
			const separate = "separate"
			writeAt(t, n.a, separate, p)
			must(t, Sync(t.Context(), a))
			must(t, Sync(t.Context(), b))
			writeAt(t, n.b, notes, r)
			must(t, os.Remove(filepath.Join(n.a, notes)))
			writeAt(t, n.a, separate, s)
			must(t, Sync(t.Context(), a))
			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("expected edit versus delete conflict: %v", err)
			}
			initial := n.status(t)
			assertBase(t, initial, separate, 2, sd)
			assertBase(t, initial, notes, 1, pd)
			conflict := findConflict(initial.Conflicts, notes)
			if conflict == nil || !conflict.Saved || conflict.latest().Kind != "deleted" ||
				conflict.latest().Revision != 2 || conflict.Local.Digest != rd {
				t.Fatalf("wrong conflict: %+v", conflict)
			}

			if scenario.method == http.MethodPost {
				must(t, a.Remote.Put(t.Context(), conflict.Generation, rd, r))
			}
			var blocked atomic.Bool
			blocked.Store(true)
			attempts := make(chan protocol.Change, 8)
			b.Remote = reviewProxy(t, n, func(w http.ResponseWriter, req *http.Request) bool {
				if req.Method != scenario.method || !blocked.Load() {
					return false
				}
				if req.Method == http.MethodPost {
					var change protocol.Change
					if err := json.NewDecoder(req.Body).Decode(&change); err != nil {
						t.Errorf("decode resolution: %v", err)
						return false
					}
					attempts <- change
				}
				w.Header().Set("Content-Type", "application/json")
				w.WriteHeader(http.StatusInsufficientStorage)
				_, _ = fmt.Fprintf(w, "{\"error\":%q}", scenario.code)
				return true
			})
			choice := ResolveRequest{Path: notes, Generation: conflict.Generation, Revision: 2, Choice: "local"}
			err := Resolve(t.Context(), b, choice)
			if !errors.Is(err, ErrConflict) || !storageFull(err) {
				t.Fatalf("expected resolution capacity hold: %v", err)
			}
			waiting := findConflict(n.status(t).Conflicts, notes)
			if waiting == nil || waiting.Resolution == nil || waiting.Resolution.State != "ready" || waiting.Resolution.Receipt != nil {
				t.Fatalf("capacity rejection was treated as an acceptance: %+v", waiting)
			}
			op := waiting.Resolution.Change.Operation
			writeAt(t, n.a, separate, q)
			must(t, Sync(t.Context(), a))
			history, err := a.Remote.Manifest(t.Context())
			must(t, err)
			if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
				t.Fatalf("retry should report only a hold: %v", err)
			}
			state := n.status(t)
			retry := findConflict(state.Conflicts, notes)
			if !reflect.DeepEqual(waiting, retry) || state.Holds[notes] != "resolution_capacity" {
				t.Fatalf("saved intent or hold changed: %+v -> %+v, holds %v", waiting, retry, state.Holds)
			}
			if scenario.method == http.MethodPost {
				for range 2 {
					select {
					case change := <-attempts:
						if !reflect.DeepEqual(change, retry.Resolution.Change) {
							t.Fatalf("resolution request changed: %+v", change)
						}
					default:
						t.Fatal("missing capacity-rejected POST")
					}
				}
			}
			assertFile(t, n.stage(""), retry.Resolution.Selected.Copy, r, rd)
			assertFile(t, n.b, notes, r, rd)
			assertBase(t, state, notes, 1, pd)
			assertFile(t, n.b, separate, q, qd)
			assertBase(t, state, separate, 3, qd)
			end, err := a.Remote.Manifest(t.Context())
			must(t, err)
			assertSameHistory(t, n, history, end, 5)
			assertResolutionReceipt(t, n, op, 0)

			if scenario.stale {
				writeAt(t, n.a, notes, q)
				must(t, Sync(t.Context(), a))
				// Leave the 507 injection in place until the hub advances, then
				// let the same pending request reach the actual CAS check.
				blocked.Store(false)
				if err := Sync(t.Context(), b); !errors.Is(err, ErrConflict) {
					t.Fatalf("advancing the hub must require a new choice: %v", err)
				}
				state = n.status(t)
				stale := findConflict(state.Conflicts, notes)
				if stale == nil || stale.State != "pending" || stale.Resolution.State != "stale" ||
					stale.Resolution.Change.Operation != op || stale.Resolution.Receipt != nil ||
					stale.latest().Revision != 3 || stale.latest().Digest != qd || !stale.latest().Saved ||
					state.Holds[notes] == "resolution_capacity" {
					t.Fatalf("wrong stale choice or hold: %+v, %v", stale, state.Holds)
				}
				assertFile(t, n.b, notes, r, rd)
				assertBase(t, state, notes, 1, pd)
				assertFile(t, n.stage(""), stale.Resolution.Selected.Copy, r, rd)
				assertFile(t, n.stage(""), stale.latest().Copy, q, qd)
				assertResolutionReceipt(t, n, op, 0)
				if err := Resolve(t.Context(), b, choice); !errors.Is(err, ErrConflict) {
					t.Fatalf("old displayed revision unexpectedly resolved: %v", err)
				}
				choice.Revision = 3
				must(t, Resolve(t.Context(), b, choice))
				done := findConflict(n.status(t).Conflicts, notes)
				if done == nil || done.Resolution.Change.Operation == op {
					t.Fatalf("new choice reused stale operation: %+v", done)
				}
				assertResolutionReceipt(t, n, done.Resolution.Change.Operation, 1)
				assertBase(t, n.status(t), notes, 4, rd)
				return
			}

			blocked.Store(false)
			must(t, Sync(t.Context(), b))
			must(t, Sync(t.Context(), b))
			state = n.status(t)
			done := findConflict(state.Conflicts, notes)
			if len(state.Holds) != 0 || done == nil || done.State != "resolved" ||
				done.Resolution.Receipt == nil || done.Resolution.State != "complete" ||
				done.Resolution.Change.Operation != op {
				t.Fatalf("wrong completed resolution: %+v, holds %v", done, state.Holds)
			}
			assertFile(t, n.b, notes, r, rd)
			assertBase(t, state, notes, 3, rd)
			assertFile(t, n.stage(""), done.Resolution.Selected.Copy, r, rd)
			end, err = a.Remote.Manifest(t.Context())
			must(t, err)
			entry := hubPaths(end)[notes]
			if entry.Operation != op || entry.Revision != 3 || digestOf(entry) != rd {
				t.Fatalf("wrong hub entry: %+v", entry)
			}
			assertResolutionReceipt(t, n, op, 1)
			assertSameHistory(t, n, end, end, 6)
		})
	}
}

func reviewProxy(t *testing.T, n networkLab, intercept func(http.ResponseWriter, *http.Request) bool) *protocol.Client {
	t.Helper()
	proxy := httputil.NewSingleHostReverseProxy(&url.URL{Scheme: "http", Host: n.addr})
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !intercept(w, r) {
			proxy.ServeHTTP(w, r)
		}
	}))
	t.Cleanup(server.Close)
	client, err := protocol.NewClient(server.Listener.Addr().String(), localTestToken(2))
	must(t, err)
	return client
}

func assertResolutionReceipt(t *testing.T, n networkLab, op string, want int) {
	t.Helper()
	name := filepath.Join(n.volume, "hub", safefs.Management, "state", safefs.StateDB)
	db, err := sql.Open("sqlite3", "file:"+name+"?mode=ro")
	must(t, err)
	defer db.Close()
	var receipts int
	must(t, db.QueryRow("SELECT count(*) FROM receipts WHERE client='client-b' AND op=?", op).Scan(&receipts))
	if receipts != want {
		t.Fatalf("receipts for %s = %d, want %d", op, receipts, want)
	}
}

func TestReviewCASRecordsStructuralGroup(t *testing.T) {
	n := newNetworkLab(t)
	a, b := n.req(t, "A"), n.req(t, "B")
	p, pd := fixture(t, "P")
	q, qd := fixture(t, "Q")
	r, rd := fixture(t, "R")
	const parent, old, local, separate = "x", "x/old", "x/local", "separate"
	must(t, os.Mkdir(filepath.Join(n.a, parent), 0700))
	writeAt(t, n.a, old, p)
	writeAt(t, n.a, separate, p)
	must(t, Sync(t.Context(), a))
	must(t, Sync(t.Context(), b))
	previous := n.status(t)
	writeAt(t, n.b, local, r)
	type request struct {
		change protocol.Change
		result chan error
	}
	posted := make(chan request, 2)
	direct := b.Remote
	b.Remote = reviewProxy(t, n, func(w http.ResponseWriter, r *http.Request) bool {
		if r.Method != http.MethodPost {
			return false
		}
		// Observe the request and advance A before forwarding B's POST.
		var change protocol.Change
		if err := json.NewDecoder(r.Body).Decode(&change); err != nil {
			t.Errorf("decode outgoing POST: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return true
		}
		result := make(chan error, 1)
		select {
		case posted <- request{change, result}:
		case <-r.Context().Done():
			return true
		}
		select {
		case err := <-result:
			m, manifestErr := a.Remote.Manifest(r.Context())
			if err != nil || manifestErr != nil {
				t.Errorf("advance A: %v; manifest: %v", err, manifestErr)
				w.WriteHeader(http.StatusInternalServerError)
				return true
			}
			body, marshalErr := json.Marshal(change)
			if marshalErr != nil {
				t.Errorf("encode outgoing POST: %v", marshalErr)
				w.WriteHeader(http.StatusInternalServerError)
				return true
			}
			_, postErr := direct.Post(r.Context(), change, body)
			var problem *protocol.Problem
			if !errors.As(postErr, &problem) || problem.Status != 409 || problem.Code != "namespace_conflict" ||
				hubPaths(m)[parent].Revision != 1 {
				t.Errorf("unexpected POST outcome: %v, manifest %+v", postErr, m)
				w.WriteHeader(http.StatusInternalServerError)
				return true
			}
			w.Header().Set("Content-Type", "application/json")
			w.WriteHeader(problem.Status)
			_, _ = fmt.Fprintf(w, "{\"error\":%q}", problem.Code)
		case <-r.Context().Done():
		}
		return true
	})
	syncResult := make(chan error, 1)
	go func() { syncResult <- Sync(t.Context(), b) }()
	var first request
	select {
	case first = <-posted:
	case err := <-syncResult:
		t.Fatalf("sync did not attempt original POST: %v", err)
	}
	if first.change.Path != local {
		t.Errorf("unexpected outgoing path: %+v", first.change)
	}
	must(t, os.Remove(filepath.Join(n.a, old)))
	must(t, os.Remove(filepath.Join(n.a, parent)))
	must(t, Sync(t.Context(), a))
	writeAt(t, n.a, parent, q)
	writeAt(t, n.a, separate, q)
	must(t, Sync(t.Context(), a))
	first.result <- nil
	select {
	case another := <-posted:
		t.Fatalf("unexpected recomparison POST: %+v", another.change)
	case err := <-syncResult:
		if !errors.Is(err, ErrConflict) {
			t.Fatalf("CAS structural collision should be held: %v", err)
		}
	}
	state := n.status(t)
	want := map[string]string{parent: "namespace_conflict", old: "namespace_conflict", local: "namespace_conflict"}
	if !reflect.DeepEqual(state.Holds, want) || !reflect.DeepEqual(state.Bases, previous.Bases) ||
		!reflect.DeepEqual(state.Plans, previous.Plans) || len(state.Outbox) != 1 ||
		state.Outbox[0].Change.Operation != first.change.Operation || state.Outbox[0].Status != "rejected" {
		t.Fatalf("first CAS sync changed history or missed related holds: %+v", state)
	}
	assertFile(t, n.b, old, p, pd)
	assertFile(t, n.b, local, r, rd)
	assertFile(t, n.b, separate, p, pd)
	assertFile(t, n.stage(""), state.Outbox[0].name(), r, rd)
	assertResolutionReceipt(t, n, first.change.Operation, 0)

	// Move the user's blocker out of the synchronized tree. The regular
	// comparison can now delete the old child and install its parent.
	kept := filepath.Join(n.volume, "kept-local")
	must(t, os.Rename(filepath.Join(n.b, local), kept))
	b = n.req(t, "B")
	must(t, Sync(t.Context(), b))
	state = n.status(t)
	if len(state.Holds) != 0 || len(state.Outbox) != 0 {
		t.Fatalf("organizing paths did not clear holds: %+v", state)
	}
	assertFile(t, n.volume, "kept-local", r, rd)
	assertFile(t, n.b, parent, q, qd)
	assertBase(t, state, parent, 1, qd)
	assertBase(t, state, old, 2, safefs.Digest{})
	assertFile(t, n.b, separate, q, qd)
	assertBase(t, state, separate, 2, qd)
	m, err := a.Remote.Manifest(t.Context())
	must(t, err)
	assertSameHistory(t, n, m, m, 6)
}
