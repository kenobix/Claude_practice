package safefs

import (
	"errors"
	"strings"

	"golang.org/x/sys/unix"
)

// RemoveUser checks the type and inode immediately before unlinking a leaf of
// a verified parent directory. The caller also checks the content and holds the root lock.
func (d *Dir) RemoveUser(leaf string, expected Identity) error {
	if !validComponent(leaf) || leaf == Management {
		return ErrPath
	}
	fd, st, err := d.regular(leaf, false)
	if err != nil {
		return err
	}
	defer unix.Close(fd)
	if identity(st) != expected {
		return ErrChanged
	}
	return unix.Unlinkat(d.fd, leaf, 0)
}

type DirectoryStamp struct {
	Identity
	BirthSeconds int64
	BirthNanos   uint32
	HasBirth     bool
}

func (d *Dir) stamp() (DirectoryStamp, error) {
	id, err := d.ID()
	if err != nil {
		return DirectoryStamp{}, err
	}
	var st unix.Statx_t
	err = unix.Statx(d.fd, "", unix.AT_EMPTY_PATH, unix.STATX_BTIME, &st)
	if errors.Is(err, unix.ENOSYS) || errors.Is(err, unix.EOPNOTSUPP) || errors.Is(err, unix.EINVAL) {
		return DirectoryStamp{Identity: id}, nil
	}
	return DirectoryStamp{Identity: id, BirthSeconds: st.Btime.Sec, BirthNanos: st.Btime.Nsec,
		HasBirth: st.Mask&unix.STATX_BTIME != 0}, err
}

func (d *Dir) DirectoryVersion(path string) (DirectoryStamp, error) {
	if err := Validate(path); err != nil {
		return DirectoryStamp{}, err
	}
	child, err := d.openDir(path)
	if err != nil {
		return DirectoryStamp{}, err
	}
	defer child.Close()
	return child.stamp()
}

func (d *Dir) RemoveEmptyDir(path string, expected DirectoryStamp) error {
	parent, leaf, err := d.Parent(path, false)
	if err != nil {
		return err
	}
	defer parent.Close()
	actual, err := parent.DirectoryVersion(leaf)
	if err != nil {
		return err
	}
	if !actual.HasBirth || actual != expected {
		return ErrChanged
	}
	if err := unix.Unlinkat(parent.fd, leaf, unix.AT_REMOVEDIR); err != nil {
		if errors.Is(err, unix.ENOTEMPTY) {
			return ErrStructure
		}
		return err
	}
	return parent.Sync()
}

// SyncExistingParents covers user-initiated deletions even if some ancestors
// no longer exist. An existing file ancestor is not a directory to traverse.
func (d *Dir) SyncExistingParents(path string) error {
	if err := Validate(path); err != nil {
		return err
	}
	for i := strings.LastIndexByte(path, '/'); i >= 0; i = strings.LastIndexByte(path, '/') {
		path = path[:i]
		parent, err := d.openDir(path)
		if errors.Is(err, unix.ENOENT) || errors.Is(err, unix.ENOTDIR) {
			continue
		}
		if err != nil {
			return err
		}
		err = parent.Sync()
		parent.Close()
		if err != nil {
			return err
		}
	}
	return d.Sync()
}
