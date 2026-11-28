package local

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	_ "github.com/mattn/go-sqlite3"
	"golang.org/x/sys/unix"
)

const (
	pageSize      = 4096
	pageLimit     = 8192
	metadataLimit = 128 << 20
	planLimit     = 4096
)

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
}

func (p Plan) Incoming() string  { return p.ID + ".incoming" }
func (p Plan) LocalCopy() string { return p.ID + ".local" }
func (p Plan) Backup() string    { return p.ID + ".before" }
func (p Plan) needsBackup() bool { return p.Before.Hash != "" && p.Before != p.Target.Digest }

type stateDB struct {
	manager *safefs.Managed
	db      *sql.DB
	conn    *sql.Conn
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
	name, err := manager.State.Anchor(safefs.StateDB)
	if err != nil {
		return nil, err
	}
	var marker strings.Builder
	digest, _, err := manager.State.ReadPrivate(safefs.RootID, &marker)
	if err != nil {
		return nil, err
	}
	rootID := marker.String()
	if digest.Size != 32 || !safefs.StageName(rootID+".incoming") {
		return nil, errors.New("invalid root identity")
	}
	db, err := sql.Open("sqlite3", (&url.URL{
		Scheme: "file", Path: name, RawQuery: "mode=rw&_synchronous=FULL&_foreign_keys=on",
	}).String())
	if err != nil {
		return nil, err
	}
	db.SetMaxOpenConns(1)
	db.SetMaxIdleConns(1)
	s := &stateDB{manager: manager, db: db}
	if err := s.checkMetadata(); err != nil {
		db.Close()
		return nil, err
	}
	s.conn, err = db.Conn(ctx)
	if err != nil {
		db.Close()
		return nil, err
	}
	if err := s.configure(ctx, initialize, rootID); err != nil {
		return nil, errors.Join(err, s.close())
	}
	return s, nil
}

func (s *stateDB) close() error {
	return errors.Join(s.conn.Close(), s.db.Close())
}

func (s *stateDB) configure(ctx context.Context, initialize bool, rootID string) error {
	if err := s.expect(ctx, "SELECT sqlite_version()", "3.53.4"); err != nil {
		return err
	}
	if initialize {
		if _, err := s.conn.ExecContext(ctx, "PRAGMA page_size=4096"); err != nil {
			return err
		}
		if err := s.expect(ctx, "PRAGMA journal_mode=WAL", "wal"); err != nil {
			return err
		}
	}
	settings := []struct{ set, read, want string }{
		{"", "page_size", "4096"},
		{"", "journal_mode", "wal"},
		{"", "synchronous", "2"},
		{"", "foreign_keys", "1"},
		{"max_page_count=8192", "max_page_count", "8192"},
		{"cache_spill=0", "cache_spill", "0"},
		{"temp_store=MEMORY", "temp_store", "2"},
		{"wal_autocheckpoint=0", "wal_autocheckpoint", "0"},
		{"mmap_size=0", "mmap_size", "0"},
	}
	for _, setting := range settings {
		if setting.set != "" {
			if _, err := s.conn.ExecContext(ctx, "PRAGMA "+setting.set); err != nil {
				return err
			}
		}
		if err := s.expect(ctx, "PRAGMA "+setting.read, setting.want); err != nil {
			return err
		}
	}
	if initialize {
		err := s.write(ctx, func(tx *sql.Tx) error {
			if _, err := tx.ExecContext(ctx, schema); err != nil {
				return err
			}
			_, err := tx.ExecContext(ctx, "INSERT INTO identity VALUES (1, ?)", rootID)
			return err
		})
		if err != nil {
			return err
		}
	}
	if err := s.expect(ctx, "PRAGMA user_version", "1"); err != nil {
		return err
	}
	if err := s.expect(ctx, "PRAGMA integrity_check", "ok"); err != nil {
		return err
	}
	rows, err := s.conn.QueryContext(ctx, "PRAGMA foreign_key_check")
	if err != nil {
		return err
	}
	hasViolation := rows.Next()
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return err
	}
	if hasViolation {
		return errors.New("SQLite foreign_key_check found a violation")
	}
	var actual string
	err = s.conn.QueryRowContext(ctx, "SELECT root_id FROM identity WHERE singleton=1").Scan(&actual)
	if err != nil {
		return err
	}
	if actual != rootID {
		return errors.New("state database and root identity disagree")
	}
	return s.checkMetadata()
}

func (s *stateDB) expect(ctx context.Context, query, want string) error {
	var value string
	if err := s.conn.QueryRowContext(ctx, query).Scan(&value); err != nil {
		return fmt.Errorf("%s: %w", query, err)
	}
	if value != want {
		return fmt.Errorf("%s: got %q, want %q", query, value, want)
	}
	return nil
}

func (s *stateDB) checkMetadata() error {
	if err := s.manager.ValidateStateFiles(); err != nil {
		return err
	}
	size, err := s.manager.MetadataSize()
	if err != nil {
		return err
	}
	_, dbSize, err := s.manager.State.Info(safefs.StateDB)
	if err != nil {
		return err
	}
	if size > metadataLimit || dbSize > pageSize*pageLimit {
		return fmt.Errorf("SQLite management budget: %w", safefs.ErrLimit)
	}
	return nil
}

func (s *stateDB) checkpoint(ctx context.Context) error {
	if err := s.checkMetadata(); err != nil {
		return err
	}
	var busy, log, done int
	err := s.conn.QueryRowContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)").Scan(&busy, &log, &done)
	if err != nil {
		return err
	}
	if busy != 0 || log != 0 || done != 0 {
		return fmt.Errorf("SQLite WAL checkpoint incomplete (%d, %d, %d)", busy, log, done)
	}
	_, size, err := s.manager.State.Info(safefs.StateDB + "-wal")
	if err != nil {
		return err
	}
	if size != 0 {
		return errors.New("SQLite WAL did not truncate")
	}
	return s.checkMetadata()
}

func (s *stateDB) write(ctx context.Context, perform func(*sql.Tx) error) error {
	if err := s.checkpoint(ctx); err != nil {
		return err
	}
	tx, err := s.conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := perform(tx); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return fmt.Errorf("SQLite commit outcome requires inspection after reopening: %w", err)
	}
	return s.checkMetadata()
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
       status,local_hash,local_size,local_reserved,saved FROM plans ORDER BY path,id`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	plans := make([]Plan, 0)
	for rows.Next() {
		var p Plan
		err := rows.Scan(&p.ID, &p.Path, &p.Source, &p.BaseRevision, &p.Before.Hash, &p.Before.Size,
			&p.Target.Revision, &p.Target.Hash, &p.Target.Size, &p.Status, &p.Local.Hash, &p.Local.Size,
			&p.LocalReserved, &p.Saved)
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
INSERT INTO plans (id,path,source,base_revision,before_hash,before_size,target_revision,target_hash,target_size)
VALUES (?,?,?,?,?,?,?,?,?)`, p.ID, p.Path, p.Source, p.BaseRevision, p.Before.Hash, p.Before.Size,
			p.Target.Revision, p.Target.Hash, p.Target.Size)
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

func (s *stateDB) complete(ctx context.Context, p Plan) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		if err := mutateOne(ctx, tx, `
INSERT INTO baseline (path,revision,hash,size) VALUES (?,?,?,?)
ON CONFLICT(path) DO UPDATE SET revision=excluded.revision, hash=excluded.hash, size=excluded.size
WHERE baseline.revision=?`, p.Path, p.Target.Revision, p.Target.Hash, p.Target.Size, p.BaseRevision); err != nil {
			return err
		}
		return mutateOne(ctx, tx, "UPDATE plans SET status='complete' WHERE id=? AND status='pending'", p.ID)
	})
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

func (s *stateDB) usage(plans []Plan) (count int, bytes int64, err error) {
	reserved := make(map[string]int64)
	retained := make(map[string]int64)
	for _, p := range plans {
		if p.Status != "complete" {
			reserved[p.Incoming()] = p.Target.Size
		}
		if p.needsBackup() {
			if p.Status == "pending" {
				reserved[p.Backup()] = p.Before.Size
			} else {
				retained[p.Backup()] = p.Before.Size
			}
		}
		if p.LocalReserved {
			reserved[p.LocalCopy()] = p.Local.Size
		}
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
