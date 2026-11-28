package safefs

import (
	"fmt"
	"strings"

	"golang.org/x/sys/unix"
)

const (
	reconcilePrefix = "reconcile-"
	// Reserve room in the 128 MiB management budget for the live DB,
	// one bounded WAL transaction, and SHM.
	archiveBudget = 60 << 20
	maxArchives   = 16
)

type ManagedFile struct {
	Name string   `json:"name"`
	Size int64    `json:"size"`
	ID   Identity `json:"identity"`
}

func ReconcileDirName(name string) bool {
	id, ok := strings.CutPrefix(name, reconcilePrefix)
	return ok && len(id) == 32 && strings.Trim(id, "0123456789abcdef") == ""
}

func (m *Managed) StateInventory() ([]ManagedFile, error) {
	files := make([]ManagedFile, 0)
	archives := 0
	var bytes int64
	collect := func(name string, dir *Dir, archived bool) error {
		return dir.Names(func(file string) error {
			switch file {
			case RootID, StateDB, StateDB + "-wal", StateDB + "-shm", StateDB + "-journal":
			default:
				return fmt.Errorf("unexpected state entry %q: %w", file, ErrType)
			}
			id, size, err := dir.Info(file)
			if err != nil {
				return err
			}
			if archived {
				bytes += size
				if bytes > archiveBudget {
					return fmt.Errorf("archived state budget: %w", ErrLimit)
				}
			}
			files = append(files, ManagedFile{Name: name + "/" + file, Size: size, ID: id})
			return nil
		})
	}
	if err := m.dir.Names(func(name string) error {
		if !ReconcileDirName(name) {
			return nil
		}
		archives++
		if archives > maxArchives {
			return ErrLimit
		}
		dir, err := m.dir.PrivateDir(name, false)
		if err != nil {
			return err
		}
		defer dir.Close()
		return collect(name, dir, true)
	}); err != nil {
		return nil, err
	}
	if m.State != nil {
		if err := collect("state", m.State, false); err != nil {
			return nil, err
		}
	}
	return files, nil
}

// PrepareState writes a fresh identity and DB under a new private directory.
// The original state remains authoritative until ActivateState exchanges them.
func (m *Managed) PrepareState(id string) (*Managed, func(), error) {
	name := reconcilePrefix + id
	if !ReconcileDirName(name) {
		return nil, nil, ErrPath
	}
	files, err := m.StateInventory()
	if err != nil {
		return nil, nil, err
	}
	var total int64
	for _, f := range files {
		total += f.Size
	}
	// An interrupted preparation remains in the archive area. Reserve
	// one DB page and one WAL frame per page, plus room for sidecars.
	const preparationOverhead = 1 << 20
	pages := (archiveBudget - total - preparationOverhead) / (2*4096 + 24)
	if pages < 64 {
		return nil, nil, fmt.Errorf("state preparation budget: %w", ErrLimit)
	}
	archives := 0
	if err := m.dir.Names(func(name string) error {
		if ReconcileDirName(name) {
			archives++
		}
		return nil
	}); err != nil {
		return nil, nil, err
	}
	if archives >= maxArchives {
		return nil, nil, ErrLimit
	}
	state, err := m.dir.PrivateDir(name, true)
	if err != nil {
		return nil, nil, err
	}
	for _, f := range []struct{ name, value string }{{RootID, id}, {StateDB, ""}} {
		if err := createSmall(state, f.name, f.value); err != nil {
			state.Close()
			return nil, nil, err
		}
	}
	if err := m.dir.Sync(); err != nil {
		state.Close()
		return nil, nil, err
	}
	return &Managed{dir: m.dir, State: state, Stage: m.Stage, preparedPages: int(pages)}, state.Close, nil
}

func (m *Managed) PreparedPageLimit() int { return m.preparedPages }

func (m *Managed) ActivateState(id string, prepared *Managed) error {
	name := reconcilePrefix + id
	if !ReconcileDirName(name) {
		return ErrPath
	}
	check, err := m.dir.PrivateDir(name, false)
	if err != nil {
		return err
	}
	defer check.Close()
	id1, err := check.ID()
	if err != nil {
		return err
	}
	id2, err := prepared.State.ID()
	if err != nil {
		return err
	}
	if id1 != id2 {
		return ErrChanged
	}
	if err := prepared.State.Sync(); err != nil {
		return err
	}
	if _, err := m.StateInventory(); err != nil {
		return err
	}
	flags := uint(unix.RENAME_NOREPLACE)
	if m.State != nil {
		flags = unix.RENAME_EXCHANGE
	}
	if err := unix.Renameat2(m.dir.fd, name, m.dir.fd, "state", flags); err != nil {
		return err
	}
	if err := m.dir.Sync(); err != nil {
		return err
	}
	prepared.preparedPages = 0
	return nil
}
