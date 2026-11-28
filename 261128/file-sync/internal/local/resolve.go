package local

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type ResolveRequest struct {
	Path       string
	Generation string
	Revision   int64
	Choice     string
	File       string
}

type resolution struct {
	State    string            `json:"state"`
	Change   protocol.Change   `json:"change"`
	Before   safefs.Digest     `json:"before"`
	Selected ConflictCandidate `json:"selected"`
	Receipt  *protocol.Receipt `json:"receipt,omitempty"`
}

func Resolve(ctx context.Context, req NetworkRequest, choice ResolveRequest) error {
	if err := safefs.Validate(choice.Path); err != nil {
		return err
	}
	if !protocol.IsHex(choice.Generation, 32) || choice.Revision < 0 {
		return errors.New("require the displayed hub generation and revision")
	}
	switch choice.Choice {
	case "local", "hub", "delete":
		if choice.File != "" {
			return errors.New("candidate path requires --choose file")
		}
	case "file":
		if err := safefs.Validate(choice.File); err != nil {
			return err
		}
	default:
		return errors.New("choose local, hub, delete, or file")
	}
	return withNetwork(ctx, req, func(n *networkSync) error {
		m, s, err := n.conflictContext()
		if err != nil {
			return err
		}
		list, err := n.db.conflicts(ctx, true)
		if err != nil {
			return err
		}
		c := findConflict(list, choice.Path)
		if c == nil {
			return errors.New("no active content conflict at path")
		}
		h := hubPaths(m)[c.Path]
		if c.Resolution != nil && (c.Resolution.State == "ready" || c.Resolution.State == "accepted") {
			return fmt.Errorf("%w: resume the existing resolution with sync-once", ErrConflict)
		}
		if err := n.updateHubCandidate(c, h); err != nil {
			return err
		}
		if err := n.saveCandidates(c, nil); err != nil {
			return err
		}
		if choice.Generation != c.Generation || choice.Revision != h.Revision {
			return fmt.Errorf("%w: hub HTTP 409: revision_mismatch; review conflicts and choose again", ErrConflict)
		}
		if structuralPaths(s, m.Entries)[c.Path] || s.Dirs[c.Path] {
			return fmt.Errorf("%w: organize the conflicting paths and recheck first", ErrConflict)
		}
		for _, other := range list {
			if other.Path != c.Path && relatedPath(c.Path, other.Path) {
				return fmt.Errorf("%w: related path %q is held", ErrConflict, other.Path)
			}
		}
		return n.choose(c, choice, s)
	})
}

func ListConflicts(ctx context.Context, req NetworkRequest) (list []Conflict, err error) {
	err = withNetwork(ctx, req, func(n *networkSync) error {
		m, snapshot, err := n.conflictContext()
		if err != nil {
			return err
		}
		list, err = n.db.conflicts(ctx, false)
		if err != nil {
			return err
		}
		entries := hubPaths(m)
		for i := range list {
			c := &list[i]
			if c.State == "resolved" {
				continue
			}
			if err := n.updateHubCandidate(c, entries[c.Path]); err != nil {
				return err
			}
			if err := n.saveCandidates(c, nil); err != nil {
				return err
			}
		}
		bases, err := n.db.bases(n.ctx)
		if err != nil {
			return err
		}
		holds, err := n.db.holds(n.ctx)
		if err != nil {
			return err
		}
		for path, reason := range holds {
			if reason != "namespace_conflict" && reason != "apply_structure" {
				continue
			}
			base := bases[path]
			list = append(list, Conflict{Path: path, Generation: m.Generation, Reason: reason, State: "structural_hold",
				Base: baseCandidate(base), Local: localCandidate(path, snapshot, base), Hub: []ConflictCandidate{hubCandidate(entries[path])}})
		}
		slices.SortStableFunc(list, func(a, b Conflict) int { return strings.Compare(a.Path, b.Path) })
		if list == nil {
			list = []Conflict{}
		}
		return nil
	})
	return list, err
}

func (n *networkSync) conflictContext() (protocol.Manifest, safefs.Snapshot, error) {
	if n.reg == nil || !n.reg.Complete {
		return protocol.Manifest{}, safefs.Snapshot{}, errors.New("registration must complete before conflict operations")
	}
	n.bindRegistration()
	m, err := n.manifest()
	if err != nil {
		return m, safefs.Snapshot{}, err
	}
	if err := n.checkManifestHistory(m); err != nil {
		return m, safefs.Snapshot{}, err
	}
	s, err := n.scan()
	return m, s, err
}

func (n *networkSync) choose(c *Conflict, selection ResolveRequest, s safefs.Snapshot) error {
	current := localCandidate(c.Path, s, Base{Revision: c.Base.Revision, Digest: c.Base.Digest})
	if current.Kind != c.Local.Kind || current.Digest != c.Local.Digest {
		seen := false
		for _, e := range c.Edits {
			seen = seen || (e.Kind == current.Kind && e.Digest == current.Digest)
		}
		if !seen {
			if len(c.Edits) >= planLimit {
				return safefs.ErrLimit
			}
			c.Edits = append(c.Edits, current)
			if err := n.writeConflict(c); err != nil {
				return err
			}
			if err := n.saveCandidate(c, &c.Edits[len(c.Edits)-1], n.destination, nil); err != nil {
				return err
			}
		}
	}
	if !c.Saved {
		return fmt.Errorf("%w: candidate preservation incomplete", ErrConflict)
	}
	if err := n.verifyRetained(c); err != nil {
		return err
	}
	selected := c.Local
	switch selection.Choice {
	case "hub":
		selected = *c.latest()
	case "delete":
		selected = candidateOf(safefs.Digest{}, 0, "deleted")
	case "file":
		d := s.Files[selection.File]
		if d.Hash == "" {
			return errors.New("candidate is not a scanned regular file")
		}
		// Persist the explicit file candidate and copy it under the root lock.
		selected = candidateOf(d, 0, "file")
		if err := n.saveExternal(c, selection.File, &selected); err != nil {
			return err
		}
	}
	if selected.Kind == "directory" {
		return fmt.Errorf("%w: a directory is not a file candidate", ErrConflict)
	}
	if selected.Hash == "" && c.latest().Revision == 0 {
		// Both sides must be absent before an unregistered path can be left
		// absent. The hub protocol rejects a delete of an unknown path.
		if current.Hash != "" {
			return fmt.Errorf("%w: hub path is unregistered; preserve the local file under a different name and reconcile", ErrConflict)
		}
		return n.finishConflict(c, nil)
	}
	item, err := outgoing(n.reg, c.Path, selection.Revision, selected.Digest)
	if err != nil {
		return err
	}
	if c.Resolution != nil {
		c.PriorResolutions = append(c.PriorResolutions, *c.Resolution)
	}
	if len(c.PriorResolutions) >= protocol.MaxReceipts {
		return safefs.ErrLimit
	}
	c.State = "resolving"
	c.Resolution = &resolution{State: "ready", Change: item.Change, Before: current.Digest, Selected: selected}
	if err := n.writeConflict(c); err != nil {
		return err
	}
	return n.runResolution(c)
}

func (n *networkSync) saveExternal(c *Conflict, path string, selected *ConflictCandidate) error {
	if len(c.Edits) >= planLimit {
		return safefs.ErrLimit
	}
	// Use the regular candidate writer with a different read source while
	// keeping the persisted conflict key and all previous copies.
	v := *selected
	v.Source = path
	c.Edits = append(c.Edits, v)
	if err := n.writeConflict(c); err != nil {
		return err
	}
	last := &c.Edits[len(c.Edits)-1]
	if err := n.saveCandidate(c, last, n.destination, nil); err != nil {
		return err
	}
	*selected = *last
	if !selected.Saved {
		return fmt.Errorf("%w: explicit candidate preservation incomplete", ErrConflict)
	}
	return nil
}

func (n *networkSync) readSaved(name string, want safefs.Digest) ([]byte, error) {
	var data bytes.Buffer
	got, _, err := n.db.manager.Stage.ReadPrivate(name, &data)
	if err != nil {
		return nil, err
	}
	if got != want {
		return nil, ErrHash
	}
	return data.Bytes(), nil
}

func (n *networkSync) resumeResolutions() error {
	conflicts, err := n.db.conflicts(n.ctx, true)
	if err != nil {
		return err
	}
	for i := range conflicts {
		c := &conflicts[i]
		if c.Resolution == nil || (c.Resolution.State != "ready" && c.Resolution.State != "accepted") {
			continue
		}
		if err := n.runResolution(c); err != nil && !errors.Is(err, errResolutionHeld) {
			return err
		}
	}
	return nil
}

var errResolutionHeld = errors.New("resolution remains held")

func (n *networkSync) runResolution(c *Conflict) error {
	r := c.Resolution
	if r.Change.Generation != n.reg.Generation || r.Change.Client != n.reg.Client {
		return errors.New("resolution belongs to another registration")
	}
	if r.State == "ready" {
		result, err := n.postResolution(r)
		if err != nil {
			var p *protocol.Problem
			if !errors.As(err, &p) || (p.Code != "revision_mismatch" && p.Code != "namespace_conflict") {
				return err
			}
			c.State, r.State = "pending", "stale"
			if err := n.writeConflict(c); err != nil {
				return err
			}
			m, err := n.manifest()
			if err != nil {
				return err
			}
			if err := n.checkManifestHistory(m); err != nil {
				return err
			}
			if err := n.updateHubCandidate(c, hubPaths(m)[c.Path]); err != nil {
				return err
			}
			if err := n.saveCandidates(c, nil); err != nil {
				return err
			}
			return errors.Join(ErrConflict, errResolutionHeld, p)
		}
		r.Receipt, r.State = &result, "accepted"
		if err := n.writeConflict(c); err != nil {
			return err
		}
	}
	return n.applyResolution(c)
}

func (n *networkSync) postResolution(r *resolution) (protocol.Receipt, error) {
	request, err := json.Marshal(r.Change)
	if err != nil {
		return protocol.Receipt{}, err
	}
	var body []byte
	if !r.Change.Deleted {
		body, err = n.readSaved(r.Selected.Copy, r.Selected.Digest)
		if err != nil {
			return protocol.Receipt{}, err
		}
	}
	result, err := n.remote.Post(n.ctx, r.Change, request)
	var p *protocol.Problem
	switch {
	case !r.Change.Deleted && errors.As(err, &p) && p.Code == "blob_missing":
		if err := n.remote.Put(n.ctx, n.reg.Generation, r.Selected.Digest, body); err != nil {
			return result, err
		}
	case !protocol.Retryable(err) || n.ctx.Err() != nil:
		return result, err
	}
	return n.remote.Post(n.ctx, r.Change, request)
}

func (n *networkSync) applyResolution(c *Conflict) error {
	r := c.Resolution
	if r.Receipt == nil || r.State != "accepted" {
		return errors.New("resolution has no confirmed receipt")
	}
	// The chosen bytes are already durable locally. Recovery uses that
	// immutable candidate even after the hub has advanced or is unreadable.
	apply := *n
	apply.fetch = func(want safefs.Digest) ([]byte, error) { return n.readSaved(r.Selected.Copy, want) }
	target := Base{Revision: r.Receipt.Entry.Revision, Digest: r.Selected.Digest}
	base, err := n.db.lookup(n.ctx, c.Path)
	if err != nil {
		return err
	}
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return err
	}
	for _, p := range plans {
		if p.ID != r.Change.Operation {
			continue
		}
		switch p.Status {
		case "complete":
			if err := n.cleanupBackup(p); err != nil {
				return err
			}
			return n.finishConflict(c, nil)
		case "pending":
			err := apply.apply(p, true)
			if _, held := err.(*applyHold); held {
				return n.reopenAfterAcceptance(c, base, target, p.ID)
			}
			if errors.Is(err, safefs.ErrStructure) {
				return errors.Join(ErrConflict, errResolutionHeld, err)
			}
			if err == nil {
				return n.finishConflict(c, nil)
			}
			return err
		case "conflict":
			if !p.Saved {
				if err := apply.retainConflict(p); err != nil {
					if _, saved := err.(*applyHold); !saved {
						return err
					}
				}
			}
			return n.reopenAfterAcceptance(c, base, target, p.ID)
		default:
			return fmt.Errorf("%w: resolution apply candidate needs inspection", ErrConflict)
		}
	}
	s, err := n.scan()
	if err != nil {
		return err
	}
	actual := s.Files[c.Path]
	if s.Dirs[c.Path] || actual != r.Before {
		return n.reopenAfterAcceptance(c, base, target)
	}
	if actual == target.Digest {
		if err := n.adopt(c.Path, base, target); err != nil {
			return err
		}
		return n.finishConflict(c, nil)
	}
	if structurallyAbsent(s, c.Path) {
		return errors.Join(ErrConflict, errResolutionHeld, safefs.ErrStructure)
	}
	if target.Hash != "" {
		if err := checkIncomingSpace(s, []string{c.Path}); err != nil {
			return fmt.Errorf("%w: %w", errResolutionHeld, err)
		}
	}
	p := Plan{ID: r.Change.Operation, Path: c.Path, Source: n.sourceKey, BaseRevision: base.Revision,
		Before: r.Before, Target: target, Status: "pending", Resolution: true}
	if err := apply.installPlan(p); err != nil {
		if _, held := err.(*applyHold); held {
			return n.reopenAfterAcceptance(c, base, target, p.ID)
		}
		if errors.Is(err, safefs.ErrLimit) {
			return fmt.Errorf("%w: %w", errResolutionHeld, err)
		}
		return err
	}
	return n.finishConflict(c, nil)
}

func (n *networkSync) reopenAfterAcceptance(c *Conflict, previous, target Base, plans ...string) error {
	s, err := n.scan()
	if err != nil {
		return err
	}
	id, err := newID()
	if err != nil {
		return err
	}
	next := &Conflict{ID: id, Path: c.Path, Generation: c.Generation, Reason: "post_resolution_edit", State: "pending",
		Base: baseCandidate(target), Local: localCandidate(c.Path, s, target), Hub: []ConflictCandidate{hubCandidate(c.Resolution.Receipt.Entry)}}
	err = n.finishConflict(c, func(tx *sql.Tx) error {
		if previous != target {
			if err := putBase(n.ctx, tx, c.Path, previous.Revision, target); err != nil {
				return err
			}
		}
		for _, id := range plans {
			if err := settlePlan(n.ctx, tx, id); err != nil {
				return err
			}
		}
		if err := putConflict(n.ctx, tx, next); err != nil {
			return err
		}
		return putHold(n.ctx, tx, c.Path, next.Reason)
	})
	if err != nil {
		return err
	}
	if err := n.saveCandidates(next, nil); err != nil {
		return err
	}
	return errors.Join(ErrConflict, errResolutionHeld)
}

func (n *networkSync) finishConflict(c *Conflict, after func(*sql.Tx) error) error {
	out, err := n.db.outbox(n.ctx)
	if err != nil {
		return err
	}
	c.State = "resolved"
	if c.Resolution != nil {
		c.Resolution.State = "complete"
	}
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		if err := putConflict(n.ctx, tx, c); err != nil {
			return err
		}
		for _, o := range out {
			if o.Change.Path == c.Path && o.Status == "rejected" {
				if err := n.archiveRejected(tx, o); err != nil {
					return err
				}
			}
		}
		if err := mutateOne(n.ctx, tx, "DELETE FROM holds WHERE path=?", c.Path); err != nil {
			return err
		}
		if after != nil {
			return after(tx)
		}
		return nil
	})
}
