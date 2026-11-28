package safefs

import (
	"errors"
	"fmt"
	"sort"

	"golang.org/x/sys/unix"
)

type Entry struct {
	Path string `json:"path"`
	Digest
}

type Snapshot struct {
	Files map[string]Digest
	// The empty key represents the root itself.
	Dirs map[string]bool
}

func (s Snapshot) Entries() []Entry {
	entries := make([]Entry, 0, len(s.Files))
	for path, digest := range s.Files {
		entries = append(entries, Entry{Path: path, Digest: digest})
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Path < entries[j].Path })
	return entries
}

func (d *Dir) Scan() (Snapshot, error) {
	return d.ScanManaged(nil)
}

func (d *Dir) ScanManaged(locked *Managed) (Snapshot, error) {
	s := Snapshot{Files: make(map[string]Digest), Dirs: map[string]bool{"": true}}
	err := d.scanDir(d, "", &s, locked)
	if err != nil {
		return Snapshot{}, err
	}
	return s, nil
}

func (d *Dir) scanDir(dir *Dir, prefix string, s *Snapshot, locked *Managed) error {
	return dir.Names(func(name string) error {
		if prefix == "" && name == Management {
			if locked != nil {
				dir, err := d.PrivateDir(Management, false)
				if err != nil {
					return err
				}
				defer dir.Close()
				id, err := dir.ID()
				if err != nil {
					return err
				}
				previous, err := locked.dir.ID()
				if err == nil && id != previous {
					return ErrChanged
				}
				return err
			}
			management, err := d.OpenManagement()
			if err != nil {
				return err
			}
			management.Close()
			return nil
		}
		path := name
		if prefix != "" {
			path = prefix + "/" + name
		}
		if err := Validate(path); err != nil {
			return err
		}
		fd, st, err := d.node(path)
		if err != nil {
			return err
		}
		defer unix.Close(fd)
		switch st.Mode & unix.S_IFMT {
		case unix.S_IFDIR:
			if len(s.Dirs) >= MaxDirs {
				return fmt.Errorf("directory count exceeds %d: %w", MaxDirs, ErrLimit)
			}
			child, err := d.openDir(path)
			if err != nil {
				return err
			}
			defer child.Close()
			actual, err := child.ID()
			if err != nil {
				return err
			}
			if actual != identity(st) {
				return ErrChanged
			}
			s.Dirs[path] = true
			return d.scanDir(child, path, s, locked)
		case unix.S_IFREG:
			if st.Nlink != 1 {
				return fmt.Errorf("%q: %w", path, ErrType)
			}
			if len(s.Files) >= MaxFiles {
				return fmt.Errorf("file count exceeds %d: %w", MaxFiles, ErrLimit)
			}
			digest, actual, err := d.ReadUser(path, nil)
			if err != nil {
				return fmt.Errorf("scan %q: %w", path, err)
			}
			if actual != identity(st) {
				return ErrChanged
			}
			s.Files[path] = digest
			return nil
		default:
			return fmt.Errorf("%q: %w", path, ErrType)
		}
	})
}

// Observe classifies a missing path separately from an invalid ancestor or leaf.
// The caller must hold the root lock and have a successful full scan.
func (d *Dir) Observe(path string) (Digest, Identity, error) {
	if err := Validate(path); err != nil {
		return Digest{}, Identity{}, err
	}
	fd, st, err := d.node(path)
	if errors.Is(err, unix.ENOENT) {
		return Digest{}, Identity{}, nil
	}
	if errors.Is(err, unix.ENOTDIR) || (err == nil && st.Mode&unix.S_IFMT == unix.S_IFDIR) {
		if fd >= 0 {
			unix.Close(fd)
		}
		return Digest{}, Identity{}, fmt.Errorf("%q: %w", path, ErrStructure)
	}
	if err != nil {
		return Digest{}, Identity{}, err
	}
	defer unix.Close(fd)
	digest, actual, err := d.readContent(path, false, nil)
	if err != nil {
		return Digest{}, Identity{}, err
	}
	if actual != identity(st) {
		return Digest{}, Identity{}, ErrChanged
	}
	return digest, actual, nil
}
