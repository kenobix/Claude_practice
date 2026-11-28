package local

import (
	"database/sql"
	"errors"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func (e *engine) rememberDir(path string, stamp safefs.DirectoryStamp) error {
	return e.db.write(e.ctx, func(tx *sql.Tx) error {
		return mutateOne(e.ctx, tx, `
INSERT INTO owned_dirs SELECT ?,?,?,?,? WHERE (SELECT count(*) FROM owned_dirs)<?
OR EXISTS(SELECT 1 FROM owned_dirs WHERE path=?)
ON CONFLICT(path) DO UPDATE SET device=excluded.device,inode=excluded.inode,
birth_seconds=excluded.birth_seconds,birth_nanos=excluded.birth_nanos`,
			path, int64(stamp.Device), int64(stamp.Inode), stamp.BirthSeconds, stamp.BirthNanos, safefs.MaxDirs, path)
	})
}

func relatedPath(a, b string) bool {
	return a == b || strings.HasPrefix(a, b+"/") || strings.HasPrefix(b, a+"/")
}

// Only the named empty directory can be removed. A nonempty subtree
// requires the user's intervention, even if all its directories are owned.
func (n *networkSync) pruneOwned(path string, s safefs.Snapshot) error {
	var device, inode, seconds int64
	var nanos uint32
	err := n.db.conn.QueryRowContext(n.ctx, "SELECT device,inode,birth_seconds,birth_nanos FROM owned_dirs WHERE path=?", path).
		Scan(&device, &inode, &seconds, &nanos)
	if errors.Is(err, sql.ErrNoRows) {
		return nil
	}
	if err != nil {
		return err
	}
	id, err := n.destination.DirectoryVersion(path)
	if err != nil {
		return err
	}
	record := safefs.DirectoryStamp{
		Identity:     safefs.Identity{Device: uint64(device), Inode: uint64(inode)},
		BirthSeconds: seconds, BirthNanos: nanos, HasBirth: true,
	}
	if id != record {
		return nil
	}
	if err := n.destination.RemoveEmptyDir(path, id); errors.Is(err, safefs.ErrStructure) {
		return nil
	} else if err != nil {
		return err
	}
	if err := n.db.write(n.ctx, func(tx *sql.Tx) error {
		return mutateOne(n.ctx, tx, "DELETE FROM owned_dirs WHERE path=?", path)
	}); err != nil {
		return err
	}
	delete(s.Dirs, path)
	return nil
}

func structurallyAbsent(s safefs.Snapshot, path string) bool {
	if s.Dirs[path] {
		return true
	}
	for i := 0; i < len(path); i++ {
		if path[i] == '/' && s.Files[path[:i]].Hash != "" {
			return true
		}
	}
	return false
}

func (n *networkSync) confirmAbsence(path string) error {
	d, _, err := n.destination.Observe(path)
	if err != nil {
		return err
	}
	if d.Hash != "" {
		return safefs.ErrChanged
	}
	return n.destination.SyncExistingParents(path)
}
