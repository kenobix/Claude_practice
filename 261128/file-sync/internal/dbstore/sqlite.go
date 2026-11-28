// Package dbstore applies the same bounded SQLite configuration to client and hub.
package dbstore

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/url"
	"strconv"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	_ "github.com/mattn/go-sqlite3"
)

const (
	pageSize      = 4096
	pageLimit     = 8192
	metadataLimit = 128 << 20
)

type Store struct {
	manager *safefs.Managed
	pages   int
	DB      *sql.DB
	Conn    *sql.Conn
}

func Open(ctx context.Context, manager *safefs.Managed, initialize bool, setup func(*Store, string) error) (*Store, error) {
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
	limit := pageLimit
	if n := manager.PreparedPageLimit(); n > 0 {
		limit = min(n, pageLimit)
	}
	s := &Store{manager: manager, DB: db, pages: limit}
	if err := s.checkMetadata(); err != nil {
		db.Close()
		return nil, err
	}
	s.Conn, err = db.Conn(ctx)
	if err != nil {
		db.Close()
		return nil, err
	}
	if err := s.configure(ctx, initialize); err != nil {
		return nil, errors.Join(err, s.Close())
	}
	if !initialize {
		if err := s.checkIntegrity(ctx); err != nil {
			return nil, errors.Join(err, s.Close())
		}
	}
	if err := setup(s, rootID); err != nil {
		return nil, errors.Join(err, s.Close())
	}
	if initialize {
		if err := s.checkIntegrity(ctx); err != nil {
			return nil, errors.Join(err, s.Close())
		}
	}
	return s, nil
}

func (s *Store) Close() error {
	return errors.Join(s.Conn.Close(), s.DB.Close())
}

func (s *Store) configure(ctx context.Context, initialize bool) error {
	if err := s.Expect(ctx, "SELECT sqlite_version()", "3.53.4"); err != nil {
		return err
	}
	if initialize {
		if _, err := s.Conn.ExecContext(ctx, "PRAGMA page_size=4096"); err != nil {
			return err
		}
		if err := s.Expect(ctx, "PRAGMA journal_mode=WAL", "wal"); err != nil {
			return err
		}
	}
	settings := []struct{ set, read, want string }{
		{"", "page_size", "4096"},
		{"", "journal_mode", "wal"},
		{"", "synchronous", "2"},
		{"", "foreign_keys", "1"},
		{"max_page_count=" + strconv.Itoa(s.pages), "max_page_count", strconv.Itoa(s.pages)},
		{"cache_spill=0", "cache_spill", "0"},
		{"temp_store=MEMORY", "temp_store", "2"},
		{"wal_autocheckpoint=0", "wal_autocheckpoint", "0"},
		{"mmap_size=0", "mmap_size", "0"},
	}
	for _, setting := range settings {
		if setting.set != "" {
			if _, err := s.Conn.ExecContext(ctx, "PRAGMA "+setting.set); err != nil {
				return err
			}
		}
		if err := s.Expect(ctx, "PRAGMA "+setting.read, setting.want); err != nil {
			return err
		}
	}
	return s.checkMetadata()
}

func (s *Store) checkIntegrity(ctx context.Context) error {
	if err := s.validateSQLite(ctx); err != nil {
		return err
	}
	return s.checkMetadata()
}

func (s *Store) validateSQLite(ctx context.Context) error {
	if err := s.Expect(ctx, "PRAGMA integrity_check", "ok"); err != nil {
		return err
	}
	rows, err := s.Conn.QueryContext(ctx, "PRAGMA foreign_key_check")
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
	return nil
}

func (s *Store) Expect(ctx context.Context, query, want string) error {
	var value string
	if err := s.Conn.QueryRowContext(ctx, query).Scan(&value); err != nil {
		return fmt.Errorf("%s: %w", query, err)
	}
	if value != want {
		return fmt.Errorf("%s: got %q, want %q", query, value, want)
	}
	return nil
}

func (s *Store) checkMetadata() error {
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
	if size > metadataLimit || dbSize > int64(pageSize*s.pages) {
		return fmt.Errorf("SQLite management budget: %w", safefs.ErrLimit)
	}
	return nil
}

func (s *Store) Checkpoint(ctx context.Context) error {
	if err := s.checkMetadata(); err != nil {
		return err
	}
	var busy, log, done int
	err := s.Conn.QueryRowContext(ctx, "PRAGMA wal_checkpoint(TRUNCATE)").Scan(&busy, &log, &done)
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

func (s *Store) Write(ctx context.Context, perform func(*sql.Tx) error) error {
	if err := s.Checkpoint(ctx); err != nil {
		return err
	}
	tx, err := s.Conn.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	if err := perform(tx); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return &OutcomeError{err}
	}
	if err := s.checkMetadata(); err != nil {
		return &OutcomeError{err}
	}
	return nil
}

// OutcomeError identifies a write that needs inspection after reopening.
type OutcomeError struct{ cause error }

func (e *OutcomeError) Error() string {
	return fmt.Sprintf("SQLite commit outcome requires inspection after reopening: %v", e.cause)
}

func (e *OutcomeError) Unwrap() error { return e.cause }

const MaxDatabaseBytes = pageSize * pageLimit

// InspectSnapshot opens a closed, checkpointed standalone copy without writing
// sidecars. The caller holds the directory lock and has verified the file.
func InspectSnapshot(ctx context.Context, dir *safefs.Dir, inspect func(*sql.Conn) error) (err error) {
	name, err := dir.Anchor(safefs.StateDB)
	if err != nil {
		return err
	}
	db, err := sql.Open("sqlite3", (&url.URL{
		Scheme: "file", Path: name, RawQuery: "mode=ro&immutable=1&_foreign_keys=on",
	}).String())
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.Close()) }()
	db.SetMaxOpenConns(1)
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, conn.Close()) }()
	s := &Store{Conn: conn}
	for query, want := range map[string]string{
		"SELECT sqlite_version()": "3.53.4",
		"PRAGMA page_size":        "4096",
		"PRAGMA foreign_keys":     "1",
	} {
		if err := s.Expect(ctx, query, want); err != nil {
			return err
		}
	}
	if err := s.validateSQLite(ctx); err != nil {
		return err
	}
	return inspect(conn)
}
