package safefs

import (
	"bufio"
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

// LockRootForSetup excludes simultaneous setup on an ancestor or descendant.
// The caller must also check the target tree under this lock (the client scan
// rejects nested management, and hub initialization requires an empty root).
func (v *Volume) LockRootForSetup(path string) (*Dir, error) {
	if err := v.checkVolumeMount(); err != nil {
		return nil, err
	}
	root, err := v.LockRoot(path)
	if err != nil {
		return nil, err
	}
	if err := root.lockUnmanagedAncestors(false); err != nil {
		root.Close()
		return nil, err
	}
	return root, nil
}

// Ancestors are opened for topology checks only, never for access to user data.
// Shared locks on the same inodes work across different volume settings.
// Nonblocking flock makes opposed parent/child attempts fail without deadlock.
func (d *Dir) lockUnmanagedAncestors(includeSelf bool) error {
	current := d
	if includeSelf {
		if err := current.lockUnmanaged(); err != nil {
			return err
		}
	}
	for {
		fd, err := openAt(current.fd, "..", unix.O_RDONLY|unix.O_DIRECTORY, unix.RESOLVE_NO_SYMLINKS|unix.RESOLVE_NO_MAGICLINKS, 0)
		if err != nil {
			return err
		}
		parent := &Dir{fd: fd, proc: d.proc}
		d.ancestors = append(d.ancestors, parent)
		id, err := parent.ID()
		if err != nil {
			return err
		}
		previous, err := current.ID()
		if err != nil {
			return err
		}
		if id == previous {
			return nil
		}
		if err := parent.lockUnmanaged(); err != nil {
			return err
		}
		current = parent
	}
}

func (d *Dir) lockUnmanaged() error {
	if err := unix.Flock(d.fd, unix.LOCK_SH|unix.LOCK_NB); err != nil {
		return fmt.Errorf("lock ancestor directory: %w", err)
	}
	fd, _, err := d.node(Management)
	if err == nil {
		unix.Close(fd)
		return errors.New("target overlaps an existing management directory")
	}
	if !errors.Is(err, unix.ENOENT) {
		return fmt.Errorf("verify ancestor directory: %w", err)
	}
	return nil
}

// A subtree bind mount could hide the physical parent of a managed root.
// Accept filesystem roots, native Btrfs subvolumes and mounts that preserve
// the backing directory's location in the parent mount.
func (v *Volume) checkVolumeMount() error {
	var st unix.Statx_t
	if err := unix.Statx(v.dir.fd, "", unix.AT_EMPTY_PATH, unix.STATX_MNT_ID, &st); err != nil {
		return err
	}
	if st.Mask&unix.STATX_MNT_ID == 0 {
		return errors.New("volume mount ID unavailable")
	}
	f, err := os.Open("/proc/self/mountinfo")
	if err != nil {
		return err
	}
	defer f.Close()
	if err := verifyVolumeMount(bufio.NewScanner(f), strconv.FormatUint(st.Mnt_id, 10)); err != nil {
		return fmt.Errorf("verify volume mount: %w", err)
	}
	return nil
}

func verifyVolumeMount(lines *bufio.Scanner, id string) error {
	mounts := make(map[string][]string)
	for lines.Scan() {
		fields := strings.Fields(lines.Text())
		if len(fields) < 10 {
			return errors.New("invalid mountinfo entry")
		}
		mounts[fields[0]] = fields
	}
	if err := lines.Err(); err != nil {
		return err
	}
	mount := mounts[id]
	if mount == nil {
		return errors.New("volume mount not found")
	}
	return validateBackingMount(mounts, mount)
}

func validateBackingMount(mounts map[string][]string, mount []string) error {
	for mount[3] != "/" {
		parent := mounts[mount[1]]
		if parent == nil || parent[2] != mount[2] || mount[0] == parent[0] {
			return nativeSubvolume(mount)
		}
		suffix, ok := strings.CutPrefix(mount[4], strings.TrimSuffix(parent[4], "/")+"/")
		if !ok || strings.TrimSuffix(parent[3], "/")+"/"+suffix != mount[3] {
			return nativeSubvolume(mount)
		}
		mount = parent
	}
	return nil
}

func nativeSubvolume(mount []string) error {
	// Mountinfo fields are escaped by the kernel. Compare the escaped roots.
	if mount[len(mount)-3] == "btrfs" {
		for _, option := range strings.Split(mount[len(mount)-1], ",") {
			if option == "subvol="+mount[3] {
				return nil
			}
		}
	}
	return errors.New("subtree mount hides volume ancestry")
}
