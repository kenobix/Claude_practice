package local

import (
	"bytes"
	"database/sql"
	"encoding/base64"
	"errors"
	"fmt"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/hub"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

func localTestToken(b byte) string {
	return base64.RawURLEncoding.EncodeToString(bytes.Repeat([]byte{b}, 32))
}

type networkLab struct {
	lab
	addr string
}

func newNetworkLab(t *testing.T) networkLab {
	t.Helper()
	l := newLab(t)
	must(t, Init(t.Context(), l.volume, "A"))
	must(t, os.Mkdir(filepath.Join(l.volume, "hub"), 0700))
	provision := hub.Provision{Space: "test", Clients: [2]hub.Client{
		{ID: "client-a", Token: localTestToken(1)},
		{ID: "client-b", Token: localTestToken(2)},
	}}
	must(t, hub.Init(t.Context(), l.volume, "hub", provision))
	h, err := hub.Open(t.Context(), l.volume, "hub")
	must(t, err)
	server := httptest.NewServer(h)
	t.Cleanup(func() { server.Close(); h.Close() })
	n := networkLab{lab: l, addr: server.Listener.Addr().String()}
	p, _ := fixture(t, "P")
	writeAt(t, l.a, notes, p)
	must(t, Register(t.Context(), n.req(t, "A"), "seed"))
	must(t, Register(t.Context(), n.req(t, "B"), "pull"))
	return n
}

func (n networkLab) req(t *testing.T, root string) NetworkRequest {
	t.Helper()
	client := byte(1)
	if root == "B" {
		client = 2
	}
	remote, err := protocol.NewClient(n.addr, localTestToken(client))
	must(t, err)
	return NetworkRequest{Volume: n.volume, Root: root, Remote: remote}
}

func (n networkLab) publish(t *testing.T, id string) protocol.Manifest {
	t.Helper()
	if id == "" {
		must(t, os.Remove(filepath.Join(n.a, notes)))
	} else {
		body, _ := fixture(t, id)
		writeAt(t, n.a, notes, body)
	}
	must(t, Sync(t.Context(), n.req(t, "A")))
	m, err := n.req(t, "A").Remote.Manifest(t.Context())
	must(t, err)
	if len(m.Entries) != 1 || m.Entries[0].Revision != 2 {
		t.Fatalf("unexpected manifest %+v", m)
	}
	return m
}

func (n networkLab) assertHubUnchanged(t *testing.T, initial protocol.Manifest) {
	t.Helper()
	m, err := n.req(t, "A").Remote.Manifest(t.Context())
	must(t, err)
	if m.Generation != initial.Generation || len(m.Entries) != 1 ||
		m.Entries[0].Operation != initial.Entries[0].Operation || m.Entries[0].Revision != 2 ||
		m.Entries[0].Deleted != initial.Entries[0].Deleted {
		t.Fatalf("hub advanced during apply: %+v", m)
	}
	name := filepath.Join(n.volume, "hub", safefs.Management, "state", safefs.StateDB)
	db, err := sql.Open("sqlite3", "file:"+name+"?mode=ro")
	must(t, err)
	defer db.Close()
	var receipts int
	must(t, db.QueryRow("SELECT COUNT(*) FROM receipts").Scan(&receipts))
	if receipts != 2 {
		t.Fatalf("unexpected receipts %d", receipts)
	}
	p, pd := fixture(t, "P")
	data, err := n.req(t, "A").Remote.Get(t.Context(), m.Generation, pd)
	must(t, err)
	if !bytes.Equal(data, p) {
		t.Fatal("hub lost P")
	}
}

func TestP4_05_ReceiveSIGKILL(t *testing.T) {
	for _, tc := range []struct {
		point                              string
		renamed, committed, backup, edited bool
	}{
		{"plan-committed", false, false, false, false},
		{"incoming-two-bytes", false, false, false, false},
		{"incoming-verified", false, false, false, false},
		{"before-incoming-sync-close", false, false, true, false},
		{"incoming-synced-before-close", false, false, true, false},
		{"incoming-synced-closed", false, false, true, false},
		{"before-rename", false, false, true, false},
		{"renamed", true, false, true, false},
		{"before-destination-directory-sync", true, false, true, false},
		{"destination-directory-synced", true, false, true, false},
		{"before-stage-directory-sync", true, false, true, false},
		{"stage-directory-synced", true, false, true, false},
		{"before-baseline-commit", true, false, true, false},
		{"baseline-committed", true, true, true, false},
		{"renamed", true, false, true, true},
	} {
		name := tc.point
		if tc.edited {
			name += "-then-edited"
		}
		t.Run(name, func(t *testing.T) {
			n := newNetworkLab(t)
			m := n.publish(t, "Q")
			p, pd := fixture(t, "P")
			q, qd := fixture(t, "Q")
			r, rd := fixture(t, "R")
			killAtBoundary(t, n.lab, "p4-"+tc.point, "FILE_SYNC_TEST_HUB="+n.addr)
			s := n.status(t)
			plan := findPlan(t, s, 2)
			if tc.renamed {
				assertFile(t, n.b, notes, q, qd)
			} else {
				assertFile(t, n.b, notes, p, pd)
			}
			if tc.committed {
				assertBase(t, s, notes, 2, qd)
				if plan.Status != "complete" {
					t.Fatalf("plan %+v", plan)
				}
			} else {
				assertBase(t, s, notes, 1, pd)
				if plan.Status != "pending" {
					t.Fatalf("plan %+v", plan)
				}
			}
			if tc.backup {
				assertFile(t, n.stage(""), plan.Backup(), p, pd)
			} else {
				assertAbsent(t, n.stage(""), plan.Backup())
			}
			switch {
			case tc.renamed || tc.point == "plan-committed":
				assertAbsent(t, n.stage(""), plan.Incoming())
			case tc.point == "incoming-two-bytes":
				data, err := os.ReadFile(n.stage(plan.Incoming()))
				must(t, err)
				if !bytes.Equal(data, q[:2]) {
					t.Fatalf("partial body %q", data)
				}
			default:
				assertFile(t, n.stage(""), plan.Incoming(), q, qd)
			}
			if tc.edited {
				writeAt(t, n.b, notes, r)
				if err := Sync(t.Context(), n.req(t, "B")); !errors.Is(err, ErrConflict) {
					t.Fatalf("want conflict, got %v", err)
				}
				s = n.status(t)
				plan = findPlan(t, s, 2)
				assertBase(t, s, notes, 1, pd)
				assertFile(t, n.b, notes, r, rd)
				assertFile(t, n.stage(""), plan.Backup(), p, pd)
				assertFile(t, n.stage(""), plan.Incoming(), q, qd)
				assertFile(t, n.stage(""), plan.LocalCopy(), r, rd)
				if plan.Status != "conflict" || !plan.Saved {
					t.Fatalf("plan %+v", plan)
				}
			} else {
				must(t, Sync(t.Context(), n.req(t, "B")))
				s = n.status(t)
				assertBase(t, s, notes, 2, qd)
				assertFile(t, n.b, notes, q, qd)
				if findPlan(t, s, 2).Status != "complete" {
					t.Fatalf("recovery plans %+v", s.Plans)
				}
				if _, err := os.Stat(n.stage(plan.Backup())); !errors.Is(err, os.ErrNotExist) {
					t.Fatalf("expected old body cleanup after completion: %v", err)
				}
			}
			assertFile(t, n.a, notes, q, qd)
			n.assertHubUnchanged(t, m)
			t.Logf("%s: renamed=%t baseline_committed=%t saved_P=%t; edited=%t; hub rev=2 receipts=2", name, tc.renamed, tc.committed, tc.backup, tc.edited)
		})
	}
}

func TestP4_06_DeleteSIGKILL(t *testing.T) {
	for _, tc := range []struct {
		point                              string
		trash, recorded, removed, complete bool
		fault                              string
	}{
		{"plan-committed", false, false, false, false, ""},
		{"before-trash-sync-close", true, false, false, false, ""},
		{"trash-synced-before-close", true, false, false, false, ""},
		{"trash-synced-closed", true, false, false, false, ""},
		{"before-trash-directory-sync", true, false, false, false, ""},
		{"trash-directory-synced", true, false, false, false, ""},
		{"before-delete-backed-up-commit", true, false, false, false, ""},
		{"delete-backed-up", true, true, false, false, ""},
		{"before-unlink", true, true, false, false, ""},
		{"unlinked", true, true, true, false, ""},
		{"before-delete-directory-sync", true, true, true, false, ""},
		{"delete-directory-synced", true, true, true, false, ""},
		{"before-baseline-commit", true, true, true, false, ""},
		{"baseline-committed", true, true, true, true, ""},
		{"unlinked", true, true, true, false, "missing"},
		{"unlinked", true, true, true, false, "corrupt"},
	} {
		name := tc.point
		if tc.fault != "" {
			name += "-" + tc.fault
		}
		t.Run(name, func(t *testing.T) {
			n := newNetworkLab(t)
			m := n.publish(t, "")
			p, pd := fixture(t, "P")
			if !m.Entries[0].Deleted || m.Entries[0].Hash != nil {
				t.Fatalf("not tombstone %+v", m.Entries)
			}
			killAtBoundary(t, n.lab, "p4-"+tc.point, "FILE_SYNC_TEST_HUB="+n.addr)
			s := n.status(t)
			plan := findPlan(t, s, 2)
			if tc.removed {
				assertAbsent(t, n.b, notes)
			} else {
				assertFile(t, n.b, notes, p, pd)
			}
			if tc.trash {
				assertFile(t, n.stage(""), plan.Backup(), p, pd)
			} else {
				assertAbsent(t, n.stage(""), plan.Backup())
			}
			if plan.BackedUp != tc.recorded || (plan.Status == "complete") != tc.complete {
				t.Fatalf("plan %+v", plan)
			}
			if tc.complete {
				assertBase(t, s, notes, 2, safefs.Digest{})
			} else {
				assertBase(t, s, notes, 1, pd)
			}
			switch tc.fault {
			case "missing":
				must(t, os.Remove(n.stage(plan.Backup())))
			case "corrupt":
				writeAt(t, n.stage(""), plan.Backup(), []byte("bad"))
			}
			err := Sync(t.Context(), n.req(t, "B"))
			s = n.status(t)
			plan = findPlan(t, s, 2)
			assertAbsent(t, n.b, notes)
			if tc.fault == "" {
				must(t, err)
				assertBase(t, s, notes, 2, safefs.Digest{})
				assertFile(t, n.stage(""), plan.Backup(), p, pd)
				if plan.Status != "complete" || !plan.BackedUp {
					t.Fatalf("plan %+v", plan)
				}
			} else {
				if !errors.Is(err, ErrHash) || plan.Status != "pending" || !plan.BackedUp || len(s.Outbox) != 0 {
					t.Fatalf("damaged trash: %v %+v", err, s)
				}
				assertBase(t, s, notes, 1, pd)
				if tc.fault == "missing" {
					assertAbsent(t, n.stage(""), plan.Backup())
				} else {
					raw, err := os.ReadFile(n.stage(plan.Backup()))
					must(t, err)
					if string(raw) != "bad" {
						t.Fatalf("corruption lost %q", raw)
					}
				}
			}
			n.assertHubUnchanged(t, m)
			t.Logf("%s: unlink=%t trash=%t recorded=%t committed=%t; hub tombstone/2 receipts=2", name, tc.removed, tc.trash, tc.recorded, tc.complete)
		})
	}
}

func assertAbsent(t *testing.T, root, name string) {
	t.Helper()
	_, err := os.Stat(filepath.Join(root, name))
	if !errors.Is(err, os.ErrNotExist) {
		t.Fatal(fmt.Errorf("expected %s absent: %v", name, err))
	}
}

func TestP4_11_ClientCopyBudgets(t *testing.T) {
	for _, scenario := range []struct {
		name     string
		left     int64
		conflict bool
		target   string
	}{
		{"receive-no-bytes", 0, false, "Q"},
		{"replacement-no-space-for-before", 5, false, "Q"},
		{"delete-no-space-for-trash", 2, false, ""},
		{"conflict-last-file", 0, true, "Q"},
	} {
		t.Run(scenario.name, func(t *testing.T) {
			n := newNetworkLab(t)
			m := n.publish(t, scenario.target)
			p, pd := fixture(t, "P")
			r, rd := fixture(t, "R")
			count := 0
			used := int64(0)
			if scenario.conflict {
				count = safefs.MaxManagedCopies - 1
				writeAt(t, n.b, notes, r)
			} else {
				used = safefs.MaxManagedBytes - scenario.left
				count = int((used + safefs.MaxSize - 1) / safefs.MaxSize)
			}
			must(t, withDeleteEngine(t.Context(), n.lab, func(e *engine) error {
				return e.db.write(e.ctx, func(tx *sql.Tx) error {
					for i := 0; i < count; i++ {
						name := fmt.Sprintf("%032x.candidate", i+1)
						size := min(max(used-int64(i)*safefs.MaxSize, 0), safefs.MaxSize)
						f, _, err := e.db.manager.Stage.Create(name)
						if err != nil {
							return err
						}
						if err := errors.Join(f.Truncate(size), f.Sync(), f.Close()); err != nil {
							return err
						}
						if _, err := tx.Exec("INSERT INTO saved_copies VALUES (?,?)", name, size); err != nil {
							return err
						}
					}
					return e.db.manager.Stage.Sync()
				})
			}))
			err := Sync(t.Context(), n.req(t, "B"))
			if !errors.Is(err, ErrConflict) {
				t.Fatalf("expected pending: %v", err)
			}
			s := n.status(t)
			assertBase(t, s, notes, 1, pd)
			if len(s.Outbox) != 0 || len(s.Plans) != 1 {
				t.Fatalf("allocated plan without full reservation: %+v", s)
			}
			if scenario.conflict {
				assertFile(t, n.b, notes, r, rd)
				if len(s.Conflicts) != 1 || s.Conflicts[0].Saved || !s.Conflicts[0].Local.Saved ||
					s.Conflicts[0].Hub[0].Saved || s.Conflicts[0].Hub[0].Failure != "copy_budget" {
					t.Fatalf("partly retained conflict mismatch: %+v", s.Conflicts)
				}
				assertFile(t, n.stage(""), s.Conflicts[0].Local.Copy, r, rd)
				count++
			} else {
				assertFile(t, n.b, notes, p, pd)
				if s.Holds[notes] != "apply_pending" {
					t.Fatalf("missing capacity hold: %+v", s.Holds)
				}
			}
			var logical, physical int64
			entries, err := os.ReadDir(n.stage(""))
			must(t, err)
			for _, entry := range entries {
				var st unix.Stat_t
				must(t, unix.Stat(n.stage(entry.Name()), &st))
				logical += st.Size
				physical += st.Blocks * 512
			}
			if len(entries) != count || (!scenario.conflict && logical != used) || (scenario.conflict && logical != rd.Size) {
				t.Fatalf("stage count/size %d/%d", len(entries), logical)
			}
			n.assertHubUnchanged(t, m)
			t.Logf("client capacity: %s entries=%d logical=%d physical=%d base=1 receipts=2", scenario.name, len(entries), logical, physical)
		})
	}
}
