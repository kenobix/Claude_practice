package local

import (
	"database/sql"
	"errors"
	"fmt"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func (e *engine) applyDelete(p Plan, restart bool) error {
	if p.Before.Hash == "" && !p.Resolution {
		return errors.New("delete plan has no live preimage")
	}
	current, oldID, err := e.destination.Observe(p.Path)
	if err != nil {
		return err
	}
	if p.Before.Hash == "" && current == p.Before {
		if err := e.destination.SyncExistingParents(p.Path); err != nil {
			return err
		}
		return e.complete(p)
	}
	if p.BackedUp {
		if err := e.verifyTrash(p); err != nil {
			return err
		}
		if restart && current.Hash == "" {
			if err := e.destination.SyncParents(p.Path); err != nil {
				return err
			}
			return e.complete(p)
		}
	}
	if current != p.Before {
		return e.holdDelete(p, current)
	}
	if !p.BackedUp {
		if err := e.saveBackup(p); err != nil {
			return err
		}
		if err := e.db.write(e.ctx, func(tx *sql.Tx) error {
			return mutateOne(e.ctx, tx, "UPDATE plans SET backed_up=1 WHERE id=? AND status='pending' AND backed_up=0", p.ID)
		}); err != nil {
			return err
		}
		p.BackedUp = true
	}
	if err := e.emit("delete-backed-up", p); err != nil {
		return err
	}
	if err := e.verifyTrash(p); err != nil {
		return err
	}
	parent, leaf, err := e.destination.Parent(p.Path, false)
	if err != nil {
		return err
	}
	defer parent.Close()
	d, id, err := parent.Observe(leaf)
	if err != nil {
		return err
	}
	if d != p.Before || id != oldID {
		return e.holdDelete(p, d)
	}
	if err := parent.RemoveUser(leaf, id); err != nil {
		return fmt.Errorf("unlink outcome requires inspection after reopening: %w", err)
	}
	if err := e.emit("unlinked", p); err != nil {
		return err
	}
	if err := parent.Sync(); err != nil {
		return err
	}
	return e.complete(p)
}

func (e *engine) verifyTrash(p Plan) error {
	stage := e.db.manager.Stage
	d, id, err := stage.ReadPrivate(p.Backup(), nil)
	if err != nil || d != p.Before {
		return errors.Join(err, fmt.Errorf("deletion backup: %w", ErrHash))
	}
	if err := stage.SyncPrivate(p.Backup(), id); err != nil {
		return err
	}
	return stage.Sync()
}

func (e *engine) holdDelete(p Plan, current safefs.Digest) error {
	p.Status, p.Local = "conflict", current
	if err := e.db.update(e.ctx, p); err != nil {
		return err
	}
	return e.retainConflict(p)
}
