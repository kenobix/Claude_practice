package safefs

import (
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"strconv"
	"strings"

	"golang.org/x/sys/unix"
)

const resolve = unix.RESOLVE_BENEATH | unix.RESOLVE_NO_SYMLINKS | unix.RESOLVE_NO_MAGICLINKS | unix.RESOLVE_NO_XDEV

type Digest struct {
	Hash string `json:"content_hash"`
	Size int64  `json:"size"`
}

type Identity struct {
	Device uint64
	Inode  uint64
}

func identity(st unix.Stat_t) Identity { return Identity{Device: st.Dev, Inode: st.Ino} }

type Dir struct {
	fd   int
	proc int
}

type Volume struct {
	dir *Dir
}

func openAt(fd int, path string, flags, constraints uint64, mode uint64) (int, error) {
	out, err := unix.Openat2(fd, path, &unix.OpenHow{
		Flags: flags | unix.O_CLOEXEC, Resolve: constraints, Mode: mode,
	})
	if err != nil {
		return -1, fmt.Errorf("openat2 %q: %w", path, err)
	}
	return out, nil
}

func OpenVolume(path string) (*Volume, error) {
	if !strings.HasPrefix(path, "/") {
		return nil, fmt.Errorf("volume must be an absolute directory: %w", ErrPath)
	}
	relative := strings.TrimPrefix(path, "/")
	if relative == "" {
		relative = "."
	} else {
		for _, part := range strings.Split(relative, "/") {
			if !validComponent(part) || part == Management {
				return nil, fmt.Errorf("volume %q: %w", path, ErrPath)
			}
		}
	}
	slash, err := unix.Open("/", unix.O_PATH|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err != nil {
		return nil, err
	}
	defer unix.Close(slash)
	// Only selection of the trusted volume may cross a mount.
	fd, err := openAt(slash, relative, unix.O_RDONLY|unix.O_DIRECTORY, resolve&^unix.RESOLVE_NO_XDEV, 0)
	if err != nil {
		return nil, err
	}
	var fs unix.Statfs_t
	if err = unix.Fstatfs(fd, &fs); err == nil {
		switch fs.Type {
		case unix.EXT4_SUPER_MAGIC, unix.XFS_SUPER_MAGIC, unix.BTRFS_SUPER_MAGIC:
		default:
			err = fmt.Errorf("volume filesystem type %#x is unsupported", fs.Type)
		}
	}
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	proc, err := unix.Open("/proc/self/fd", unix.O_PATH|unix.O_DIRECTORY|unix.O_CLOEXEC, 0)
	if err == nil {
		err = unix.Fstatfs(proc, &fs)
		if err == nil && fs.Type != unix.PROC_SUPER_MAGIC {
			err = errors.New("/proc/self/fd is not procfs")
		}
		if err != nil {
			unix.Close(proc)
		}
	}
	if err != nil {
		unix.Close(fd)
		return nil, err
	}
	return &Volume{dir: &Dir{fd: fd, proc: proc}}, nil
}

func (v *Volume) Close() { unix.Close(v.dir.fd); unix.Close(v.dir.proc) }

func (v *Volume) LockRoot(path string) (*Dir, error) {
	if err := Validate(path); err != nil {
		return nil, err
	}
	root, err := v.dir.openDir(path)
	if err != nil {
		return nil, err
	}
	if err := root.requireOwner(false); err != nil {
		root.Close()
		return nil, err
	}
	if err := root.Lock(); err != nil {
		root.Close()
		return nil, fmt.Errorf("lock root %q: %w", path, err)
	}
	return root, nil
}

func (d *Dir) Close()      { unix.Close(d.fd) }
func (d *Dir) Sync() error { return unix.Fsync(d.fd) }
func (d *Dir) Lock() error { return unix.Flock(d.fd, unix.LOCK_EX|unix.LOCK_NB) }

func (d *Dir) ID() (Identity, error) {
	var st unix.Stat_t
	err := unix.Fstat(d.fd, &st)
	return identity(st), err
}

func (d *Dir) requireOwner(private bool) error {
	var st unix.Stat_t
	if err := unix.Fstat(d.fd, &st); err != nil {
		return err
	}
	if st.Uid != uint32(os.Geteuid()) || (private && st.Mode&07777 != 0700) {
		return fmt.Errorf("directory ownership or permissions: %w", ErrType)
	}
	return nil
}

func (d *Dir) openDir(path string) (*Dir, error) {
	fd, err := openAt(d.fd, path, unix.O_RDONLY|unix.O_DIRECTORY, resolve, 0)
	if err != nil {
		return nil, err
	}
	return &Dir{fd: fd, proc: d.proc}, nil
}

func (d *Dir) PrivateDir(name string, create bool) (*Dir, error) {
	if !validComponent(name) {
		return nil, ErrPath
	}
	if create {
		if err := unix.Mkdirat(d.fd, name, 0700); err != nil {
			return nil, err
		}
	}
	child, err := d.openDir(name)
	if err != nil {
		return nil, err
	}
	if err := child.requireOwner(true); err != nil {
		child.Close()
		return nil, err
	}
	return child, nil
}

// Anchor returns a path for libraries that require a filename in a private directory.
// Keep the directory open and locked for the lifetime of such a library.
func (d *Dir) Anchor(name string) (string, error) {
	if !validComponent(name) {
		return "", ErrPath
	}
	return "/proc/self/fd/" + strconv.Itoa(d.fd) + "/" + name, nil
}

func (d *Dir) node(path string) (int, unix.Stat_t, error) {
	var st unix.Stat_t
	fd, err := openAt(d.fd, path, unix.O_PATH, resolve, 0)
	if err != nil {
		return -1, st, err
	}
	if err := unix.Fstat(fd, &st); err != nil {
		unix.Close(fd)
		return -1, st, err
	}
	return fd, st, nil
}

func (d *Dir) regular(path string, private bool) (int, unix.Stat_t, error) {
	fd, st, err := d.node(path)
	if err != nil {
		return -1, st, err
	}
	if st.Mode&unix.S_IFMT != unix.S_IFREG || st.Nlink != 1 ||
		(private && (st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0600)) {
		unix.Close(fd)
		return -1, st, fmt.Errorf("%q: %w", path, ErrType)
	}
	return fd, st, nil
}

func (d *Dir) Info(name string) (Identity, int64, error) {
	if !validComponent(name) {
		return Identity{}, 0, ErrPath
	}
	fd, st, err := d.regular(name, true)
	if err != nil {
		return Identity{}, 0, err
	}
	unix.Close(fd)
	return identity(st), st.Size, nil
}

func (d *Dir) read(path string, private bool) (*os.File, unix.Stat_t, error) {
	fd, before, err := d.regular(path, private)
	if err != nil {
		return nil, before, err
	}
	defer unix.Close(fd)
	// This is the procfs entry for an open FD, rather than a second path lookup.
	readFD, err := unix.Openat(d.proc, strconv.Itoa(fd), unix.O_RDONLY|unix.O_CLOEXEC|unix.O_NONBLOCK, 0)
	if err != nil {
		return nil, before, err
	}
	var after unix.Stat_t
	err = unix.Fstat(readFD, &after)
	if err == nil && (identity(before) != identity(after) || after.Mode != before.Mode || after.Nlink != 1) {
		err = ErrChanged
	}
	if err != nil {
		unix.Close(readFD)
		return nil, before, err
	}
	return os.NewFile(uintptr(readFD), path), after, nil
}

func (d *Dir) readContent(path string, private bool, writer io.Writer) (Digest, Identity, error) {
	file, before, err := d.read(path, private)
	if err != nil {
		return Digest{}, Identity{}, err
	}
	defer file.Close()
	if before.Size > MaxSize {
		return Digest{}, Identity{}, fmt.Errorf("%q exceeds %d bytes: %w", path, MaxSize, ErrLimit)
	}
	hash := sha256.New()
	if writer == nil {
		writer = hash
	} else {
		writer = io.MultiWriter(writer, hash)
	}
	n, err := io.Copy(writer, io.LimitReader(file, MaxSize+1))
	if err != nil {
		return Digest{}, Identity{}, err
	}
	if n > MaxSize {
		return Digest{}, Identity{}, ErrLimit
	}
	var after unix.Stat_t
	if err := unix.Fstat(int(file.Fd()), &after); err != nil {
		return Digest{}, Identity{}, err
	}
	if n != before.Size || before.Size != after.Size || before.Mtim != after.Mtim ||
		before.Ctim != after.Ctim || after.Nlink != 1 {
		return Digest{}, Identity{}, ErrChanged
	}
	return Digest{Hash: hex.EncodeToString(hash.Sum(nil)), Size: n}, identity(before), file.Close()
}

func (d *Dir) ReadUser(path string, writer io.Writer) (Digest, Identity, error) {
	if err := Validate(path); err != nil {
		return Digest{}, Identity{}, err
	}
	return d.readContent(path, false, writer)
}

func (d *Dir) ReadPrivate(name string, writer io.Writer) (Digest, Identity, error) {
	if !validComponent(name) {
		return Digest{}, Identity{}, ErrPath
	}
	return d.readContent(name, true, writer)
}

func (d *Dir) SyncUser(path string, expected Identity) error {
	if err := Validate(path); err != nil {
		return err
	}
	f, st, err := d.read(path, false)
	if err != nil {
		return err
	}
	defer f.Close()
	if identity(st) != expected {
		return ErrChanged
	}
	if err := f.Sync(); err != nil {
		return err
	}
	return f.Close()
}

func (d *Dir) Create(name string) (*os.File, Identity, error) {
	if !validComponent(name) {
		return nil, Identity{}, ErrPath
	}
	fd, err := openAt(d.fd, name, unix.O_WRONLY|unix.O_CREAT|unix.O_EXCL|unix.O_NOFOLLOW, resolve, 0600)
	if err != nil {
		return nil, Identity{}, err
	}
	var st unix.Stat_t
	err = unix.Fstat(fd, &st)
	if err == nil && (st.Mode&unix.S_IFMT != unix.S_IFREG || st.Nlink != 1 ||
		st.Uid != uint32(os.Geteuid()) || st.Mode&07777 != 0600) {
		err = ErrType
	}
	if err != nil {
		unix.Close(fd)
		return nil, Identity{}, err
	}
	return os.NewFile(uintptr(fd), name), identity(st), nil
}

func (d *Dir) Remove(name string, expected Identity) error {
	id, _, err := d.Info(name)
	if err != nil {
		return err
	}
	if id != expected {
		return ErrChanged
	}
	return unix.Unlinkat(d.fd, name, 0)
}

func (d *Dir) Install(name string, expected Identity, parent *Dir, leaf string, replace bool) error {
	if !validComponent(leaf) || leaf == Management {
		return ErrPath
	}
	id, _, err := d.Info(name)
	if err != nil {
		return err
	}
	if id != expected {
		return ErrChanged
	}
	flags := uint(unix.RENAME_NOREPLACE)
	if replace {
		flags = 0
	}
	return unix.Renameat2(d.fd, name, parent.fd, leaf, flags)
}

// Parent opens each ancestor under the pinned root. Missing ancestors are
// installed one at a time, and their entries are synced before proceeding.
func (d *Dir) Parent(path string, create bool) (*Dir, string, error) {
	if err := Validate(path); err != nil {
		return nil, "", err
	}
	parts := strings.Split(path, "/")
	parent, err := d.openDir(".")
	if err != nil {
		return nil, "", err
	}
	for _, part := range parts[:len(parts)-1] {
		child, err := parent.openDir(part)
		if errors.Is(err, unix.ENOENT) && create {
			err = unix.Mkdirat(parent.fd, part, 0700)
			if err == nil {
				child, err = parent.openDir(part)
			}
			if err == nil {
				err = parent.Sync()
				if err != nil {
					child.Close()
				}
			}
		}
		parent.Close()
		if err != nil {
			return nil, "", err
		}
		parent = child
	}
	return parent, parts[len(parts)-1], nil
}

// Names lists an opened directory with an independent offset and bounded batches.
func (d *Dir) Names(visit func(string) error) error {
	fd, err := openAt(d.fd, ".", unix.O_RDONLY|unix.O_DIRECTORY, resolve, 0)
	if err != nil {
		return err
	}
	list := os.NewFile(uintptr(fd), "directory")
	defer list.Close()
	for {
		names, err := list.Readdirnames(128)
		for _, name := range names {
			if err := visit(name); err != nil {
				return err
			}
		}
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func (d *Dir) SyncPrivate(name string, expected Identity) error {
	if !validComponent(name) {
		return ErrPath
	}
	f, st, err := d.read(name, true)
	if err != nil {
		return err
	}
	defer f.Close()
	if identity(st) != expected {
		return ErrChanged
	}
	if err := f.Sync(); err != nil {
		return err
	}
	return f.Close()
}

// SyncParents syncs from the immediate parent back to the root. It also
// covers directories created by an interrupted attempt.
func (d *Dir) SyncParents(path string) error {
	if err := Validate(path); err != nil {
		return err
	}
	for {
		i := strings.LastIndexByte(path, '/')
		if i < 0 {
			return d.Sync()
		}
		path = path[:i]
		dir, err := d.openDir(path)
		if err != nil {
			return err
		}
		err = dir.Sync()
		dir.Close()
		if err != nil {
			return err
		}
	}
}
