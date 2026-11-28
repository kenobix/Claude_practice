package local

import (
	"database/sql"
	"errors"
	"maps"
	"slices"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
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

// ownedDirectory checks the on-disk identity against the ownership record.
func (n *networkSync) ownedDirectory(path string) (safefs.DirectoryStamp, bool, error) {
	var device, inode, seconds int64
	var nanos uint32
	err := n.db.conn.QueryRowContext(n.ctx, "SELECT device,inode,birth_seconds,birth_nanos FROM owned_dirs WHERE path=?", path).
		Scan(&device, &inode, &seconds, &nanos)
	if errors.Is(err, sql.ErrNoRows) {
		return safefs.DirectoryStamp{}, false, nil
	}
	if err != nil {
		return safefs.DirectoryStamp{}, false, err
	}
	id, err := n.destination.DirectoryVersion(path)
	if err != nil {
		return safefs.DirectoryStamp{}, false, err
	}
	record := safefs.DirectoryStamp{
		Identity:     safefs.Identity{Device: uint64(device), Inode: uint64(inode)},
		BirthSeconds: seconds, BirthNanos: nanos, HasBirth: true,
	}
	return id, id == record, nil
}

// Only the named empty directory can be removed.
func (n *networkSync) pruneOwned(path string, s safefs.Snapshot) error {
	id, owned, err := n.ownedDirectory(path)
	if err != nil || !owned {
		return err
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

// blockStructure checks which namespace conflicts would still exist after the
// eligible deletions. Only the projected snapshot and manifest are changed.
func (n *networkSync) blockStructure(s safefs.Snapshot, entries []protocol.Entry, decisions map[string]string, protected map[string]bool, selected func(string) bool, projectDeletes bool) error {
	s = safefs.Snapshot{Files: maps.Clone(s.Files), Dirs: maps.Clone(s.Dirs)}
	entries = slices.Clone(entries)
	blocked := make(map[string]bool)
	for _, entry := range entries {
		path := entry.Path
		if !entry.Deleted && decisions[path] == "receive" && blockedBy(path, protected) {
			// Include the install in the group even if its protected descendant
			// has no on-disk file (for example a pending conflicting deletion).
			blocked[path] = true
		}
	}
	if projectDeletes {
		for i, entry := range entries {
			path := entry.Path
			if !selected(path) || blockedBy(path, protected) {
				continue
			}
			switch decisions[path] {
			case "receive":
				if entry.Deleted {
					delete(s.Files, path)
				}
			case "send":
				if s.Files[path].Hash == "" {
					entries[i].Deleted = true
				}
			}
		}
	}
	for _, entry := range entries {
		path := entry.Path
		if entry.Deleted || decisions[path] != "receive" || !selected(path) || blocked[path] || !s.Dirs[path] {
			continue
		}
		prefix := path + "/"
		hasChildren := func(child string) bool { return strings.HasPrefix(child, prefix) }
		if hasDescendant(s.Files, hasChildren) || hasDescendant(s.Dirs, hasChildren) {
			continue
		}
		_, owned, err := n.ownedDirectory(path)
		if err != nil {
			return err
		}
		if owned {
			delete(s.Dirs, path)
		}
	}
	maps.Copy(blocked, structuralPaths(s, entries))
	for path, action := range decisions {
		switch action {
		case "adopt", "send", "receive":
			if blocked[path] || blockedBy(path, blocked) || blockedBy(path, protected) {
				decisions[path] = "namespace_conflict"
			}
		}
	}
	return nil
}

func hasDescendant[T any](paths map[string]T, matches func(string) bool) bool {
	for path := range paths {
		if matches(path) {
			return true
		}
	}
	return false
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
