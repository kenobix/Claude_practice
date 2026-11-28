package local

import (
	"context"
	"errors"
	"fmt"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

var (
	ErrConflict = errors.New("conflict pending")
	ErrHash     = errors.New("candidate size or SHA-256 mismatch")
	ErrRevision = errors.New("invalid target revision")
)

type Request struct {
	Volume      string
	Source      string
	Destination string
	Path        string
	Revision    int64
}

type Status struct {
	Bases            map[string]Base   `json:"baseline"`
	Plans            []Plan            `json:"plans"`
	Registration     *Registration     `json:"registration,omitempty"`
	RegistrationPlan map[string]Base   `json:"registration_plan,omitempty"`
	Outbox           []Outgoing        `json:"outbox,omitempty"`
	Holds            map[string]string `json:"holds,omitempty"`
}

func Init(ctx context.Context, volume, path string) (err error) {
	v, err := safefs.OpenVolume(volume)
	if err != nil {
		return err
	}
	defer v.Close()
	root, err := v.LockRoot(path)
	if err != nil {
		return err
	}
	defer root.Close()
	if _, err := root.Scan(); err != nil {
		return err
	}
	rootID, err := newID()
	if err != nil {
		return err
	}
	manager, err := root.InitManagement(rootID)
	if err != nil {
		return err
	}
	defer manager.Close()
	db, err := openState(ctx, manager, true)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.close()) }()
	return manager.State.Sync()
}

func Scan(volume, path string) ([]safefs.Entry, error) {
	v, err := safefs.OpenVolume(volume)
	if err != nil {
		return nil, err
	}
	defer v.Close()
	root, err := v.LockRoot(path)
	if err != nil {
		return nil, err
	}
	defer root.Close()
	s, err := root.Scan()
	if err != nil {
		return nil, err
	}
	return s.Entries(), nil
}

func Inspect(ctx context.Context, volume, path string) (status Status, err error) {
	v, err := safefs.OpenVolume(volume)
	if err != nil {
		return Status{}, err
	}
	defer v.Close()
	root, err := v.LockRoot(path)
	if err != nil {
		return Status{}, err
	}
	defer root.Close()
	manager, err := root.OpenManagement()
	if err != nil {
		return Status{}, err
	}
	defer manager.Close()
	db, err := openState(ctx, manager, false)
	if err != nil {
		return Status{}, err
	}
	defer func() { err = errors.Join(err, db.close()) }()
	status.Plans, err = db.plans(ctx)
	if err != nil {
		return Status{}, err
	}
	status.Bases, err = db.bases(ctx)
	if err != nil {
		return Status{}, err
	}
	status.Registration, err = db.registration(ctx)
	if err != nil {
		return Status{}, err
	}
	if status.Registration != nil && !status.Registration.Complete {
		items, err := db.registrationItems(ctx)
		if err != nil {
			return Status{}, err
		}
		status.RegistrationPlan = make(map[string]Base, len(items))
		for _, item := range items {
			status.RegistrationPlan[item.path] = item.base
		}
	}
	status.Outbox, err = db.outbox(ctx)
	if err != nil {
		return Status{}, err
	}
	status.Holds, err = db.holds(ctx)
	return status, err
}

func (s *stateDB) bases(ctx context.Context) (map[string]Base, error) {
	bases := make(map[string]Base)
	rows, err := s.conn.QueryContext(ctx, "SELECT path,revision,hash,size FROM baseline ORDER BY path")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var path string
		var base Base
		if err := rows.Scan(&path, &base.Revision, &base.Hash, &base.Size); err != nil {
			return nil, err
		}
		if err := safefs.Validate(path); err != nil {
			return nil, err
		}
		if len(bases) >= planLimit {
			return nil, safefs.ErrLimit
		}
		bases[path] = base
	}
	return bases, rows.Err()
}

func Copy(ctx context.Context, req Request) error {
	return run(ctx, req, false, nil)
}

func Recover(ctx context.Context, req Request) error {
	return run(ctx, req, true, nil)
}

type event struct {
	point string
	plan  Plan
}

type engine struct {
	ctx         context.Context
	req         Request
	source      *safefs.Dir
	destination *safefs.Dir
	db          *stateDB
	notify      func(event) error
	fetch       func(safefs.Digest) ([]byte, error)
}

func run(ctx context.Context, req Request, recoveryOnly bool, notify func(event) error) (err error) {
	if err := safefs.Validate(req.Source); err != nil {
		return err
	}
	if err := safefs.Validate(req.Destination); err != nil {
		return err
	}
	if req.Source == req.Destination || strings.HasPrefix(req.Source, req.Destination+"/") ||
		strings.HasPrefix(req.Destination, req.Source+"/") {
		return errors.New("source and destination roots overlap")
	}
	if !recoveryOnly {
		if req.Revision <= 0 {
			return ErrRevision
		}
		if req.Path != "" {
			if err := safefs.Validate(req.Path); err != nil {
				return err
			}
		}
	}
	v, err := safefs.OpenVolume(req.Volume)
	if err != nil {
		return err
	}
	defer v.Close()
	source, err := v.LockRoot(req.Source)
	if err != nil {
		return err
	}
	defer source.Close()
	destination, err := v.LockRoot(req.Destination)
	if err != nil {
		return err
	}
	defer destination.Close()
	input, err := source.Scan()
	if err != nil {
		return fmt.Errorf("source scan: %w", err)
	}
	output, err := destination.Scan()
	if err != nil {
		return fmt.Errorf("destination scan: %w", err)
	}
	manager, err := destination.OpenManagement()
	if err != nil {
		return fmt.Errorf("open destination state: %w", err)
	}
	defer manager.Close()
	db, err := openState(ctx, manager, false)
	if err != nil {
		return err
	}
	defer func() { err = errors.Join(err, db.close()) }()
	reg, err := db.registration(ctx)
	if err != nil {
		return err
	}
	if reg != nil {
		return errors.New("registered root cannot be a manual copy or recovery destination")
	}
	e := engine{ctx: ctx, req: req, source: source, destination: destination, db: db, notify: notify}
	plans, err := db.plans(ctx)
	if err != nil {
		return err
	}
	if _, _, err := db.usage(ctx, plans); err != nil {
		return err
	}
	recovered, err := e.recover(plans)
	if err != nil || recoveryOnly {
		return err
	}
	if recovered {
		output, err = destination.ScanManaged(manager)
		if err != nil {
			return fmt.Errorf("destination rescan: %w", err)
		}
		plans, err = db.plans(ctx)
		if err != nil {
			return err
		}
	}
	return e.copy(input, output, plans)
}

func (e *engine) recover(plans []Plan) (bool, error) {
	var recovered bool
	for _, p := range plans {
		var err error
		switch p.Status {
		case "pending", "conflict":
			if p.Source != e.req.Source {
				return false, fmt.Errorf("plan for %q requires source %q", p.Path, p.Source)
			}
		}
		switch p.Status {
		case "pending":
			err = e.apply(p, true)
			recovered = true
		case "conflict":
			err = e.retainConflict(p)
		case "complete":
			err = e.cleanupBackup(p)
		}
		if err != nil {
			return false, fmt.Errorf("recover %q: %w", p.Path, err)
		}
	}
	return recovered, nil
}

// prepare works from the complete snapshots and rejects structural collisions
// and prospective live limits before adding any new plan.
func (e *engine) prepare(input, output safefs.Snapshot) ([]Plan, error) {
	entries := input.Entries()
	if e.req.Path != "" {
		digest, ok := input.Files[e.req.Path]
		if !ok {
			return nil, fmt.Errorf("source file %q is missing", e.req.Path)
		}
		entries = []safefs.Entry{{Path: e.req.Path, Digest: digest}}
	}
	count := len(output.Files)
	dirs := len(output.Dirs)
	newDirs := make(map[string]bool)
	plans := make([]Plan, 0, len(entries))
	for _, entry := range entries {
		if output.Dirs[entry.Path] {
			return nil, fmt.Errorf("%q is a directory in destination: %w", entry.Path, safefs.ErrStructure)
		}
		parent := entry.Path
		for {
			slash := strings.LastIndexByte(parent, '/')
			if slash < 0 {
				break
			}
			parent = parent[:slash]
			if _, blocked := output.Files[parent]; blocked {
				return nil, fmt.Errorf("%q blocks destination path %q: %w", parent, entry.Path, safefs.ErrStructure)
			}
			if !output.Dirs[parent] && !newDirs[parent] {
				newDirs[parent] = true
				dirs++
			}
		}
		before, exists := output.Files[entry.Path]
		if !exists {
			count++
		}
		base, err := e.db.lookup(e.ctx, entry.Path)
		if err != nil {
			return nil, err
		}
		target := Base{Revision: e.req.Revision, Digest: entry.Digest}
		if target == base && before == base.Digest {
			continue
		}
		if target.Revision <= base.Revision {
			return nil, fmt.Errorf("%q has baseline %d, requested %d: %w",
				entry.Path, base.Revision, target.Revision, ErrRevision)
		}
		plans = append(plans, Plan{Path: entry.Path, Source: e.req.Source, BaseRevision: base.Revision,
			Before: before, Target: target, Status: "pending"})
	}
	if count > safefs.MaxFiles || dirs > safefs.MaxDirs {
		return nil, fmt.Errorf("projected destination limits: %w", safefs.ErrLimit)
	}
	return plans, nil
}

func (e *engine) copy(input, output safefs.Snapshot, existing []Plan) error {
	requested, err := e.prepare(input, output)
	if err != nil {
		return err
	}
	if len(existing)+len(requested) > planLimit {
		return fmt.Errorf("plan count: %w", safefs.ErrLimit)
	}
	count, size, err := e.db.usage(e.ctx, existing)
	if err != nil {
		return err
	}
	for _, p := range requested {
		reserveCount, reserveBytes := count+1, size+p.Target.Size
		if p.needsBackup() {
			reserveCount++
			reserveBytes += p.Before.Size
		}
		if err := checkCopyBudget(reserveCount, reserveBytes); err != nil {
			return err
		}
		p.ID, err = newID()
		if err != nil {
			return err
		}
		if err := e.db.add(e.ctx, p); err != nil {
			return err
		}
		if err := e.apply(p, false); err != nil {
			return fmt.Errorf("apply %q: %w", p.Path, err)
		}
	}
	return nil
}

func newID() (string, error) { return protocol.NewID() }
