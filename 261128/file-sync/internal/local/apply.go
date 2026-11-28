package local

import (
	"bytes"
	"errors"
	"fmt"
	"os"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

type applyHold struct{ plan Plan }

func (h *applyHold) Error() string {
	return fmt.Sprintf("%s: %q (plan %s)", ErrConflict, h.plan.Path, h.plan.ID)
}
func (h *applyHold) Unwrap() error { return ErrConflict }

type candidate struct {
	dir  *safefs.Dir
	name string
	id   safefs.Identity
	file *os.File
}

func (c *candidate) close() {
	if c.file != nil {
		c.file.Close()
		c.file = nil
	}
}

func (c *candidate) syncClose() error {
	return c.syncCloseAfter(nil)
}

func (c *candidate) syncCloseAfter(afterSync func() error) error {
	if c.file == nil {
		return c.dir.SyncPrivate(c.name, c.id)
	}
	err := c.file.Sync()
	if err == nil && afterSync != nil {
		err = afterSync()
	}
	err = errors.Join(err, c.file.Close())
	c.file = nil
	return err
}

func (e *engine) emit(point string, p Plan) error {
	if e.notify != nil {
		return e.notify(event{point: point, plan: p})
	}
	return nil
}

func (e *engine) apply(p Plan, restart bool) error {
	base, err := e.db.lookup(e.ctx, p.Path)
	if err != nil {
		return err
	}
	if base.Revision != p.BaseRevision {
		return errors.New("baseline differs from persisted plan")
	}
	if p.Target.Hash == "" {
		if !p.Resolution && base.Digest != p.Before {
			return errors.New("deletion preimage differs from baseline")
		}
		return e.applyDelete(p, restart)
	}
	stage := e.db.manager.Stage
	if restart {
		actual, id, err := e.destination.Observe(p.Path)
		if err != nil {
			return err
		}
		if (p.Before == base.Digest || p.Resolution) && actual == p.Target.Digest {
			return e.finishPublished(p, id)
		}
		if actual != p.Before || (p.Before != base.Digest && !p.Resolution) {
			p.Status, p.Local = "conflict", actual
			if err := e.db.update(e.ctx, p); err != nil {
				return errors.Join(ErrConflict, err)
			}
			return e.retainConflict(p)
		}
		if actual.Hash == "" {
			if err := e.checkRecoveryCapacity(p.Path); err != nil {
				return err
			}
		}
	}
	incoming, err := e.acquire(p, p.Incoming(), p.Target.Digest, e.source)
	if errors.Is(err, ErrHash) {
		p.Status = "rejected"
		return errors.Join(err, e.db.update(e.ctx, p))
	}
	if err != nil {
		return err
	}
	defer incoming.close()

	actual, oldID, err := e.destination.Observe(p.Path)
	if err != nil {
		return err
	}
	if actual != p.Before || (p.Before != base.Digest && !p.Resolution) {
		return e.recordConflict(p, actual, incoming)
	}
	if p.needsBackup() {
		if err := e.saveBackup(p); err != nil {
			if errors.Is(err, ErrHash) {
				current, _, observeErr := e.destination.Observe(p.Path)
				if observeErr == nil && current != p.Before {
					return e.recordConflict(p, current, incoming)
				}
				return errors.Join(err, observeErr)
			}
			return err
		}
	}
	if err := e.around("before-incoming-sync-close", "incoming-synced-closed", p, func() error {
		return incoming.syncCloseAfter(func() error { return e.emit("incoming-synced-before-close", p) })
	}); err != nil {
		return err
	}
	parent, leaf, err := e.destination.ParentRecording(p.Path, true, e.rememberDir)
	if err != nil {
		return err
	}
	defer parent.Close()
	if err := e.emit("before-final-check", p); err != nil {
		return err
	}
	currentParent, _, err := e.destination.Parent(p.Path, false)
	if err != nil {
		return err
	}
	defer currentParent.Close()
	parentID, err := parent.ID()
	if err != nil {
		return err
	}
	currentParentID, err := currentParent.ID()
	if err != nil {
		return err
	}
	if currentParentID != parentID {
		return safefs.ErrChanged
	}
	current, currentID, err := parent.Observe(leaf)
	if err != nil {
		return err
	}
	if current != p.Before || currentID != oldID {
		return e.recordConflict(p, current, incoming)
	}
	replace := p.Before.Hash != ""
	if err := e.emit("before-rename", p); err != nil {
		return err
	}
	if err := stage.Install(p.Incoming(), incoming.id, parent, leaf, replace); err != nil {
		if errors.Is(err, unix.EEXIST) && !replace {
			digest, _, observeErr := parent.Observe(leaf)
			if observeErr != nil {
				return errors.Join(err, observeErr)
			}
			return e.recordConflict(p, digest, incoming)
		}
		return fmt.Errorf("rename outcome requires inspection after reopening: %w", err)
	}
	if err := e.emit("renamed", p); err != nil {
		return err
	}
	if err := e.around("before-destination-directory-sync", "destination-directory-synced", p, parent.Sync); err != nil {
		return err
	}
	if err := e.around("before-stage-directory-sync", "stage-directory-synced", p, stage.Sync); err != nil {
		return err
	}
	return e.complete(p)
}

func (e *engine) complete(p Plan) error {
	if err := e.db.complete(e.ctx, p, func() error { return e.emit("before-baseline-commit", p) }); err != nil {
		return err
	}
	if err := e.emit("baseline-committed", p); err != nil {
		return err
	}
	return e.cleanupBackup(p)
}

func (e *engine) saveBackup(p Plan) error {
	backup, err := e.acquire(p, p.Backup(), p.Before, e.destination)
	if err != nil {
		return err
	}
	defer backup.close()
	if p.Target.Hash == "" {
		if err := e.around("before-trash-sync-close", "trash-synced-closed", p, func() error {
			return backup.syncCloseAfter(func() error { return e.emit("trash-synced-before-close", p) })
		}); err != nil {
			return err
		}
		return e.around("before-trash-directory-sync", "trash-directory-synced", p, e.db.manager.Stage.Sync)
	}
	if err := backup.syncClose(); err != nil {
		return err
	}
	return e.db.manager.Stage.Sync()
}

func (e *engine) around(before, after string, p Plan, operation func() error) error {
	if err := e.emit(before, p); err != nil {
		return err
	}
	if err := operation(); err != nil {
		return err
	}
	return e.emit(after, p)
}

func (e *engine) cleanupBackup(p Plan) error {
	if !p.needsBackup() || p.Target.Hash == "" {
		return nil
	}
	stage := e.db.manager.Stage
	digest, id, err := stage.ReadPrivate(p.Backup(), nil)
	if missing(err) {
		return nil
	}
	if err != nil {
		return err
	}
	if digest != p.Before {
		return fmt.Errorf("replacement backup changed: %w", ErrHash)
	}
	if err := stage.Remove(p.Backup(), id); err != nil {
		return err
	}
	return stage.Sync()
}

func (e *engine) checkRecoveryCapacity(path string) error {
	snapshot, err := e.destination.ScanManaged(e.db.manager)
	if err != nil {
		return err
	}
	if len(snapshot.Files) >= safefs.MaxFiles {
		return safefs.ErrLimit
	}
	dirs := len(snapshot.Dirs)
	for {
		i := strings.LastIndexByte(path, '/')
		if i < 0 {
			break
		}
		path = path[:i]
		if !snapshot.Dirs[path] {
			dirs++
		}
	}
	if dirs > safefs.MaxDirs {
		return safefs.ErrLimit
	}
	return nil
}

func (e *engine) finishPublished(p Plan, id safefs.Identity) error {
	stage := e.db.manager.Stage
	if p.needsBackup() {
		backup, _, err := stage.ReadPrivate(p.Backup(), nil)
		if err != nil {
			return fmt.Errorf("replacement backup: %w", err)
		}
		if backup != p.Before {
			return fmt.Errorf("replacement backup: %w", ErrHash)
		}
	}
	digest, stageID, err := stage.ReadPrivate(p.Incoming(), nil)
	if err == nil && digest != p.Target.Digest {
		return fmt.Errorf("published file and retained candidate disagree: %w", ErrHash)
	}
	if err != nil && !missing(err) {
		return err
	}
	if err := e.destination.SyncUser(p.Path, id); err != nil {
		return err
	}
	if err := e.destination.SyncParents(p.Path); err != nil {
		return err
	}
	if stageID != (safefs.Identity{}) {
		if err := stage.Remove(p.Incoming(), stageID); err != nil {
			return err
		}
	}
	if err := stage.Sync(); err != nil {
		return err
	}
	return e.complete(p)
}

func (e *engine) recordConflict(p Plan, local safefs.Digest, incoming *candidate) error {
	p.Status, p.Local = "conflict", local
	if err := e.db.update(e.ctx, p); err != nil {
		return errors.Join(ErrConflict, err)
	}
	if err := incoming.syncClose(); err != nil {
		return errors.Join(ErrConflict, err)
	}
	if err := e.db.manager.Stage.Sync(); err != nil {
		return errors.Join(ErrConflict, err)
	}
	return e.retainConflict(p)
}

func (e *engine) retainConflict(p Plan) error {
	hold := &applyHold{plan: p}
	if p.Target.Hash != "" {
		incoming, err := e.acquire(p, p.Incoming(), p.Target.Digest, e.source)
		if err != nil {
			return errors.Join(hold, err)
		}
		defer incoming.close()
		if err := incoming.syncClose(); err != nil {
			return errors.Join(hold, err)
		}
	}
	if p.Local.Hash != "" {
		if !p.LocalReserved {
			plans, err := e.db.plans(e.ctx)
			if err != nil {
				return errors.Join(hold, err)
			}
			count, size, err := e.db.usage(e.ctx, plans)
			if err != nil {
				return errors.Join(hold, err)
			}
			if err := checkCopyBudget(count+1, size+p.Local.Size); err != nil {
				return errors.Join(hold, err)
			}
			p.LocalReserved = true
			if err := e.db.update(e.ctx, p); err != nil {
				return errors.Join(hold, err)
			}
		}
		local, err := e.acquire(p, p.LocalCopy(), p.Local, e.destination)
		if err != nil {
			return errors.Join(hold, err)
		}
		defer local.close()
		if err := local.syncClose(); err != nil {
			return errors.Join(hold, err)
		}
	}
	if err := e.db.manager.Stage.Sync(); err != nil {
		return errors.Join(hold, err)
	}
	if !p.Saved {
		p.Saved = true
		if err := e.db.update(e.ctx, p); err != nil {
			return errors.Join(hold, err)
		}
	}
	hold.plan = p
	return hold
}

// acquire either verifies an existing stage or creates it from a verified
// source. A short stage is recycled only if its bytes match the source prefix.
func (e *engine) acquire(p Plan, name string, want safefs.Digest, from *safefs.Dir) (*candidate, error) {
	stage := e.db.manager.Stage
	var partial bytes.Buffer
	digest, id, err := stage.ReadPrivate(name, &partial)
	if err == nil {
		if digest == want {
			return &candidate{dir: stage, name: name, id: id}, nil
		}
		if digest.Size >= want.Size || p.Saved {
			return nil, fmt.Errorf("%w: %s", ErrHash, name)
		}
	} else if !missing(err) || p.Saved {
		return nil, err
	}
	var data bytes.Buffer
	var actual safefs.Digest
	if from == nil {
		if e.fetch == nil {
			return nil, errors.New("missing candidate source")
		}
		body, fetchErr := e.fetch(want)
		if fetchErr != nil {
			return nil, fetchErr
		}
		_, _ = data.Write(body)
		actual = want // The protocol client validated the full response.
		err = nil
	} else {
		actual, _, err = from.ReadUser(p.Path, &data)
	}
	if err != nil {
		return nil, fmt.Errorf("read source of %s: %w", name, err)
	}
	if actual != want || !bytes.HasPrefix(data.Bytes(), partial.Bytes()) {
		return nil, fmt.Errorf("%w: source or partial bytes of %s", ErrHash, name)
	}
	if id != (safefs.Identity{}) {
		if err := stage.Remove(name, id); err != nil {
			return nil, err
		}
		if err := stage.Sync(); err != nil {
			return nil, err
		}
	}
	file, id, err := stage.Create(name)
	if err != nil {
		return nil, err
	}
	result := &candidate{dir: stage, name: name, id: id, file: file}
	if err := e.fill(p, result, data.Bytes(), want); err != nil {
		result.close()
		return nil, err
	}
	return result, nil
}

func (e *engine) fill(p Plan, c *candidate, data []byte, want safefs.Digest) error {
	isIncoming := c.name == p.Incoming()
	if isIncoming && len(data) > 2 {
		if _, err := c.file.Write(data[:2]); err != nil {
			return err
		}
		if err := e.emit("incoming-two-bytes", p); err != nil {
			return err
		}
		data = data[2:]
	}
	if _, err := c.file.Write(data); err != nil {
		return err
	}
	if isIncoming {
		if err := e.emit("incoming-written", p); err != nil {
			return err
		}
	}
	actual, id, err := c.dir.ReadPrivate(c.name, nil)
	if err != nil {
		return err
	}
	if actual != want {
		return fmt.Errorf("%w: %s", ErrHash, c.name)
	}
	if id != c.id {
		return safefs.ErrChanged
	}
	if isIncoming {
		return e.emit("incoming-verified", p)
	}
	return nil
}
