package local

import (
	"context"
	"database/sql"
	"strings"
)

// The two old tables need new CHECK constraints for a versioned absence.
// Copy the original columns verbatim, including unresolved Phase 1/2 work.
func migratePhase3(ctx context.Context, tx *sql.Tx) error {
	start := strings.Index(schema, "CREATE TABLE baseline")
	end := strings.Index(schema, "PRAGMA user_version")
	tables := schema[start:end]
	tables = strings.Replace(tables, "length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'", "((hash = '' AND size = 0) OR (length(hash) = 64 AND hash NOT GLOB '*[^0-9a-f]*'))", 1)
	tables = strings.Replace(tables, "target_revision > base_revision", "target_revision > 0 AND target_revision >= base_revision", 1)
	tables = strings.Replace(tables, "length(target_hash) = 64 AND target_hash NOT GLOB '*[^0-9a-f]*'", "((target_hash = '' AND target_size = 0) OR (length(target_hash) = 64 AND target_hash NOT GLOB '*[^0-9a-f]*'))", 1)
	_, err := tx.ExecContext(ctx, `
DROP INDEX one_unresolved_plan;
ALTER TABLE baseline RENAME TO old_baseline;
ALTER TABLE plans RENAME TO old_plans;
`+tables+`
INSERT INTO baseline SELECT * FROM old_baseline;
INSERT INTO plans SELECT * FROM old_plans;
DROP TABLE old_baseline;
DROP TABLE old_plans;
ALTER TABLE plans ADD COLUMN backed_up INTEGER NOT NULL DEFAULT 0 CHECK(backed_up IN (0,1));
ALTER TABLE plans ADD COLUMN resolution INTEGER NOT NULL DEFAULT 0 CHECK(resolution IN (0,1));
ALTER TABLE plans ADD COLUMN settled INTEGER NOT NULL DEFAULT 0 CHECK(settled IN (0,1));
DROP INDEX one_unresolved_plan;
CREATE UNIQUE INDEX one_unresolved_plan ON plans(path) WHERE status IN ('pending','conflict') AND settled=0;
CREATE TABLE owned_dirs (
    path TEXT COLLATE BINARY PRIMARY KEY NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL,
    birth_seconds INTEGER NOT NULL, birth_nanos INTEGER NOT NULL
);
CREATE TABLE rejected_changes (id TEXT PRIMARY KEY NOT NULL, request BLOB NOT NULL);
CREATE TABLE saved_copies (name TEXT PRIMARY KEY NOT NULL, size INTEGER NOT NULL CHECK(size BETWEEN 0 AND 10485760));
CREATE TABLE conflicts (
    id TEXT PRIMARY KEY NOT NULL, path TEXT COLLATE BINARY NOT NULL,
    active INTEGER NOT NULL CHECK(active IN (0,1)), data BLOB NOT NULL
);
CREATE UNIQUE INDEX one_active_conflict ON conflicts(path) WHERE active=1;
ALTER TABLE registration RENAME TO old_registration;
CREATE TABLE registration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), space TEXT NOT NULL, client TEXT NOT NULL,
    generation TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('seed','pull','reconcile')),
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1))
);
INSERT INTO registration SELECT * FROM old_registration;
DROP TABLE old_registration;
CREATE TABLE reconcile_plan (singleton INTEGER PRIMARY KEY CHECK(singleton=1), data BLOB NOT NULL);
PRAGMA user_version=3;
`)
	return err
}
