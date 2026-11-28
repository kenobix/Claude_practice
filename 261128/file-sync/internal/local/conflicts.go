package local

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type ConflictCandidate struct {
	Kind     string `json:"kind"`
	Revision int64  `json:"revision"`
	safefs.Digest
	Copy    string `json:"stage_file,omitempty"`
	Saved   bool   `json:"saved"`
	Failure string `json:"reason,omitempty"`
	Source  string `json:"source_path,omitempty"`
}

type Conflict struct {
	ID               string              `json:"id,omitempty"`
	Path             string              `json:"path"`
	Generation       string              `json:"hub_generation"`
	Reason           string              `json:"reason"`
	State            string              `json:"state"`
	Saved            bool                `json:"saved"`
	Base             ConflictCandidate   `json:"baseline"`
	Local            ConflictCandidate   `json:"detected_local"`
	Hub              []ConflictCandidate `json:"hub_candidates"`
	Edits            []ConflictCandidate `json:"later_local_edits,omitempty"`
	Rejected         *protocol.Change    `json:"rejected_change,omitempty"`
	Resolution       *resolution         `json:"resolution,omitempty"`
	PriorResolutions []resolution        `json:"prior_resolutions,omitempty"`
}

func candidateOf(d safefs.Digest, revision int64, absent string) ConflictCandidate {
	if d.Hash != "" {
		return ConflictCandidate{Kind: "file", Revision: revision, Digest: d}
	}
	return ConflictCandidate{Kind: absent, Revision: revision, Saved: true}
}

func baseCandidate(b Base) ConflictCandidate {
	if b.Revision == 0 {
		return candidateOf(b.Digest, 0, "unregistered")
	}
	return candidateOf(b.Digest, b.Revision, "deleted")
}

func hubCandidate(e protocol.Entry) ConflictCandidate {
	return baseCandidate(Base{Revision: e.Revision, Digest: digestOf(e)})
}

func localCandidate(path string, s safefs.Snapshot, b Base) ConflictCandidate {
	if s.Dirs[path] {
		return candidateOf(safefs.Digest{}, 0, "directory")
	}
	kind := "unregistered"
	if b.Revision != 0 {
		kind = "deleted"
	}
	return candidateOf(s.Files[path], 0, kind)
}

func (c *Conflict) latest() *ConflictCandidate { return &c.Hub[len(c.Hub)-1] }

func (c *Conflict) updateSaved() {
	c.Saved = c.Local.Saved
	for _, values := range [][]ConflictCandidate{c.Hub, c.Edits} {
		for _, v := range values {
			c.Saved = c.Saved && v.Saved
		}
	}
}

func (s *stateDB) conflicts(ctx context.Context, activeOnly bool) ([]Conflict, error) {
	query := "SELECT id,path,active,data FROM conflicts"
	if activeOnly {
		query += " WHERE active=1"
	}
	rows, err := s.conn.QueryContext(ctx, query+" ORDER BY path,id")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Conflict
	for rows.Next() {
		var c Conflict
		var active bool
		var id, path string
		var data []byte
		if err := rows.Scan(&id, &path, &active, &data); err != nil {
			return nil, err
		}
		if len(result) >= planLimit || json.Unmarshal(data, &c) != nil || c.ID != id || c.Path != path ||
			!protocol.IsHex(id, 32) || !protocol.IsHex(c.Generation, 32) || safefs.Validate(path) != nil || len(c.Hub) == 0 ||
			active != (c.State != "resolved") {
			return nil, errors.New("invalid persisted conflict")
		}
		result = append(result, c)
	}
	return result, rows.Err()
}

func putConflict(ctx context.Context, tx *sql.Tx, c *Conflict) error {
	data, err := json.Marshal(c)
	if err != nil {
		return err
	}
	return mutateOne(ctx, tx, `
INSERT INTO conflicts SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM conflicts WHERE id=?)
OR (SELECT count(*) FROM conflicts)<?
ON CONFLICT(id) DO UPDATE SET active=excluded.active,data=excluded.data`,
		c.ID, c.Path, c.State != "resolved", data, c.ID, planLimit)
}

func (n *networkSync) writeConflict(c *Conflict) error {
	c.updateSaved()
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		if err := putConflict(n.ctx, tx, c); err != nil {
			return err
		}
		return putHold(n.ctx, tx, c.Path, c.Reason)
	})
}

func (n *networkSync) makeConflict(path, reason string, base Base, local ConflictCandidate, hub protocol.Entry, rejected *Outgoing) (*Conflict, error) {
	id, err := newID()
	if err != nil {
		return nil, err
	}
	c := &Conflict{ID: id, Path: path, Generation: n.reg.Generation, Reason: reason,
		State: "pending", Base: baseCandidate(base), Local: local, Hub: []ConflictCandidate{hubCandidate(hub)}}
	if rejected != nil {
		c.Rejected = &rejected.Change
	}
	if err := n.writeConflict(c); err != nil {
		return nil, err
	}
	return c, nil
}

func (n *networkSync) updateHubCandidate(c *Conflict, e protocol.Entry) error {
	previous, current := c.latest(), hubCandidate(e)
	switch {
	case previous.Revision > current.Revision ||
		(previous.Revision == current.Revision && (previous.Digest != current.Digest || previous.Kind != current.Kind)):
		return fmt.Errorf("hub candidate regressed or disagrees for %q", c.Path)
	case previous.Revision == current.Revision:
		return nil
	case len(c.Hub) >= protocol.MaxReceipts+1:
		return safefs.ErrLimit
	}
	c.Hub = append(c.Hub, current)
	return n.writeConflict(c)
}

// Missing or unreadable candidates keep the conflict pending. Storage and DB
// errors while creating a copy stop the current run.
func (n *networkSync) saveCandidates(c *Conflict, rejected *Outgoing) error {
	if rejected == nil && c.Rejected != nil && !c.Local.Saved {
		items, err := n.db.outbox(n.ctx)
		if err != nil {
			return err
		}
		for _, item := range items {
			if item.Change.Operation == c.Rejected.Operation {
				rejected = &item
				break
			}
		}
	}
	if err := n.saveCandidate(c, &c.Local, n.destination, rejected); err != nil {
		return err
	}
	for i := range c.Edits {
		if err := n.saveCandidate(c, &c.Edits[i], n.destination, nil); err != nil {
			return err
		}
	}
	for i := range c.Hub {
		if err := n.saveCandidate(c, &c.Hub[i], nil, nil); err != nil {
			return err
		}
	}
	return nil
}

func (n *networkSync) saveCandidate(c *Conflict, v *ConflictCandidate, from *safefs.Dir, rejected *Outgoing) error {
	if v.Saved {
		return nil
	}
	if v.Copy == "" {
		plans, err := n.db.plans(n.ctx)
		if err != nil {
			return err
		}
		count, used, err := n.db.usage(n.ctx, plans)
		if err != nil {
			return err
		}
		if err := checkCopyBudget(count+1, used+v.Size); err != nil {
			v.Failure = "copy_budget"
			return n.writeConflict(c)
		}
		id, err := newID()
		if err != nil {
			return err
		}
		v.Copy = id + ".candidate"
		if err := n.db.write(n.ctx, func(tx *sql.Tx) error {
			if _, err := tx.ExecContext(n.ctx, "INSERT INTO saved_copies VALUES (?,?)", v.Copy, v.Size); err != nil {
				return err
			}
			return putConflict(n.ctx, tx, c)
		}); err != nil {
			return err
		}
	}
	e := n.engine
	p := Plan{Path: c.Path}
	if v.Source != "" {
		p.Path = v.Source
	}
	if from == nil {
		e.fetch = func(want safefs.Digest) ([]byte, error) {
			data, err := n.fetch(want)
			if err != nil {
				return nil, &unavailableCandidate{err}
			}
			return data, nil
		}
	} else if rejected != nil && rejected.digest() == v.Digest {
		// A rejected send may be the only copy of the detection-time bytes.
		e.fetch = func(want safefs.Digest) ([]byte, error) {
			return n.readSaved(rejected.name(), want)
		}
		from = nil
	}
	stage, err := e.acquire(p, v.Copy, v.Digest, from)
	if err != nil {
		var unavailable *unavailableCandidate
		switch {
		case errors.As(err, &unavailable):
			v.Failure = "blob_unavailable"
		case errors.Is(err, ErrHash), errors.Is(err, safefs.ErrStructure), missing(err):
			v.Failure = "candidate_changed_or_missing"
		default:
			return err
		}
		return n.writeConflict(c)
	}
	defer stage.close()
	if err := stage.syncClose(); err != nil {
		return err
	}
	if err := n.db.manager.Stage.Sync(); err != nil {
		return err
	}
	v.Saved, v.Failure = true, ""
	return n.writeConflict(c)
}

type unavailableCandidate struct{ error }

func findConflict(conflicts []Conflict, path string) *Conflict {
	i := slices.IndexFunc(conflicts, func(c Conflict) bool { return c.Path == path })
	if i < 0 {
		return nil
	}
	return &conflicts[i]
}

func (n *networkSync) handleApplyHold(failure error) error {
	h, held := failure.(*applyHold)
	if !held {
		return failure
	}
	p := h.plan
	base, err := n.db.lookup(n.ctx, p.Path)
	if err != nil {
		return err
	}
	id, err := newID()
	if err != nil {
		return err
	}
	local := candidateOf(p.Local, 0, "deleted")
	remote := baseCandidate(p.Target)
	if local.Hash != "" {
		local.Copy, local.Saved = p.LocalCopy(), p.Saved
	}
	if remote.Hash != "" {
		remote.Copy, remote.Saved = p.Incoming(), p.Saved
	}
	c := &Conflict{ID: id, Path: p.Path, Generation: n.reg.Generation, Reason: "apply_local_edit", State: "pending",
		Base: baseCandidate(base), Local: local, Hub: []ConflictCandidate{remote}}
	c.updateSaved()
	return n.db.write(n.ctx, func(tx *sql.Tx) error {
		if err := putConflict(n.ctx, tx, c); err != nil {
			return err
		}
		if err := putHold(n.ctx, tx, c.Path, c.Reason); err != nil {
			return err
		}
		return settlePlan(n.ctx, tx, p.ID)
	})
}

func settlePlan(ctx context.Context, tx *sql.Tx, id string) error {
	return mutateOne(ctx, tx, "UPDATE plans SET settled=1 WHERE id=? AND status='conflict' AND settled=0", id)
}

func (n *networkSync) verifyRetained(c *Conflict) error {
	candidates := []*ConflictCandidate{&c.Local}
	for i := range c.Hub {
		candidates = append(candidates, &c.Hub[i])
	}
	for i := range c.Edits {
		candidates = append(candidates, &c.Edits[i])
	}
	for _, v := range candidates {
		if v.Hash == "" {
			continue
		}
		d, _, err := n.db.manager.Stage.ReadPrivate(v.Copy, nil)
		if err == nil && d == v.Digest {
			continue
		}
		v.Saved, v.Failure = false, "retained_copy_unverified"
		if writeErr := n.writeConflict(c); writeErr != nil {
			return writeErr
		}
		return errors.Join(ErrConflict, ErrHash, err)
	}
	return nil
}
