package local

import (
	"context"
	"database/sql"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func withDeleteEngine(ctx context.Context, l lab, fn func(*engine) error) (err error) {
	v, err := safefs.OpenVolume(l.volume)
	if err != nil {
		return err
	}
	defer v.Close()
	dest, err := v.LockRoot("B")
	if err != nil {
		return err
	}
	defer dest.Close()
	m, err := dest.OpenManagement()
	if err != nil {
		return err
	}
	defer m.Close()
	db, err := openState(ctx, m, false)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.close()) }()
	if _, err := dest.ScanManaged(m); err != nil {
		return err
	}
	return fn(&engine{ctx: ctx, destination: dest, db: db})
}

func prepareDelete(t *testing.T, l lab) Plan {
	t.Helper()
	seedP(t, l)
	_, d := fixture(t, "P")
	id, err := newID()
	must(t, err)
	p := Plan{ID: id, Path: notes, Source: "A", BaseRevision: 1, Before: d, Target: Base{Revision: 2}, Status: "pending"}
	must(t, withDeleteEngine(t.Context(), l, func(e *engine) error { return e.db.add(e.ctx, p) }))
	return p
}

func resumeDelete(ctx context.Context, l lab, notify func(event) error) error {
	return withDeleteEngine(ctx, l, func(e *engine) error {
		plans, err := e.db.plans(ctx)
		if err != nil {
			return err
		}
		e.notify = notify
		for _, p := range plans {
			if p.Status == "pending" && p.Target.Hash == "" {
				return e.apply(p, true)
			}
		}
		return errors.New("missing pending deletion")
	})
}

func TestP3DeleteSIGKILL(t *testing.T) {
	for _, point := range []string{"delete-backed-up", "unlinked"} {
		t.Run(point, func(t *testing.T) {
			l := newLab(t)
			p := prepareDelete(t, l)
			body, digest := fixture(t, "P")
			killAtBoundary(t, l, "p3-"+point)
			observed := l.status(t)
			assertBase(t, observed, notes, 1, digest)
			pending := findPlan(t, observed, 2)
			if !pending.BackedUp || pending.Status != "pending" {
				t.Fatalf("stopped deletion plan %+v", pending)
			}
			assertFile(t, filepath.Dir(l.stage(p.Backup())), p.Backup(), body, digest)
			if point == "delete-backed-up" {
				assertFile(t, l.b, notes, body, digest)
			} else if _, err := os.Stat(filepath.Join(l.b, notes)); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("unlink missing: %v", err)
			}
			must(t, resumeDelete(t.Context(), l, nil))
			final := l.status(t)
			assertBase(t, final, notes, 2, safefs.Digest{})
			if p := findPlan(t, final, 2); p.Status != "complete" || !p.BackedUp {
				t.Fatalf("final plan %+v", p)
			}
			if _, err := os.Stat(filepath.Join(l.b, notes)); !errors.Is(err, os.ErrNotExist) {
				t.Fatalf("delete not completed: %v", err)
			}
			assertFile(t, filepath.Dir(l.stage(p.Backup())), p.Backup(), body, digest)
			assertFile(t, l.a, notes, body, digest)
			t.Logf("%s: preimage %s/1 retained, plan pending until restart, tombstone/2 committed after verification", point, digest.Hash)
		})
	}
}

func TestP3DeleteRejectsMissingOrCorruptTrash(t *testing.T) {
	for _, fault := range []string{"corrupt-before-unlink", "missing-before-unlink", "missing-after-unlink"} {
		t.Run(fault, func(t *testing.T) {
			l := newLab(t)
			p := prepareDelete(t, l)
			body, digest := fixture(t, "P")
			if fault == "missing-after-unlink" {
				killAtBoundary(t, l, "p3-unlinked")
				must(t, os.Remove(l.stage(p.Backup())))
			}
			err := resumeDelete(t.Context(), l, func(ev event) error {
				if ev.point != "delete-backed-up" {
					return nil
				}
				if fault == "corrupt-before-unlink" {
					return os.WriteFile(l.stage(ev.plan.Backup()), []byte("bad"), 0600)
				}
				return os.Remove(l.stage(ev.plan.Backup()))
			})
			if !errors.Is(err, ErrHash) {
				t.Fatalf("delete with unsafe backup: %v", err)
			}
			s := l.status(t)
			assertBase(t, s, notes, 1, digest)
			if p := findPlan(t, s, 2); p.Status != "pending" || !p.BackedUp {
				t.Fatalf("pending deletion lost: %+v", p)
			}
			if fault != "missing-after-unlink" {
				assertFile(t, l.b, notes, body, digest)
			}
			assertFile(t, l.a, notes, body, digest)
		})
	}
}

func TestP3DeletionAndConflictWhenCopyBudgetIsFull(t *testing.T) {
	l := newLab(t)
	seedP(t, l)
	body, p := fixture(t, "P")
	_, q := fixture(t, "Q")
	_, r := fixture(t, "R")
	must(t, withDeleteEngine(t.Context(), l, func(e *engine) error {
		if err := e.db.write(e.ctx, func(tx *sql.Tx) error {
			for i := 0; i < safefs.MaxManagedBytes/safefs.MaxSize; i++ {
				id, err := newID()
				if err != nil {
					return err
				}
				if _, err := tx.ExecContext(e.ctx, "INSERT INTO saved_copies VALUES (?,?)", id+".candidate", safefs.MaxSize); err != nil {
					return err
				}
			}
			return nil
		}); err != nil {
			return err
		}
		n := networkSync{engine: *e, reg: &Registration{Generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}}
		if err := n.install(notes, Base{Revision: 1, Digest: p}, Base{Revision: 2}); !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("deletion should reserve space before writing: %v", err)
		}
		from := candidateOf(r, 0, "deleted")
		c, err := n.makeConflict(notes, "both_changed", Base{Revision: 1, Digest: p}, from,
			protocol.Entry{Path: notes, Hash: &q.Hash, Size: q.Size, Revision: 2}, nil)
		if err != nil {
			return err
		}
		if err := n.saveCandidates(c, nil); err != nil {
			return err
		}
		if c.Saved || c.Local.Failure != "copy_budget" || c.Hub[0].Failure != "copy_budget" ||
			c.Local.Copy != "" || c.Hub[0].Copy != "" {
			t.Fatalf("capacity-limited conflict %+v", c)
		}
		return nil
	}))
	assertFile(t, l.b, notes, body, p)
	assertBase(t, l.status(t), notes, 1, p)
}
