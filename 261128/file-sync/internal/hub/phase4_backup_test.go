package hub

import (
	"bytes"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"slices"
	"strconv"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/local"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func lossProxy(t *testing.T, l *lab, client int, process bool) (local.NetworkRequest, <-chan history) {
	t.Helper()
	dropped := make(chan history, 4)
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodPost {
			l.h.ServeHTTP(w, r)
			return
		}
		raw, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			return
		}
		var event history
		if err := json.Unmarshal(raw, &event.Change); err != nil {
			t.Error(err)
			return
		}
		if process {
			r.Body = io.NopCloser(bytes.NewReader(raw))
			response := httptest.NewRecorder()
			l.h.ServeHTTP(response, r)
			if response.Code != 200 {
				t.Errorf("lost response was HTTP %d: %s", response.Code, response.Body)
				return
			}
			if err := json.Unmarshal(response.Body.Bytes(), &event.Receipt); err != nil {
				t.Error(err)
				return
			}
		}
		dropped <- event
		conn, _, err := w.(http.Hijacker).Hijack()
		if err != nil {
			t.Error(err)
			return
		}
		conn.Close()
	}))
	t.Cleanup(server.Close)
	remote, err := protocol.NewClient(server.Listener.Addr().String(), l.provision.Clients[client].Token)
	must(t, err)
	return local.NetworkRequest{Volume: l.dir, Root: []string{"A", "B"}[client], Remote: remote}, dropped
}

func TestP4_04_RepeatOldReceiptAfterNewHead(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	w := loadSamples(t).Cases["P4-04"]
	l.write(t, "A", w.Path, "Q")
	lost, observed := lossProxy(t, l, 0, true)
	if err := local.Sync(t.Context(), lost); err == nil {
		t.Fatal("lost HTTP response classified as success")
	}
	if len(observed) != 2 {
		t.Fatalf("dropped responses %d", len(observed))
	}
	first, repeat := <-observed, <-observed
	outbox := l.status(t, "A").Outbox
	if len(outbox) != 1 || outbox[0].Change.Operation != first.Change.Operation || repeat.Receipt.Operation != first.Receipt.Operation ||
		first.Receipt.Entry.Revision != 2 || repeat.Receipt.Entry.Revision != 2 {
		t.Fatalf("outbox or lost receipt mismatch %+v %+v", first, repeat)
	}
	l.assertBase(t, "A", w.Path, "P", 1)
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	l.file(t, "B", w.Path, "Q", 2)
	l.write(t, "B", w.Path, w.B)
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	l.hubFile(t, w.Path, w.Content, "client-b", w.Revision)
	code, data := l.post(t, first.Change, 0)
	want, err := json.Marshal(first.Receipt)
	must(t, err)
	if code != 200 || !bytes.Equal(bytes.TrimSpace(data), want) {
		t.Fatalf("replay %d %s want %s", code, data, want)
	}
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.file(t, "A", w.Path, w.A, w.ARev)
	l.file(t, "B", w.Path, w.B, w.BRev)
	l.clean(t, g)
	for _, id := range []string{"P", "Q", "R"} {
		l.checkBlob(t, id, g)
	}
	var historyLines []string
	for _, h := range l.history(t) {
		alias := "a1"
		if h.Change.Operation == first.Change.Operation {
			alias = "a2"
		} else if h.Change.Client == "client-b" {
			alias = "b2"
		}
		historyLines = append(historyLines, alias+":"+strconv.FormatInt(h.Receipt.Entry.Revision, 10)+":"+h.Receipt.Result)
	}
	if !slices.Equal(historyLines, w.Receipts) {
		t.Fatalf("history %v want %v", historyLines, w.Receipts)
	}
}

func TestP4_12_RestoreOldGeneration(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	spec := loadSamples(t).Cases["P4-12"]
	first := l.history(t)[0]
	unused, sd := sample(t, "S")
	must(t, l.req(t, "A", 0).Remote.Put(t.Context(), g, sd, unused))
	if _, err := Backup(t.Context(), l.dir, "hub", "backup"); err == nil {
		t.Fatal("backup accepted a live hub")
	}
	l.stop()
	b, err := Backup(t.Context(), l.dir, "hub", "backup")
	must(t, err)
	if len(b.Spaces) != 1 || b.Spaces[0].Generation != g || len(b.Blobs) != 2 {
		t.Fatalf("backup inventory %+v", b)
	}
	l.start(t)
	l.write(t, "A", spec.Path, "Q")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	must(t, local.Sync(t.Context(), l.req(t, "B", 1)))
	l.file(t, "B", spec.Path, "Q", 2)
	l.write(t, "A", spec.Path, "R")
	req, dropped := lossProxy(t, l, 0, false)
	if err := local.Sync(t.Context(), req); err == nil || len(dropped) != 2 {
		t.Fatalf("interrupted R POST: %v", err)
	}
	oldA := l.status(t, "A")
	if len(oldA.Outbox) != 1 || oldA.Outbox[0].Status != "ready" || oldA.Outbox[0].Change.Generation != g {
		t.Fatalf("old outbox %+v", oldA)
	}
	old := oldA.Outbox[0].Change
	oldFile := filepath.Join("A", safefs.Management, "stage", old.Operation+".outgoing")
	l.assertBody(t, oldFile, "R")
	l.hubFile(t, spec.Path, "Q", "client-a", 2)
	if len(l.history(t)) != 2 {
		t.Fatal("unconfirmed R unexpectedly committed")
	}

	current := l.provision
	current.Clients[0].Token = token(3)
	current.Clients[1].Token = token(4)
	restored, err := Restore(t.Context(), l.dir, "backup", "restored", current)
	must(t, err)
	if restored.Generation == g || !protocol.IsHex(restored.Generation, 32) {
		t.Fatalf("restore did not rotate generation: %+v", restored)
	}
	h, err := Open(t.Context(), l.dir, "restored")
	must(t, err)
	server := httptest.NewServer(h)
	defer h.Close()
	defer server.Close()
	fresh := &lab{dir: l.dir, provision: current, h: h, server: server}
	for _, original := range l.provision.Clients {
		if _, ok := h.authenticate(original.Token); ok {
			t.Fatal("obsolete token activated")
		}
		request, err := http.NewRequestWithContext(t.Context(), "GET", server.URL+"/v1/manifest", nil)
		must(t, err)
		request.Header.Set("Authorization", "Bearer "+original.Token)
		resp, err := server.Client().Do(request)
		must(t, err)
		body, err := io.ReadAll(resp.Body)
		must(t, err)
		resp.Body.Close()
		assertCode(t, resp.StatusCode, body, 401, "unauthorized")
	}
	for _, c := range []protocol.Change{first.Change, old} {
		status, reply := fresh.post(t, c, 0)
		assertCode(t, status, reply, 409, "generation_mismatch")
	}
	// Keep the unreceived operation ID, with an expected version equal to the restored row.
	old.Base = spec.Revision
	status, reply := fresh.post(t, old, 0)
	assertCode(t, status, reply, 409, "generation_mismatch")
	fresh.pending(t, fresh.req(t, "A", 0))
	fresh.pending(t, fresh.req(t, "B", 1))
	l.assertBase(t, "A", spec.Path, "Q", 2)
	l.assertBody(t, "A/"+spec.Path, spec.A)
	l.file(t, "B", spec.Path, spec.B, spec.BRev)
	ra := fresh.req(t, "A", 0)
	preview, err := local.Reconcile(t.Context(), ra, "")
	must(t, err)
	if preview.Prior == nil || len(preview.Prior.Outbox) != 1 || preview.Prior.Outbox[0].Change.Operation != old.Operation {
		t.Fatalf("old outbox missing from approval %+v", preview)
	}
	if _, err := local.Reconcile(t.Context(), ra, preview.Approval); !errors.Is(err, local.ErrConflict) {
		t.Fatalf("re-registration %v", err)
	}
	after := l.status(t, "A")
	if len(after.Conflicts) != 1 || !after.Conflicts[0].Saved || len(after.Outbox) != 0 || after.Registration.Generation != restored.Generation ||
		after.Bases[spec.Path].Revision != 0 || after.Conflicts[0].Hub[0].Revision != spec.Revision {
		t.Fatalf("reconciled state %+v", after)
	}
	fresh.assertCandidate(t, "A", after.Conflicts[0].Local, spec.A)
	fresh.assertCandidate(t, "A", after.Conflicts[0].Hub[0], spec.Content)
	fresh.hubFile(t, spec.Path, spec.Content, "client-a", spec.Revision)
	history := fresh.history(t)
	if len(spec.Receipts) != 1 || len(history) != 1 || history[0].Change.Operation != first.Change.Operation ||
		history[0].Receipt.Generation != g || history[0].Receipt.Entry.Revision != 1 || history[0].Receipt.Result != "applied" {
		t.Fatalf("restored history %+v", history)
	}
	fresh.checkBlob(t, "S", restored.Generation)
	l.assertBody(t, oldFile, "R")
	l.assertBody(t, filepath.Join("backup", "blobs", blobName(l.provision.Space, sd.Hash)), "S")
	l.hubFile(t, spec.Path, "Q", "client-a", 2)
	l.checkBlob(t, "R", g)
	l.file(t, "B", spec.Path, spec.B, spec.BRev)
	assertArchivedOutbox(t, l, old.Operation)
	t.Logf("G=%s G2=%s revision=%d old receipts=1, new receipts=0; old Q and outbox R held", g, restored.Generation, spec.Revision)
}

func assertArchivedOutbox(t *testing.T, l *lab, operation string) {
	t.Helper()
	root := filepath.Join(l.dir, "A", safefs.Management)
	entries, err := os.ReadDir(root)
	must(t, err)
	for _, e := range entries {
		if !safefs.ReconcileDirName(e.Name()) {
			continue
		}
		db, err := sql.Open("sqlite3", "file:"+filepath.Join(root, e.Name(), safefs.StateDB)+"?mode=ro")
		must(t, err)
		var actual string
		err = db.QueryRow("SELECT id FROM outbox").Scan(&actual)
		must(t, db.Close())
		must(t, err)
		if actual == operation {
			return
		}
	}
	t.Fatal("old outbox DB not retained")
}

func TestP4_12_BackupRejectsDamageAndExistingDestination(t *testing.T) {
	for _, fault := range []string{"manifest", "blob", "database", "existing-destination", "client-id", "auth-swap"} {
		t.Run(fault, func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			p, pd := sample(t, "P")
			l.stop()
			_, err := Backup(t.Context(), l.dir, "hub", "backup")
			must(t, err)
			backupRoot := filepath.Join(l.dir, "backup")
			destination := filepath.Join(l.dir, "restored")
			config := l.provision
			var changed string
			switch fault {
			case "manifest":
				changed = filepath.Join(backupRoot, backupManifest)
			case "blob":
				changed = filepath.Join(backupRoot, "blobs", blobName(l.provision.Space, pd.Hash))
			case "database":
				changed = filepath.Join(backupRoot, safefs.StateDB)
			case "existing-destination":
				must(t, os.Mkdir(destination, 0700))
				must(t, os.WriteFile(filepath.Join(destination, "sentinel"), p, 0600))
			case "client-id":
				config.Clients[0].ID = "unexpected"
			case "auth-swap":
				config.Clients[0].Token, config.Clients[1].Token = config.Clients[1].Token, config.Clients[0].Token
			}
			if changed != "" {
				must(t, os.WriteFile(changed, []byte("bad"), 0600))
			}
			result, err := Restore(t.Context(), l.dir, "backup", "restored", config)
			if fault == "auth-swap" {
				must(t, err)
				h, err := Open(t.Context(), l.dir, "restored")
				must(t, err)
				defer h.Close()
				a, ok := h.authenticate(config.Clients[0].Token)
				if !ok || a.client != "client-a" || a.generation != result.Generation || a.generation == g {
					t.Fatalf("swapped token %+v", a)
				}
			} else if err == nil {
				t.Fatalf("restore accepted %s", fault)
			} else if fault == "existing-destination" {
				assertDiskBytes(t, filepath.Join(destination, "sentinel"), p)
			} else {
				assertDiskBytes(t, destination, nil)
			}
			if changed != "" {
				assertDiskBytes(t, changed, []byte("bad"))
			}
			l.start(t)
			l.hubFile(t, notes, "P", "client-a", 1)
		})
	}
}

func TestP4_12_OfflineSIGKILL(t *testing.T) {
	for _, tc := range []struct{ mode, point string }{
		{"backup", "preparing"}, {"backup", "before-publish"}, {"backup", "published"},
		{"restore", "copied-before-generation"}, {"restore", "generation-committed"}, {"restore", "published"},
	} {
		t.Run(tc.mode+"/"+tc.point, func(t *testing.T) {
			l := newLab(t)
			g := l.seed(t)
			l.stop()
			if tc.mode == "restore" {
				_, err := Backup(t.Context(), l.dir, "hub", "backup")
				must(t, err)
			}
			child := startStoppedHub(t, l, tc.mode, tc.point)
			child.kill(t, tc.point)
			dest := "stopped-" + tc.mode
			if tc.point != "published" {
				assertDiskBytes(t, filepath.Join(l.dir, dest), nil)
				entries, err := os.ReadDir(l.dir)
				must(t, err)
				n := 0
				for _, e := range entries {
					if strings.HasPrefix(e.Name(), ".file-sync-offline-") {
						n++
						if h, err := Open(t.Context(), l.dir, e.Name()); err == nil {
							h.Close()
							t.Fatal("unfinished work served HTTP state")
						}
					}
				}
				if n != 1 {
					t.Fatalf("staging dirs %d", n)
				}
			}
			if tc.mode == "backup" {
				if tc.point != "published" {
					_, err := Backup(t.Context(), l.dir, "hub", dest)
					must(t, err)
				}
				info, err := Restore(t.Context(), l.dir, dest, "retry", l.provision)
				must(t, err)
				if info.Generation == g {
					t.Fatal("reused generation")
				}
			} else {
				if tc.point != "published" {
					_, err := Restore(t.Context(), l.dir, "backup", dest, l.provision)
					must(t, err)
				}
				h, err := Open(t.Context(), l.dir, dest)
				must(t, err)
				if h.principals[0].generation == g {
					h.Close()
					t.Fatal("published an old generation")
				}
				must(t, h.Close())
			}
			l.start(t)
			l.hubFile(t, notes, "P", "client-a", 1)
		})
	}
}

func TestP4_12_BackupIncludesPriorVersionsAndChecksForeignKeys(t *testing.T) {
	l := newLab(t)
	g := l.seed(t)
	l.write(t, "A", notes, "Q")
	must(t, local.Sync(t.Context(), l.req(t, "A", 0)))
	l.putSample(t, g, "S")
	l.stop()
	info, err := Backup(t.Context(), l.dir, "hub", "backup")
	must(t, err)
	for _, id := range []string{"P", "Q", "S"} {
		_, d := sample(t, id)
		l.assertBody(t, filepath.Join("backup", "blobs", blobName(l.provision.Space, d.Hash)), id)
	}
	if len(info.Blobs) != 3 {
		t.Fatalf("backup didn't include all registered versions: %+v", info)
	}
	r, err := Restore(t.Context(), l.dir, "backup", "restored", l.provision)
	must(t, err)
	l.h, err = Open(t.Context(), l.dir, "restored")
	must(t, err)
	l.server = httptest.NewServer(l.h)
	l.hubFile(t, notes, "Q", "client-a", 2)
	for _, id := range []string{"P", "Q", "S"} {
		l.checkBlob(t, id, r.Generation)
	}
	if len(l.history(t)) != 2 {
		t.Fatal("backup dropped prior receipts")
	}
	l.stop()

	// Recompute the outer digest so the foreign-key check has to detect
	// the inconsistency inside SQLite independently of the digest guard.
	root := filepath.Join(l.dir, "backup")
	db, err := sql.Open("sqlite3", "file:"+filepath.Join(root, safefs.StateDB)+"?mode=rw&_foreign_keys=off")
	must(t, err)
	_, err = db.Exec("UPDATE entries SET hash=?", strings.Repeat("f", 64))
	must(t, err)
	must(t, db.Close())
	raw, err := os.ReadFile(filepath.Join(root, safefs.StateDB))
	must(t, err)
	info.Database = protocol.Digest(raw)
	manifest, err := json.Marshal(info)
	must(t, err)
	must(t, os.WriteFile(filepath.Join(root, backupManifest), manifest, 0600))
	if _, err := Restore(t.Context(), l.dir, "backup", "refused", l.provision); err == nil || !strings.Contains(err.Error(), "foreign_key_check") {
		t.Fatal(fmt.Errorf("inconsistent backup: %v", err))
	}
	assertDiskBytes(t, filepath.Join(l.dir, "refused"), nil)
	assertDiskBytes(t, filepath.Join(root, safefs.StateDB), raw)
}
