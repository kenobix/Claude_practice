package local

import (
	"bytes"
	"database/sql"
	"errors"
	"fmt"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

var errRejudge = errors.New("new comparison required")

func (n *networkSync) checkOutgoing(item Outgoing) error {
	if item.Change.Generation != n.reg.Generation || item.Change.Client != n.reg.Client {
		return errors.New("outgoing operation belongs to another registration")
	}
	return nil
}

func (n *networkSync) prepareOutgoing(path string, base int64, d safefs.Digest) (Outgoing, error) {
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return Outgoing{}, err
	}
	count, used, err := n.db.usage(n.ctx, plans)
	if err != nil {
		return Outgoing{}, err
	}
	if d.Hash != "" {
		if err := checkCopyBudget(count+1, used+d.Size); err != nil {
			return Outgoing{}, err
		}
	}
	item, err := outgoing(n.reg, path, base, d)
	if err != nil {
		return item, err
	}
	err = n.db.write(n.ctx, func(tx *sql.Tx) error { return insertOutgoing(n.ctx, tx, item) })
	return item, err
}

func (n *networkSync) stageOutgoing(item Outgoing) error {
	if item.Change.Deleted {
		return nil
	}
	p := Plan{Path: item.Change.Path, ID: item.Change.Operation}
	c, err := n.acquire(p, item.name(), item.digest(), n.destination)
	if err != nil {
		return err
	}
	defer c.close()
	if err := c.syncClose(); err != nil {
		return err
	}
	return n.db.manager.Stage.Sync()
}

func (n *networkSync) send(item Outgoing) error {
	if err := n.checkOutgoing(item); err != nil {
		return err
	}
	firstSend := item.Status == "staging"
	if firstSend {
		if err := n.stageOutgoing(item); err != nil {
			return err
		}
		if err := n.db.setOutgoing(n.ctx, item, "ready"); err != nil {
			return err
		}
		item.Status = "ready"
	}
	if item.Status != "ready" {
		return errors.New("outgoing operation is not ready")
	}
	var buf bytes.Buffer
	digest := item.digest()
	if !item.Change.Deleted {
		actual, _, err := n.db.manager.Stage.ReadPrivate(item.name(), &buf)
		if err != nil || actual != digest {
			return errors.Join(err, fmt.Errorf("stored outgoing body: %w", ErrHash))
		}
	}
	putAttempts := 0
	put := func() error {
		for putAttempts < 2 {
			putAttempts++
			err := n.remote.Put(n.ctx, n.reg.Generation, digest, buf.Bytes())
			if !protocol.Retryable(err) || putAttempts == 2 || n.ctx.Err() != nil {
				return err
			}
		}
		return errors.New("outgoing operation exhausted its PUT attempts")
	}
	if firstSend && !item.Change.Deleted {
		if err := put(); err != nil {
			return err
		}
	}
	for attempt := 0; attempt < 2; attempt++ {
		receipt, err := n.remote.Post(n.ctx, item.Change, item.request)
		if err == nil {
			if err = n.db.acknowledge(n.ctx, item, receipt); err != nil {
				return err
			}
			item.Status = "done"
			return n.cleanupOutgoing(item)
		}
		var p *protocol.Problem
		if errors.As(err, &p) {
			switch p.Code {
			case "blob_missing":
				if item.Change.Deleted {
					return err
				}
				if putErr := put(); putErr != nil {
					return putErr
				}
				if attempt == 0 {
					continue
				}
			case "revision_mismatch", "namespace_conflict":
				if writeErr := n.db.setOutgoing(n.ctx, item, "rejected"); writeErr != nil {
					return writeErr
				}
				return errors.Join(errRejudge, err)
			case "generation_mismatch", "op_id_reused":
				return errors.Join(ErrConflict, err)
			}
		}
		if attempt == 0 && protocol.Retryable(err) && n.ctx.Err() == nil {
			continue
		}
		return err
	}
	return errors.New("outgoing operation exhausted its POST attempts")
}

func (n *networkSync) retireRejected(item Outgoing, decision string) error {
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		if err := putHold(n.ctx, tx, item.Change.Path, "rechecked_"+decision); err != nil {
			return err
		}
		return n.archiveRejected(tx, item)
	})
}

func (n *networkSync) archiveRejected(tx *sql.Tx, item Outgoing) error {
	if _, err := tx.ExecContext(n.ctx, "INSERT INTO rejected_changes VALUES (?,?)", item.Change.Operation, item.request); err != nil {
		return err
	}
	if !item.Change.Deleted {
		if _, err := tx.ExecContext(n.ctx, "INSERT INTO saved_copies VALUES (?,?)", item.name(), item.Change.Size); err != nil {
			return err
		}
	}
	return mutateOne(n.ctx, tx, "DELETE FROM outbox WHERE id=? AND status='rejected'", item.Change.Operation)
}

func (n *networkSync) cleanupOutgoing(item Outgoing) error {
	stage := n.db.manager.Stage
	d, id, err := stage.ReadPrivate(item.name(), nil)
	switch {
	case err == nil:
		if d != item.digest() {
			return fmt.Errorf("retained outgoing body: %w", ErrHash)
		}
		if err := stage.Remove(item.name(), id); err != nil {
			return err
		}
	case !missing(err):
		return err
	}
	if err := stage.Sync(); err != nil {
		return err
	}
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		return mutateOne(n.ctx, tx, "DELETE FROM outbox WHERE id=? AND status='done'", item.Change.Operation)
	})
}
