package safefs

import (
	"errors"
	"fmt"
	"os"
	"strings"

	"golang.org/x/sys/unix"
)

const (
	StateDB  = "state.sqlite"
	RootID   = "root-id"
	LockFile = "lock"
)

const (
	MaxManagedCopies = 4096
	MaxManagedBytes  = 1920 << 20
)

type Managed struct {
	dir   *Dir
	State *Dir
	Stage *Dir
	lock  *os.File
}

func StageName(name string) bool {
	id, suffix, ok := strings.Cut(name, ".")
	if !ok || len(id) != 32 || (suffix != "incoming" && suffix != "local" && suffix != "before" && suffix != "outgoing") {
		return false
	}
	return strings.Trim(id, "0123456789abcdef") == ""
}

func (m *Managed) Close() {
	if m.Stage != nil {
		m.Stage.Close()
	}
	if m.State != nil {
		m.State.Close()
	}
	if m.lock != nil {
		m.lock.Close()
	}
	m.dir.Close()
}

func (d *Dir) OpenManagement() (*Managed, error) {
	dir, err := d.PrivateDir(Management, false)
	if err != nil {
		return nil, err
	}
	m := &Managed{dir: dir}
	if err := m.open(); err != nil {
		m.Close()
		return nil, fmt.Errorf("management directory: %w", err)
	}
	return m, nil
}

func (m *Managed) open() error {
	if err := m.dir.Lock(); err != nil {
		return err
	}
	var err error
	m.lock, _, err = m.dir.read(LockFile, true)
	if err != nil {
		return err
	}
	if err := unix.Flock(int(m.lock.Fd()), unix.LOCK_EX|unix.LOCK_NB); err != nil {
		return err
	}
	if err := m.dir.Names(func(name string) error {
		switch name {
		case LockFile, "state", "stage":
			return nil
		default:
			return fmt.Errorf("unexpected entry %q: %w", name, ErrType)
		}
	}); err != nil {
		return err
	}
	m.State, err = m.dir.PrivateDir("state", false)
	if err != nil {
		return err
	}
	m.Stage, err = m.dir.PrivateDir("stage", false)
	if err != nil {
		return err
	}
	if err := m.ValidateStateFiles(); err != nil {
		return err
	}
	count := 0
	var used int64
	return m.Stage.Names(func(name string) error {
		count++
		if count > MaxManagedCopies {
			return ErrLimit
		}
		if !StageName(name) {
			return fmt.Errorf("unexpected stage entry %q: %w", name, ErrType)
		}
		_, size, err := m.Stage.Info(name)
		if err != nil {
			return err
		}
		if size > MaxSize {
			return ErrLimit
		}
		used += size
		if used > MaxManagedBytes {
			return ErrLimit
		}
		return nil
	})
}

func (m *Managed) ValidateStateFiles() error {
	err := m.State.Names(func(name string) error {
		switch name {
		case RootID, StateDB, StateDB + "-wal", StateDB + "-shm", StateDB + "-journal":
		default:
			return fmt.Errorf("unexpected state entry %q: %w", name, ErrType)
		}
		_, _, err := m.State.Info(name)
		return err
	})
	if err != nil {
		return err
	}
	for _, name := range []string{StateDB, RootID} {
		if _, _, err := m.State.Info(name); err != nil {
			return err
		}
	}
	return nil
}

func (d *Dir) InitManagement(rootID string) (*Managed, error) {
	dir, err := d.PrivateDir(Management, true)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	state, err := dir.PrivateDir("state", true)
	if err != nil {
		return nil, err
	}
	defer state.Close()
	stage, err := dir.PrivateDir("stage", true)
	if err != nil {
		return nil, err
	}
	defer stage.Close()
	if err := createSmall(dir, LockFile, ""); err != nil {
		return nil, err
	}
	if err := createSmall(state, RootID, rootID); err != nil {
		return nil, err
	}
	if err := createSmall(state, StateDB, ""); err != nil {
		return nil, err
	}
	for _, parent := range []*Dir{stage, state, dir, d} {
		if err := parent.Sync(); err != nil {
			return nil, err
		}
	}
	return d.OpenManagement()
}

func createSmall(dir *Dir, name, text string) error {
	f, _, err := dir.Create(name)
	if err != nil {
		return err
	}
	_, writeErr := f.WriteString(text)
	var syncErr error
	if writeErr == nil {
		syncErr = f.Sync()
	}
	return errors.Join(writeErr, syncErr, f.Close())
}

func (m *Managed) MetadataSize() (int64, error) {
	_, size, err := m.dir.Info(LockFile)
	if err != nil {
		return 0, err
	}
	err = m.State.Names(func(name string) error {
		_, n, err := m.State.Info(name)
		size += n
		return err
	})
	return size, err
}
