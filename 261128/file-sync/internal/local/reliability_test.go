package local

import (
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

func TestSQLiteSettingsAndWAL(t *testing.T) {
	l := newLab(t)
	seedP(t, l)
	v, err := safefs.OpenVolume(l.volume)
	must(t, err)
	defer v.Close()
	root, err := v.LockRoot("B")
	must(t, err)
	defer root.Close()
	manager, err := root.OpenManagement()
	must(t, err)
	defer manager.Close()
	state, err := openState(t.Context(), manager, false)
	must(t, err)
	defer func() { must(t, state.close()) }()
	for query, want := range map[string]string{
		"SELECT sqlite_version()":   "3.53.4",
		"PRAGMA journal_mode":       "wal",
		"PRAGMA synchronous":        "2",
		"PRAGMA foreign_keys":       "1",
		"PRAGMA page_size":          "4096",
		"PRAGMA max_page_count":     "8192",
		"PRAGMA cache_spill":        "0",
		"PRAGMA temp_store":         "2",
		"PRAGMA wal_autocheckpoint": "0",
		"PRAGMA mmap_size":          "0",
		"PRAGMA integrity_check":    "ok",
	} {
		var actual string
		must(t, state.conn.QueryRowContext(t.Context(), query).Scan(&actual))
		if actual != want {
			t.Errorf("%s = %s, want %s", query, actual, want)
		}
	}
	if stats := state.db.Stats(); stats.MaxOpenConnections != 1 || stats.OpenConnections != 1 || stats.InUse != 1 {
		t.Fatalf("not using one pinned physical connection: %+v", stats)
	}
	must(t, state.checkpoint(t.Context()))
	info, err := os.Stat(filepath.Join(l.b, safefs.Management, "state", safefs.StateDB+"-wal"))
	must(t, err)
	if info.Size() != 0 {
		t.Fatalf("WAL length after checkpoint: %d", info.Size())
	}
}

func TestNamespaceCollision(t *testing.T) {
	for _, ancestor := range []bool{true, false} {
		name := "destination-directory"
		if ancestor {
			name = "destination-ancestor-file"
		}
		t.Run(name, func(t *testing.T) {
			l := newLab(t)
			p, pHash := fixture(t, "P")
			r, rHash := fixture(t, "R")
			source, dest := "a", "a/keep"
			if ancestor {
				source, dest = dest, source
			}
			writeAt(t, l.a, source, p)
			writeAt(t, l.a, "unrelated", p)
			writeAt(t, l.b, dest, r)
			if err := Copy(t.Context(), l.request(1)); !errors.Is(err, safefs.ErrStructure) {
				t.Fatalf("expected namespace refusal: %v", err)
			}
			s := l.status(t)
			if len(s.Plans) != 0 || len(s.Bases) != 0 {
				t.Fatalf("unexpected plans: %+v", s)
			}
			_, err := os.Stat(filepath.Join(l.b, "unrelated"))
			if !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("a different path was applied after invalid preflight: %v", err)
			}
			assertFile(t, l.a, source, p, pHash)
			assertFile(t, l.b, dest, r, rHash)
		})
	}
}

func TestMissingOrUnsafeStateStopsCopy(t *testing.T) {
	for _, example := range []string{"missing", "symlink", "root-id-mismatch"} {
		t.Run(example, func(t *testing.T) {
			l := newLab(t)
			seedP(t, l)
			p, pHash := fixture(t, "P")
			q, qHash := fixture(t, "Q")
			writeAt(t, l.a, notes, q)
			state := filepath.Join(l.b, safefs.Management, "state")
			db := filepath.Join(state, safefs.StateDB)
			switch example {
			case "missing":
				must(t, os.Rename(db, filepath.Join(l.volume, "saved-state")))
			case "symlink":
				must(t, os.Rename(db, filepath.Join(l.volume, "saved-state")))
				writeAt(t, l.volume, "sentinel", p)
				must(t, os.Symlink(filepath.Join(l.volume, "sentinel"), db))
			default:
				writeAt(t, state, safefs.RootID, []byte("00000000000000000000000000000000"))
			}
			if err := Copy(t.Context(), l.request(2)); err == nil {
				t.Fatal("copy continued without a valid state database")
			}
			assertFile(t, l.b, notes, p, pHash)
			assertFile(t, l.a, notes, q, qHash)
			if example == "symlink" {
				assertFile(t, l.volume, "sentinel", p, pHash)
			}
		})
	}
}

func TestUnknownIOKeepsRecoveryData(t *testing.T) {
	l := newLab(t)
	seedP(t, l)
	p, pHash := fixture(t, "P")
	q, qHash := fixture(t, "Q")
	writeAt(t, l.a, notes, q)
	err := run(t.Context(), l.request(2), false, func(ev event) error {
		if ev.point == "renamed" {
			return unix.EIO
		}
		return nil
	})
	if !errors.Is(err, unix.EIO) {
		t.Fatalf("expected EIO: %v", err)
	}
	status := l.status(t)
	plan := findPlan(t, status, 2)
	if plan.Status != "pending" {
		t.Fatalf("lost pending state: %+v", plan)
	}
	assertBase(t, status, notes, 1, pHash)
	assertFile(t, l.b, notes, q, qHash)
	assertFile(t, l.stage(""), plan.Backup(), p, pHash)
	assertFile(t, l.a, notes, q, qHash)
	// Reopening must stop if it cannot verify the old bytes. Neither version is discarded.
	r, rHash := fixture(t, "R")
	writeAt(t, l.stage(""), plan.Backup(), r)
	if err := Recover(t.Context(), l.request(0)); !errors.Is(err, ErrHash) {
		t.Fatalf("invalid backup should stop recovery: %v", err)
	}
	assertBase(t, l.status(t), notes, 1, pHash)
	assertFile(t, l.stage(""), plan.Backup(), r, rHash)
	assertFile(t, l.b, notes, q, qHash)
	writeAt(t, l.stage(""), plan.Backup(), p)
	must(t, Recover(t.Context(), l.request(0)))
	assertBase(t, l.status(t), notes, 2, qHash)
}

func TestManagementReservationBoundaries(t *testing.T) {
	must(t, checkCopyBudget(4096, 1920<<20))
	for _, err := range []error{checkCopyBudget(4097, 0), checkCopyBudget(1, (1920<<20)+1)} {
		if !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("expected management quota: %v", err)
		}
	}
}
