package local

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"maps"
	"slices"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type NetworkRequest struct {
	Volume string
	Root   string
	Remote *protocol.Client
	notify func(event) error
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

var (
	errIncomingFiles = fmt.Errorf("incoming file count: %w", safefs.ErrLimit)
	errIncomingDirs  = fmt.Errorf("incoming directory count: %w", safefs.ErrLimit)
)

type networkSync struct {
	engine
	remote    *protocol.Client
	reg       *Registration
	sourceKey string
}

func runNetwork(ctx context.Context, req NetworkRequest, mode string) error {
	return withNetwork(ctx, req, func(n *networkSync) error {
		reg := n.reg
		if mode == "" && (reg == nil || !reg.Complete) {
			return errors.New("registration must complete before sync")
		}
		if mode != "" && reg != nil && reg.Mode != mode {
			return errors.New("resume registration using its original mode")
		}
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
		n.bindRegistration()
		if err := n.checkManifestHistory(manifest); err != nil {
			return err
		}
		if err := n.recoverFiles(); err != nil {
			return err
		}
		if mode != "" {
			if n.reg.Complete {
				return n.registrationHolds()
			}
			return n.finishRegistration()
		}
		if err := n.resumeResolutions(); err != nil {
			return err
		}
		return n.sync()
	})
}

func (n *networkSync) bindRegistration() {
	n.sourceKey = "hub-" + n.reg.Generation
	n.req.Source = n.sourceKey
	n.fetch = func(want safefs.Digest) ([]byte, error) { return n.remote.Get(n.ctx, n.reg.Generation, want) }
}

func withNetwork(ctx context.Context, req NetworkRequest, run func(*networkSync) error) (err error) {
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
	n := networkSync{engine: engine{ctx: ctx, destination: root, db: db, notify: req.notify}, remote: req.Remote, reg: reg}
	return run(&n)
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
	// Persisted deletions can release space and structure needed by a
	// persisted install. Keep the original order within each class.
	slices.SortStableFunc(plans, func(a, b Plan) int {
		if (a.Target.Hash == "") == (b.Target.Hash == "") {
			return 0
		}
		if a.Target.Hash == "" {
			return -1
		}
		return 1
	})
	for _, p := range plans {
		if p.Resolution || p.Settled || p.Status == "rejected" {
			continue
		}
		var err error
		if p.Status == "conflict" {
			err = n.retainConflict(p)
		} else if p.Status == "pending" {
			err = n.apply(p, true)
		} else {
			err = n.cleanupBackup(p)
		}
		switch {
		case errors.Is(err, safefs.ErrStructure) && p.Status == "pending":
			err = n.db.setHold(n.ctx, p.Path, "apply_structure")
		case n.reg.Complete && p.Status == "pending" && incomingLimitReason(err) != "":
			err = n.db.setHold(n.ctx, p.Path, incomingLimitReason(err))
		default:
			err = n.handleApplyHold(err)
		}
		if err != nil {
			return err
		}
	}
	return nil
}

func (n *networkSync) sync() error {
	out, err := n.db.outbox(n.ctx)
	if err != nil {
		return err
	}
	rejected := make(map[string]Outgoing)
	deferred := make(map[string]bool)
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
		if storageFull(sendErr) {
			deferred[item.Change.Path] = true
			sendErr = n.db.setHold(n.ctx, item.Change.Path, "send_capacity")
		}
		if sendErr != nil {
			return fmt.Errorf("resume %q: %w", item.Change.Path, sendErr)
		}
	}
	pending, err := n.compare(rejected, nil, deferred)
	if err != nil {
		return err
	}
	if len(pending) > 0 {
		// The original candidates stay saved until a fresh decision succeeds
		// or the corresponding path is explicitly held.
		last, err := n.compare(pending, pending, deferred)
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

func hubPaths(m protocol.Manifest) map[string]protocol.Entry {
	paths := make(map[string]protocol.Entry, len(m.Entries))
	for _, e := range m.Entries {
		paths[e.Path] = e
	}
	return paths
}

func (n *networkSync) checkManifestHistory(m protocol.Manifest) error {
	entries := hubPaths(m)
	bases, err := n.db.bases(n.ctx)
	if err != nil {
		return err
	}
	for path, base := range bases {
		e := entries[path]
		if e.Revision < base.Revision || (e.Revision == base.Revision && digestOf(e) != base.Digest) {
			return fmt.Errorf("hub baseline regressed or disagrees for %q; reconcile required", path)
		}
	}
	conflicts, err := n.db.conflicts(n.ctx, true)
	if err != nil {
		return err
	}
	for _, c := range conflicts {
		e, latest := entries[c.Path], c.latest()
		if e.Revision < latest.Revision || (e.Revision == latest.Revision && digestOf(e) != latest.Digest) {
			return fmt.Errorf("hub conflict version regressed or disagrees for %q; reconcile required", c.Path)
		}
	}
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return err
	}
	for _, p := range plans {
		if p.Status != "pending" || p.Source != n.sourceKey {
			continue
		}
		e := entries[p.Path]
		if e.Revision < p.Target.Revision || (e.Revision == p.Target.Revision && digestOf(e) != p.Target.Digest) {
			return fmt.Errorf("hub apply target regressed or disagrees for %q; reconcile required", p.Path)
		}
	}
	return nil
}

func (n *networkSync) compare(rejected map[string]Outgoing, only map[string]Outgoing, deferred map[string]bool) (map[string]Outgoing, error) {
	snapshot, err := n.scan()
	if err != nil {
		return nil, err
	}
	m, err := n.manifest()
	if err != nil {
		return nil, err
	}
	if err := n.checkManifestHistory(m); err != nil {
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
	conflicts, err := n.db.conflicts(n.ctx, true)
	if err != nil {
		return nil, err
	}
	active := make(map[string]*Conflict, len(conflicts))
	paths := make(map[string]bool)
	for i := range conflicts {
		active[conflicts[i].Path] = &conflicts[i]
		paths[conflicts[i].Path] = true
	}
	hubEntries := hubPaths(m)
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return nil, err
	}
	badApply := make(map[string]string)
	for _, p := range plans {
		if p.Status == "rejected" && !p.Settled {
			badApply[p.Path] = "apply_rejected"
		}
		if !p.Resolution && p.Status == "pending" {
			switch reason := holds[p.Path]; reason {
			case "apply_structure", "incoming_file_limit", "incoming_directory_limit":
				badApply[p.Path] = reason
			}
		}
	}
	for _, e := range m.Entries {
		paths[e.Path] = true
	}
	for path := range snapshot.Files {
		paths[path] = true
	}
	for path := range bases {
		paths[path] = true
	}
	for path := range holds {
		paths[path] = true
	}
	for path := range rejected {
		paths[path] = true
	}
	ordered := slices.Sorted(maps.Keys(paths))
	decisions := make(map[string]string)
	protected := make(map[string]bool)
	for _, path := range ordered {
		base, l, h := bases[path], snapshot.Files[path], hubEntries[path]
		var action string
		switch {
		case active[path] != nil:
			action = active[path].Reason
		case badApply[path] != "":
			action = badApply[path]
		case deferred[path]:
			action = "send_capacity"
		case holds[path] == "registration_local_edit" && l != base.Digest:
			action = "registration_local_edit"
		case holds[path] == "both_changed":
			action = "both_changed"
		case h.Revision == base.Revision && l == base.Digest:
			action = "none"
		case h.Revision == base.Revision:
			action = "send"
		case l == digestOf(h):
			action = "adopt"
		case l == base.Digest:
			action = "receive"
		default:
			action = "both_changed"
		}
		// A directory in place of a known live file is a structural
		// change, not evidence of a user deleting the file.
		if base.Hash != "" && l.Hash == "" && action == "send" && structurallyAbsent(snapshot, path) {
			action = "namespace_conflict"
		}
		decisions[path] = action
		if action != "none" && action != "adopt" && action != "send" && action != "receive" {
			protected[path] = true
		}
	}
	selected := func(path string) bool {
		_, ok := only[path]
		return only == nil || ok
	}
	if err := n.blockStructure(snapshot, m.Entries, decisions, protected, selected, true); err != nil {
		return nil, err
	}
	next := make(map[string]Outgoing)
	for _, deleting := range []bool{true, false} {
		var rejudgedGroups map[string]bool
		if !deleting {
			// Re-read after the deletion pass. A held or rejected deletion
			// cannot make a dependent install eligible.
			snapshot, err = n.scan()
			if err != nil {
				return nil, err
			}
			if err := n.blockStructure(snapshot, m.Entries, decisions, protected, selected, false); err != nil {
				return nil, err
			}
			if only != nil {
				rejudgedGroups = make(map[string]bool, len(only))
				for path := range only {
					rejudgedGroups[structuralHoldRoot(path, decisions)] = true
				}
			}
		}
		for _, path := range ordered {
			action := decisions[path]
			prior, rejudged := rejected[path]
			base, l, h := bases[path], snapshot.Files[path], hubEntries[path]
			isDelete := (action == "send" && l.Hash == "") || (action == "receive" && h.Deleted)
			if deleting != isDelete {
				continue
			}
			if only != nil && !rejudged {
				// Report the related paths without replaying their work or
				// replacing a hold for an existing conflict or apply plan.
				if action == "namespace_conflict" && holds[path] == "" &&
					rejudgedGroups[structuralHoldRoot(path, decisions)] {
					if err := n.db.setHold(n.ctx, path, action); err != nil {
						return nil, err
					}
				}
				continue
			}
			switch action {
			case "none", "adopt", "send", "receive":
			default:
				if action == "both_changed" || active[path] != nil {
					c := active[path]
					var item *Outgoing
					if rejudged {
						item = &prior
					}
					if c == nil {
						c, err = n.makeConflict(path, action, base, localCandidate(path, snapshot, base), h, item)
					} else {
						err = n.updateHubCandidate(c, h)
					}
					if err != nil {
						return nil, err
					}
					if err := n.saveCandidates(c, item); err != nil {
						return nil, err
					}
				} else if holds[path] != action {
					if err := n.db.setHold(n.ctx, path, action); err != nil {
						return nil, err
					}
				}
				continue
			}
			if action == "receive" && !h.Deleted {
				if err := checkIncomingSpace(snapshot, []string{path}); err != nil {
					if err := n.db.setHold(n.ctx, path, incomingLimitReason(err)); err != nil {
						return nil, err
					}
					continue
				}
				if snapshot.Dirs[path] {
					if err := n.pruneOwned(path, snapshot); err != nil {
						return nil, err
					}
					if snapshot.Dirs[path] {
						if err := n.db.setHold(n.ctx, path, "namespace_conflict"); err != nil {
							return nil, err
						}
						continue
					}
				}
			}
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
			var actionErr error
			switch action {
			case "send":
				if l.Hash == "" {
					if err := n.confirmAbsence(path); err != nil {
						return nil, err
					}
				}
				item, err := n.prepareOutgoing(path, base.Revision, l)
				if err != nil {
					return nil, err
				}
				actionErr = n.send(item)
				if storageFull(actionErr) {
					deferred[path], protected[path] = true, true
					actionErr = n.db.setHold(n.ctx, path, "send_capacity")
				} else if errors.Is(actionErr, errRejudge) {
					item.Status = "rejected"
					next[path] = item
					if deleting {
						protected[path] = true
					}
					actionErr = nil
				} else if deleting && actionErr == nil {
					// Our successful deletion releases a namespace blocker.
					// A remote recreation will still be subject to hub CAS.
					for i := range m.Entries {
						if m.Entries[i].Path == path {
							m.Entries[i].Deleted = true
							break
						}
					}
				}
			case "adopt":
				actionErr = n.adopt(path, base, Base{Revision: h.Revision, Digest: l})
			case "receive":
				actionErr = n.install(path, base, Base{Revision: h.Revision, Digest: digestOf(h)})
				if _, held := actionErr.(*applyHold); held {
					actionErr = n.handleApplyHold(actionErr)
					decisions[path], holds[path], protected[path] = "apply_local_edit", "apply_local_edit", true
				} else if errors.Is(actionErr, safefs.ErrLimit) {
					actionErr = n.db.setHold(n.ctx, path, "apply_pending")
					decisions[path], holds[path], protected[path] = "apply_pending", "apply_pending", true
				} else if actionErr == nil && !h.Deleted {
					recordInstallation(snapshot, path, digestOf(h))
				}
			}
			if actionErr != nil {
				return nil, fmt.Errorf("%s %q: %w", action, path, actionErr)
			}
		}
	}
	return next, nil
}

func blockedBy(path string, protected map[string]bool) bool {
	for p := range protected {
		if path != p && relatedPath(path, p) {
			return true
		}
	}
	return false
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
	if err != nil && !(target.Hash == "" && errors.Is(err, safefs.ErrStructure)) {
		return err
	}
	if d != target.Digest {
		return fmt.Errorf("%w: %q changed during baseline adoption", ErrConflict, path)
	}
	if d.Hash != "" {
		if err := n.destination.SyncUser(path, id); err != nil {
			return err
		}
	}
	if err := n.destination.SyncExistingParents(path); err != nil {
		return err
	}
	return n.db.write(n.ctx, func(tx *sql.Tx) error { return putBase(n.ctx, tx, path, base.Revision, target) })
}

func (n *networkSync) install(path string, base, target Base) error {
	return n.installPlan(Plan{Path: path, Source: n.sourceKey, BaseRevision: base.Revision, Before: base.Digest, Target: target, Status: "pending"})
}

func (n *networkSync) installPlan(p Plan) error {
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
	if p.ID == "" {
		p.ID, err = newID()
		if err != nil {
			return err
		}
	}
	if p.Target.Hash != "" {
		count++
		used += p.Target.Size
	}
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
	if err := n.emit("plan-committed", p); err != nil {
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
	if count > safefs.MaxFiles {
		return errIncomingFiles
	}
	if dirs > safefs.MaxDirs {
		return errIncomingDirs
	}
	return nil
}

func incomingLimitReason(err error) string {
	switch {
	case errors.Is(err, errIncomingFiles):
		return "incoming_file_limit"
	case errors.Is(err, errIncomingDirs):
		return "incoming_directory_limit"
	default:
		return ""
	}
}

func recordInstallation(s safefs.Snapshot, path string, digest safefs.Digest) {
	s.Files[path] = digest
	for i := strings.LastIndexByte(path, '/'); i >= 0; i = strings.LastIndexByte(path, '/') {
		path = path[:i]
		s.Dirs[path] = true
	}
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
