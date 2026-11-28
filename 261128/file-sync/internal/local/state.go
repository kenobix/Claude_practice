package local

import (
	"context"
	"database/sql"
	"errors"
	"fmt"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/dbstore"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

const planLimit = 4096

type Base struct {
	Revision int64 `json:"revision"`
	safefs.Digest
}

type Plan struct {
	ID            string        `json:"id"`
	Path          string        `json:"path"`
	Source        string        `json:"source"`
	BaseRevision  int64         `json:"base_revision"`
	Before        safefs.Digest `json:"before"`
	Target        Base          `json:"target"`
	Status        string        `json:"status"`
	Local         safefs.Digest `json:"local"`
	LocalReserved bool          `json:"local_reserved"`
	Saved         bool          `json:"saved"`
	BackedUp      bool          `json:"backed_up,omitempty"`
	Settled       bool          `json:"settled,omitempty"`
	Resolution    bool          `json:"resolution,omitempty"`
}

func (p Plan) Incoming() string  { return p.ID + ".incoming" }
func (p Plan) LocalCopy() string { return p.ID + ".local" }
func (p Plan) Backup() string {
	if p.Target.Hash == "" {
		return p.ID + ".trash"
	}
	return p.ID + ".before"
}
func (p Plan) needsBackup() bool { return p.Before.Hash != "" && p.Before != p.Target.Digest }

type stateDB struct {
	manager *safefs.Managed
	db      *sql.DB
	conn    *sql.Conn
	store   *dbstore.Store
}

const schema = `
CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK (singleton = 1), root_id TEXT NOT NULL);
CREATE TABLE baseline (
    path TEXT COLLATE BINARY PRIMARY KEY NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0),
    hash TEXT NOT NULL CHECK (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'),
    size INTEGER NOT NULL CHECK (size BETWEEN 0 AND 10485760)
);
CREATE TABLE plans (
    id TEXT PRIMARY KEY NOT NULL CHECK (length(id) = 32 AND id NOT GLOB '*[^0-9a-f]*'),
    path TEXT COLLATE BINARY NOT NULL,
    source TEXT COLLATE BINARY NOT NULL,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    before_hash TEXT NOT NULL CHECK (before_hash = '' OR (length(before_hash) = 64 AND before_hash NOT GLOB '*[^0-9a-f]*')),
    before_size INTEGER NOT NULL CHECK (before_size BETWEEN 0 AND 10485760 AND (before_hash <> '' OR before_size = 0)),
    target_revision INTEGER NOT NULL CHECK (target_revision > base_revision),
    target_hash TEXT NOT NULL CHECK (length(target_hash) = 64 AND target_hash NOT GLOB '*[^0-9a-f]*'),
    target_size INTEGER NOT NULL CHECK (target_size BETWEEN 0 AND 10485760),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','complete','conflict','rejected')),
    local_hash TEXT NOT NULL DEFAULT '' CHECK (local_hash = '' OR (length(local_hash) = 64 AND local_hash NOT GLOB '*[^0-9a-f]*')),
    local_size INTEGER NOT NULL DEFAULT 0 CHECK (local_size BETWEEN 0 AND 10485760 AND (local_hash <> '' OR local_size = 0)),
    local_reserved INTEGER NOT NULL DEFAULT 0 CHECK (local_reserved IN (0,1)),
    saved INTEGER NOT NULL DEFAULT 0 CHECK (saved IN (0,1)),
    CHECK (local_reserved = 0 OR (status = 'conflict' AND local_hash <> '')),
    CHECK (saved = 0 OR (status = 'conflict' AND (local_hash = '' OR local_reserved = 1)))
);
CREATE UNIQUE INDEX one_unresolved_plan ON plans(path) WHERE status IN ('pending','conflict');
PRAGMA user_version=1;
`

func openState(ctx context.Context, manager *safefs.Managed, initialize bool) (*stateDB, error) {
	store, err := dbstore.Open(ctx, manager, initialize, func(store *dbstore.Store, rootID string) error {
		return setupClient(ctx, store, initialize, rootID)
	})
	if err != nil {
		return nil, err
	}
	return &stateDB{manager: manager, conn: store.Conn, db: store.DB, store: store}, nil
}

func setupClient(ctx context.Context, store *dbstore.Store, initialize bool, rootID string) error {
	if initialize {
		return store.Write(ctx, func(tx *sql.Tx) error {
			if _, err := tx.ExecContext(ctx, schema); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, "INSERT INTO identity VALUES (1, ?)", rootID); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, networkSchema); err != nil {
				return err
			}
			return migratePhase3(ctx, tx)
		})
	}
	var actual string
	if err := store.Conn.QueryRowContext(ctx, "SELECT root_id FROM identity WHERE singleton=1").Scan(&actual); err != nil {
		return err
	}
	if actual != rootID {
		return errors.New("state database and root identity disagree")
	}
	var version int
	if err := store.Conn.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil {
		return err
	}
	switch version {
	case 1:
		// A hub also has a root marker and can have user_version 1.
		// Require the old client schema before applying the additive migration.
		if err := store.Expect(ctx, "SELECT count(*) FROM sqlite_schema WHERE type='table' AND name IN ('baseline','plans')", "2"); err != nil {
			return err
		}
		return store.Write(ctx, func(tx *sql.Tx) error {
			if _, err := tx.ExecContext(ctx, networkSchema); err != nil {
				return err
			}
			return migratePhase3(ctx, tx)
		})
	case 2:
		return store.Write(ctx, func(tx *sql.Tx) error { return migratePhase3(ctx, tx) })
	case 3:
		return nil
	default:
		return errors.New("unsupported client database schema")
	}
}

func (s *stateDB) close() error { return s.store.Close() }

func (s *stateDB) checkpoint(ctx context.Context) error { return s.store.Checkpoint(ctx) }

func (s *stateDB) write(ctx context.Context, perform func(*sql.Tx) error) error {
	return s.store.Write(ctx, perform)
}

func (s *stateDB) lookup(ctx context.Context, path string) (Base, error) {
	var base Base
	err := s.conn.QueryRowContext(ctx, "SELECT revision, hash, size FROM baseline WHERE path=?", path).
		Scan(&base.Revision, &base.Hash, &base.Size)
	if errors.Is(err, sql.ErrNoRows) {
		return Base{}, nil
	}
	return base, err
}

func (s *stateDB) plans(ctx context.Context) ([]Plan, error) {
	rows, err := s.conn.QueryContext(ctx, `
SELECT id,path,source,base_revision,before_hash,before_size,target_revision,target_hash,target_size,
       status,local_hash,local_size,local_reserved,saved,backed_up,resolution,settled FROM plans ORDER BY path,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	plans := make([]Plan, 0)
	for rows.Next() {
		var p Plan
		err := rows.Scan(&p.ID, &p.Path, &p.Source, &p.BaseRevision, &p.Before.Hash, &p.Before.Size,
			&p.Target.Revision, &p.Target.Hash, &p.Target.Size, &p.Status, &p.Local.Hash, &p.Local.Size,
			&p.LocalReserved, &p.Saved, &p.BackedUp, &p.Resolution, &p.Settled)
		if err != nil {
			return nil, err
		}
		if !safefs.StageName(p.Incoming()) || safefs.Validate(p.Path) != nil || safefs.Validate(p.Source) != nil {
			return nil, errors.New("invalid persisted plan")
		}
		if len(plans) >= planLimit {
			return nil, fmt.Errorf("plan count: %w", safefs.ErrLimit)
		}
		plans = append(plans, p)
	}
	return plans, rows.Err()
}

func (s *stateDB) add(ctx context.Context, p Plan) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		_, err := tx.ExecContext(ctx, `
INSERT INTO plans (id,path,source,base_revision,before_hash,before_size,target_revision,target_hash,target_size,resolution)
VALUES (?,?,?,?,?,?,?,?,?,?)`, p.ID, p.Path, p.Source, p.BaseRevision, p.Before.Hash, p.Before.Size,
			p.Target.Revision, p.Target.Hash, p.Target.Size, p.Resolution)
		return err
	})
}

func (s *stateDB) update(ctx context.Context, p Plan) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		return mutateOne(ctx, tx, `
UPDATE plans SET status=?, local_hash=?, local_size=?, local_reserved=?, saved=?
WHERE id=? AND status IN ('pending','conflict')`, p.Status, p.Local.Hash, p.Local.Size, p.LocalReserved, p.Saved, p.ID)
	})
}

func (s *stateDB) complete(ctx context.Context, p Plan, beforeCommit func() error) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		if err := putBase(ctx, tx, p.Path, p.BaseRevision, p.Target); err != nil {
			return err
		}
		if err := mutateOne(ctx, tx, "UPDATE plans SET status='complete' WHERE id=? AND status='pending'", p.ID); err != nil {
			return err
		}
		return beforeCommit()
	})
}

func putBase(ctx context.Context, tx *sql.Tx, path string, previous int64, target Base) error {
	if previous == 0 {
		return mutateOne(ctx, tx, "INSERT INTO baseline (path,revision,hash,size) VALUES (?,?,?,?)",
			path, target.Revision, target.Hash, target.Size)
	}
	return mutateOne(ctx, tx, "UPDATE baseline SET revision=?,hash=?,size=? WHERE path=? AND revision=?",
		target.Revision, target.Hash, target.Size, path, previous)
}

func mutateOne(ctx context.Context, tx *sql.Tx, statement string, args ...any) error {
	result, err := tx.ExecContext(ctx, statement, args...)
	if err != nil {
		return err
	}
	n, err := result.RowsAffected()
	if err != nil {
		return err
	}
	if n != 1 {
		return errors.New("SQLite state changed unexpectedly")
	}
	return nil
}

func (s *stateDB) usage(ctx context.Context, plans []Plan) (count int, bytes int64, err error) {
	reserved := make(map[string]int64)
	retained := make(map[string]int64)
	for _, p := range plans {
		if p.Status != "complete" && p.Target.Hash != "" {
			reserved[p.Incoming()] = p.Target.Size
		}
		if p.needsBackup() {
			if p.Status == "pending" || p.BackedUp {
				reserved[p.Backup()] = p.Before.Size
			} else {
				retained[p.Backup()] = p.Before.Size
			}
		}
		if p.LocalReserved {
			reserved[p.LocalCopy()] = p.Local.Size
		}
	}
	outbox, err := s.outbox(ctx)
	if err != nil {
		return 0, 0, err
	}
	for _, item := range outbox {
		if item.Change.Deleted {
			continue
		}
		if item.Status == "done" {
			retained[item.name()] = item.Change.Size
		} else {
			reserved[item.name()] = item.Change.Size
		}
	}
	rows, err := s.conn.QueryContext(ctx, "SELECT name,size FROM saved_copies")
	if err != nil {
		return 0, 0, err
	}
	for rows.Next() {
		var name string
		var size int64
		if err := rows.Scan(&name, &size); err != nil {
			rows.Close()
			return 0, 0, err
		}
		if !safefs.StageName(name) {
			rows.Close()
			return 0, 0, errors.New("invalid saved copy name")
		}
		reserved[name] = size
	}
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return 0, 0, err
	}
	for _, size := range reserved {
		count++
		bytes += size
	}
	err = s.manager.Stage.Names(func(name string) error {
		size, ok := reserved[name]
		if !ok {
			size, ok = retained[name]
			if !ok {
				return fmt.Errorf("stage file %q has no live reservation", name)
			}
			count++
			bytes += size
		}
		_, actual, err := s.manager.Stage.Info(name)
		if err != nil {
			return err
		}
		if actual > safefs.MaxSize {
			return safefs.ErrLimit
		}
		if actual > size {
			bytes += (actual - size)
		}
		return nil
	})
	if err == nil {
		err = checkCopyBudget(count, bytes)
	}
	return count, bytes, err
}

func checkCopyBudget(count int, bytes int64) error {
	if count > safefs.MaxManagedCopies || bytes > safefs.MaxManagedBytes {
		return fmt.Errorf("management copy budget: %w", safefs.ErrLimit)
	}
	return nil
}

func missing(err error) bool { return errors.Is(err, unix.ENOENT) }
