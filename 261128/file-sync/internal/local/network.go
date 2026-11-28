package local

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"maps"
	"slices"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type NetworkRequest struct {
	Volume string
	Root   string
	Remote *protocol.Client
}

func Register(ctx context.Context, req NetworkRequest, mode string) error {
	if mode != "seed" && mode != "pull" {
		return errors.New("select seed or pull registration")
	}
	return runNetwork(ctx, req, mode)
}

func Sync(ctx context.Context, req NetworkRequest) error {
	return runNetwork(ctx, req, "")
}

type networkSync struct {
	engine
	remote    *protocol.Client
	reg       *Registration
	sourceKey string
}

func runNetwork(ctx context.Context, req NetworkRequest, mode string) (err error) {
	if req.Remote == nil {
		return errors.New("missing hub connection")
	}
	volume, err := safefs.OpenVolume(req.Volume)
	if err != nil {
		return err
	}
	defer volume.Close()
	root, err := volume.LockRoot(req.Root)
	if err != nil {
		return err
	}
	defer root.Close()
	manager, err := root.OpenManagement()
	if err != nil {
		return fmt.Errorf("open client state: %w", err)
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
	if mode == "" && (reg == nil || !reg.Complete) {
		return errors.New("initial registration must complete before sync")
	}
	if mode != "" && reg != nil && reg.Mode != mode {
		return errors.New("resume registration using its original mode")
	}
	n := networkSync{engine: engine{ctx: ctx, destination: root, db: db}, remote: req.Remote, reg: reg}
	manifest, err := n.manifest()
	if err != nil {
		return err
	}
	snapshot, err := n.scan()
	if err != nil {
		return err
	}
	if reg == nil {
		if err := n.beginRegistration(mode, manifest, snapshot); err != nil {
			return err
		}
	}
	n.sourceKey = "hub-" + n.reg.Generation
	n.req.Source = n.sourceKey
	n.fetch = func(want safefs.Digest) ([]byte, error) { return n.remote.Get(ctx, n.reg.Generation, want) }
	if err := n.recoverFiles(); err != nil {
		return err
	}
	if mode != "" {
		if n.reg.Complete {
			return n.registrationHolds()
		}
		return n.finishRegistration()
	}
	return n.sync()
}

func (n *networkSync) manifest() (protocol.Manifest, error) {
	m, err := n.remote.Manifest(n.ctx)
	if err != nil {
		return m, err
	}
	switch {
	case n.reg == nil:
	case n.reg.Space != m.Space || n.reg.Client != m.Client:
		return m, errors.New("authenticated space or client differs from registration")
	case n.reg.Generation != m.Generation:
		return m, fmt.Errorf("%w: hub generation changed; explicit re-registration required", ErrConflict)
	}
	return m, nil
}

func (n *networkSync) scan() (safefs.Snapshot, error) {
	return n.destination.ScanManaged(n.db.manager)
}

func (n *networkSync) recoverFiles() error {
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return err
	}
	for _, p := range plans {
		if p.Source != n.sourceKey {
			return errors.New("persisted plan belongs to another source or generation")
		}
	}
	if _, _, err := n.db.usage(n.ctx, plans); err != nil {
		return err
	}
	_, err = n.recover(plans)
	return err
}

func (n *networkSync) sync() error {
	out, err := n.db.outbox(n.ctx)
	if err != nil {
		return err
	}
	rejected := make(map[string]Outgoing)
	for _, item := range out {
		if err := n.checkOutgoing(item); err != nil {
			return err
		}
		var sendErr error
		switch item.Status {
		case "done":
			sendErr = n.cleanupOutgoing(item)
		case "rejected":
			rejected[item.Change.Path] = item
		default:
			sendErr = n.send(item)
			if errors.Is(sendErr, errRejudge) {
				item.Status = "rejected"
				rejected[item.Change.Path] = item
				sendErr = nil
			}
		}
		if sendErr != nil {
			return fmt.Errorf("resume %q: %w", item.Change.Path, sendErr)
		}
	}
	pending, err := n.compare(rejected, nil)
	if err != nil {
		return err
	}
	if len(pending) > 0 {
		// The original candidates stay saved until a fresh decision succeeds
		// or the corresponding path is explicitly held.
		last, err := n.compare(pending, pending)
		if err != nil {
			return err
		}
		if len(last) > 0 {
			for _, path := range slices.Sorted(maps.Keys(last)) {
				if err := n.db.setHold(n.ctx, path, "revision_changed_again"); err != nil {
					return err
				}
			}
			return ErrConflict
		}
	}
	holds, err := n.db.holds(n.ctx)
	if err != nil {
		return err
	}
	if len(holds) > 0 {
		return fmt.Errorf("%w: %d path(s); see status", ErrConflict, len(holds))
	}
	return nil
}

func (n *networkSync) compare(rejected map[string]Outgoing, only map[string]Outgoing) (map[string]Outgoing, error) {
	snapshot, err := n.scan()
	if err != nil {
		return nil, err
	}
	m, err := n.manifest()
	if err != nil {
		return nil, err
	}
	bases, err := n.db.bases(n.ctx)
	if err != nil {
		return nil, err
	}
	holds, err := n.db.holds(n.ctx)
	if err != nil {
		return nil, err
	}
	hubEntries := make(map[string]protocol.Entry, len(m.Entries))
	paths := make(map[string]bool)
	for _, e := range m.Entries {
		hubEntries[e.Path] = e
		paths[e.Path] = true
	}
	for path := range snapshot.Files {
		paths[path] = true
	}
	for path, base := range bases {
		paths[path] = true
		entry := hubEntries[path]
		if entry.Revision < base.Revision ||
			(entry.Revision == base.Revision && (entry.Deleted || digestOf(entry) != base.Digest)) {
			return nil, fmt.Errorf("hub baseline regressed or disagrees for %q", path)
		}
	}
	for path := range holds {
		paths[path] = true
	}
	for path := range rejected {
		paths[path] = true
	}
	blocked := structuralPaths(snapshot, m.Entries)
	pulls := make([]string, 0)
	decisions := make(map[string]string)
	ordered := slices.Sorted(maps.Keys(paths))
	for _, path := range ordered {
		if only != nil {
			if _, ok := only[path]; !ok {
				continue
			}
		}
		base, l, h := bases[path], snapshot.Files[path], hubEntries[path]
		switch {
		case holds[path] == "registration_local_edit" && l != base.Digest:
			decisions[path] = "registration_local_edit"
		case holds[path] == "both_changed":
			decisions[path] = "both_changed"
		case blocked[path]:
			decisions[path] = "namespace_conflict"
		case h.Deleted:
			decisions[path] = "hub_delete_unsupported"
		case l.Hash == "" && base.Revision != 0:
			decisions[path] = "local_delete_unsupported"
		case h.Revision == base.Revision && l == base.Digest:
			decisions[path] = "none"
		case h.Revision == base.Revision:
			decisions[path] = "send"
		case l == digestOf(h):
			decisions[path] = "adopt"
		case l == base.Digest:
			decisions[path] = "receive"
			pulls = append(pulls, path)
		default:
			decisions[path] = "both_changed"
		}
	}
	if err := checkIncomingSpace(snapshot, pulls); err != nil {
		return nil, err
	}
	next := make(map[string]Outgoing)
	for _, path := range ordered {
		action, selected := decisions[path]
		if !selected {
			continue
		}
		prior, rejudged := rejected[path]
		switch action {
		case "none", "send", "adopt", "receive":
			if rejudged {
				if err := n.retireRejected(prior, action); err != nil {
					return nil, err
				}
				delete(rejected, path)
			}
			if _, ok := holds[path]; ok || rejudged {
				if err := n.clearHold(path); err != nil {
					return nil, err
				}
			}
		default:
			if holds[path] != action {
				if err := n.db.setHold(n.ctx, path, action); err != nil {
					return nil, err
				}
			}
			continue
		}
		base, l, h := bases[path], snapshot.Files[path], hubEntries[path]
		var actionErr error
		switch action {
		case "send":
			item, err := n.prepareOutgoing(path, base.Revision, l)
			if err != nil {
				return nil, err
			}
			actionErr = n.send(item)
			if errors.Is(actionErr, errRejudge) {
				item.Status = "rejected"
				if rejudged {
					actionErr = n.db.setHold(n.ctx, path, "revision_changed_again")
				} else {
					next[path] = item
					actionErr = nil
				}
			}
		case "adopt":
			actionErr = n.adopt(path, base, Base{Revision: h.Revision, Digest: l})
		case "receive":
			actionErr = n.install(path, base, Base{Revision: h.Revision, Digest: digestOf(h)})
		}
		if actionErr != nil {
			return nil, fmt.Errorf("%s %q: %w", action, path, actionErr)
		}
	}
	return next, nil
}

func digestOf(e protocol.Entry) safefs.Digest {
	if e.Hash == nil {
		return safefs.Digest{}
	}
	return safefs.Digest{Hash: *e.Hash, Size: e.Size}
}

func (n *networkSync) clearHold(path string) error {
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		return mutateOne(n.ctx, tx, "DELETE FROM holds WHERE path=?", path)
	})
}

func (n *networkSync) adopt(path string, base, target Base) error {
	d, id, err := n.destination.Observe(path)
	if err != nil {
		return err
	}
	if d != target.Digest {
		return fmt.Errorf("%w: %q changed during baseline adoption", ErrConflict, path)
	}
	if err := n.destination.SyncUser(path, id); err != nil {
		return err
	}
	if err := n.destination.SyncParents(path); err != nil {
		return err
	}
	return n.db.write(n.ctx, func(tx *sql.Tx) error { return putBase(n.ctx, tx, path, base.Revision, target) })
}

func (n *networkSync) install(path string, base, target Base) error {
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return err
	}
	if len(plans) >= planLimit {
		return safefs.ErrLimit
	}
	count, used, err := n.db.usage(n.ctx, plans)
	if err != nil {
		return err
	}
	id, err := newID()
	if err != nil {
		return err
	}
	p := Plan{ID: id, Path: path, Source: n.sourceKey, BaseRevision: base.Revision, Before: base.Digest, Target: target, Status: "pending"}
	count++
	used += target.Size
	if p.needsBackup() {
		count++
		used += p.Before.Size
	}
	if err := checkCopyBudget(count, used); err != nil {
		return err
	}
	if err := n.db.add(n.ctx, p); err != nil {
		return err
	}
	return n.apply(p, false)
}

// checkIncomingSpace projects live files and directories before creating plans.
func checkIncomingSpace(s safefs.Snapshot, incoming []string) error {
	count, dirs := len(s.Files), len(s.Dirs)
	newDirs := make(map[string]bool)
	for _, path := range incoming {
		if _, exists := s.Files[path]; !exists {
			count++
		}
		for i := 0; i < len(path); i++ {
			if path[i] == '/' && !s.Dirs[path[:i]] && !newDirs[path[:i]] {
				newDirs[path[:i]] = true
				dirs++
			}
		}
	}
	if count > safefs.MaxFiles || dirs > safefs.MaxDirs {
		return safefs.ErrLimit
	}
	return nil
}

func structuralPaths(s safefs.Snapshot, entries []protocol.Entry) map[string]bool {
	blocked := make(map[string]bool)
	hubFiles := make(map[string]bool)
	for _, e := range entries {
		if !e.Deleted {
			hubFiles[e.Path] = true
			if s.Dirs[e.Path] {
				blocked[e.Path] = true
			}
			for i := 0; i < len(e.Path); i++ {
				if e.Path[i] == '/' && s.Files[e.Path[:i]].Hash != "" {
					blocked[e.Path], blocked[e.Path[:i]] = true, true
				}
			}
		}
	}
	for path := range s.Files {
		for i := 0; i < len(path); i++ {
			if path[i] == '/' && hubFiles[path[:i]] {
				blocked[path], blocked[path[:i]] = true, true
			}
		}
	}
	return blocked
}
