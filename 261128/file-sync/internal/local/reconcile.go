package local

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"maps"
	"slices"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type ReconcilePreview struct {
	Approval  string               `json:"approval"`
	Manifest  protocol.Manifest    `json:"manifest"`
	Client    string               `json:"client_id"`
	Local     []safefs.Entry       `json:"local"`
	Dirs      []string             `json:"directories"`
	Blocked   []string             `json:"structural_conflicts,omitempty"`
	Paths     []ReconcilePath      `json:"comparison"`
	State     []safefs.ManagedFile `json:"state_files,omitempty"`
	Stage     []safefs.Entry       `json:"retained_stage_files,omitempty"`
	Prior     *Status              `json:"readable_old_state,omitempty"`
	OldStatus string               `json:"old_state_status"`
}

type ReconcilePath struct {
	Path  string            `json:"path"`
	Local ConflictCandidate `json:"local"`
	Hub   ConflictCandidate `json:"hub"`
	Match bool              `json:"matches"`
}

func Reconcile(ctx context.Context, req NetworkRequest, approval string) (report ReconcilePreview, err error) {
	if req.Remote == nil {
		return report, errors.New("missing hub connection")
	}
	if approval != "" && !protocol.IsHex(approval, 64) {
		return report, errors.New("invalid approval digest")
	}
	v, err := safefs.OpenVolume(req.Volume)
	if err != nil {
		return report, err
	}
	defer v.Close()
	root, err := v.LockRoot(req.Root)
	if err != nil {
		return report, err
	}
	defer root.Close()
	m, err := root.OpenReconcileManagement()
	if err != nil && !missing(err) {
		return report, err
	}
	if err != nil {
		// A missing member of an existing management area is different from
		// a missing area. Scan will refuse unsafe or incomplete layouts here.
		if _, err := root.Scan(); err != nil {
			return report, err
		}
	} else {
		defer m.Close()
	}
	s, err := root.ScanManaged(m)
	if err != nil {
		return report, err
	}
	h, err := req.Remote.Manifest(ctx)
	if err != nil {
		return report, err
	}
	report = reconcilePreview(h, s)
	if m != nil {
		if m.State != nil {
			db, openErr := openState(ctx, m, false)
			if openErr == nil {
				prior, inspectErr := db.inspect(ctx)
				if inspectErr != nil {
					return report, errors.Join(inspectErr, db.close())
				}
				if prior.Registration != nil && (prior.Registration.Space != h.Space || prior.Registration.Client != h.Client) {
					return report, errors.Join(errors.New("authenticated space or client differs from old registration"), db.close())
				}
				if prior.Registration != nil && prior.Registration.Mode == "reconcile" && !prior.Registration.Complete && approval != "" {
					n := networkSync{engine: engine{ctx: ctx, destination: root, db: db}, remote: req.Remote, reg: prior.Registration}
					n.bindRegistration()
					done, err := n.resumeReconcile(report, approval)
					if done || err != nil {
						report.Approval = approval
						return report, errors.Join(err, db.close())
					}
				}
				report.Prior, report.OldStatus = &prior, "readable"
				if err := db.close(); err != nil {
					return report, err
				}
			} else {
				report.OldStatus = "unverified"
			}
		}
		report.State, err = m.StateInventory()
		if err != nil {
			return report, err
		}
		slices.SortFunc(report.State, func(a, b safefs.ManagedFile) int {
			if a.Name < b.Name {
				return -1
			}
			if a.Name > b.Name {
				return 1
			}
			return 0
		})
		if err := m.Stage.Names(func(name string) error {
			d, _, err := m.Stage.ReadPrivate(name, nil)
			if err == nil {
				report.Stage = append(report.Stage, safefs.Entry{Path: name, Digest: d})
			}
			return err
		}); err != nil {
			return report, err
		}
		slices.SortFunc(report.Stage, func(a, b safefs.Entry) int {
			if a.Path < b.Path {
				return -1
			}
			if a.Path > b.Path {
				return 1
			}
			return 0
		})
	}
	report.Approval, err = report.fingerprint()
	if err != nil || approval == "" {
		return report, err
	}
	if approval != report.Approval {
		return report, fmt.Errorf("%w: reconciliation observations changed; review this plan", ErrConflict)
	}
	if len(report.Blocked) != 0 {
		return report, fmt.Errorf("%w: organize the listed structural conflicts and preview again", ErrConflict)
	}
	// Verify the manifest and local state again while holding the same root lock.
	h, err = req.Remote.Manifest(ctx)
	if err != nil {
		return report, err
	}
	s, err = root.ScanManaged(m)
	if err != nil {
		return report, err
	}
	if !report.sameObservation(reconcilePreview(h, s)) {
		return report, fmt.Errorf("%w: reconciliation observations changed; preview again", ErrConflict)
	}
	id, err := newID()
	if err != nil {
		return report, err
	}
	if m == nil {
		m, err = root.InitManagement(id)
		if err != nil {
			return report, err
		}
		defer m.Close()
	}
	prepared, closePrepared, err := m.PrepareState(id)
	if err != nil {
		return report, err
	}
	defer closePrepared()
	db, err := openState(ctx, prepared, true)
	if err != nil {
		return report, err
	}
	r := &Registration{Space: h.Space, Client: h.Client, Generation: h.Generation, Mode: "reconcile"}
	if err := prepareReconcile(ctx, db, r, report); err != nil {
		return report, errors.Join(err, db.close())
	}
	if err := db.checkpoint(ctx); err != nil {
		return report, errors.Join(err, db.close())
	}
	if err := db.close(); err != nil {
		return report, err
	}
	if err := m.ActivateState(id, prepared); err != nil {
		return report, err
	}
	db, err = openState(ctx, prepared, false)
	if err != nil {
		return report, err
	}
	defer func() { err = errors.Join(err, db.close()) }()
	n := networkSync{engine: engine{ctx: ctx, destination: root, db: db}, remote: req.Remote, reg: r}
	n.bindRegistration()
	err = n.finishReconcile()
	return report, err
}

func reconcilePreview(m protocol.Manifest, s safefs.Snapshot) ReconcilePreview {
	report := ReconcilePreview{Manifest: m, Client: m.Client, Local: s.Entries(), Dirs: slices.Sorted(maps.Keys(s.Dirs)),
		Blocked: slices.Sorted(maps.Keys(structuralPaths(s, m.Entries))), OldStatus: "not_found"}
	h := hubPaths(m)
	paths := make(map[string]bool)
	for p := range h {
		paths[p] = true
	}
	for p := range s.Files {
		paths[p] = true
	}
	for _, p := range slices.Sorted(maps.Keys(paths)) {
		b := h[p]
		l := localCandidate(p, s, Base{})
		report.Paths = append(report.Paths, ReconcilePath{Path: p, Local: l, Hub: hubCandidate(b), Match: l.Digest == digestOf(b)})
	}
	return report
}

func (r ReconcilePreview) fingerprint() (string, error) {
	r.Approval = ""
	data, err := json.Marshal(r)
	return protocol.Digest(data).Hash, err
}

func (r ReconcilePreview) sameObservation(other ReconcilePreview) bool {
	a, aErr := json.Marshal(r.Manifest)
	b, bErr := json.Marshal(other.Manifest)
	return aErr == nil && bErr == nil && string(a) == string(b) &&
		r.Client == other.Client && slices.Equal(r.Local, other.Local) && slices.Equal(r.Dirs, other.Dirs)
}

func prepareReconcile(ctx context.Context, db *stateDB, r *Registration, report ReconcilePreview) error {
	// Keep the approved observation and human-readable inventory. The old
	// database itself is preserved byte-for-byte at the directory swap.
	report.Prior = nil
	data, err := json.Marshal(report)
	if err != nil {
		return err
	}
	return db.write(ctx, func(tx *sql.Tx) error {
		if _, err := tx.ExecContext(ctx, "INSERT INTO reconcile_plan VALUES (1,?)", data); err != nil {
			return err
		}
		if _, err := tx.ExecContext(ctx, "INSERT INTO registration VALUES (1,?,?,?,'reconcile',0)", r.Space, r.Client, r.Generation); err != nil {
			return err
		}
		for _, f := range report.Stage {
			if _, err := tx.ExecContext(ctx, "INSERT INTO saved_copies VALUES (?,?)", f.Path, f.Size); err != nil {
				return err
			}
		}
		count := 0
		for _, p := range report.Paths {
			if p.Match {
				if p.Hub.Revision != 0 {
					if err := putBase(ctx, tx, p.Path, 0, Base{Revision: p.Hub.Revision, Digest: p.Hub.Digest}); err != nil {
						return err
					}
				}
				continue
			}
			count++
			if count > planLimit {
				return safefs.ErrLimit
			}
			id, err := newID()
			if err != nil {
				return err
			}
			c := &Conflict{ID: id, Path: p.Path, Generation: r.Generation, Reason: "reconcile_difference", State: "pending",
				Base: baseCandidate(Base{}), Local: p.Local, Hub: []ConflictCandidate{p.Hub}}
			if err := putConflict(ctx, tx, c); err != nil {
				return err
			}
			if err := putHold(ctx, tx, p.Path, c.Reason); err != nil {
				return err
			}
		}
		return nil
	})
}

func (n *networkSync) resumeReconcile(now ReconcilePreview, approval string) (bool, error) {
	var data []byte
	if err := n.db.conn.QueryRowContext(n.ctx, "SELECT data FROM reconcile_plan WHERE singleton=1").Scan(&data); err != nil {
		return false, err
	}
	var prior ReconcilePreview
	if err := json.Unmarshal(data, &prior); err != nil {
		return false, err
	}
	if prior.Approval != approval || !prior.sameObservation(now) {
		return false, nil
	}
	return true, n.finishReconcile()
}

func (n *networkSync) finishReconcile() error {
	list, err := n.db.conflicts(n.ctx, true)
	if err != nil {
		return err
	}
	for i := range list {
		if err := n.saveCandidates(&list[i], nil); err != nil {
			return err
		}
	}
	if err := n.db.write(n.ctx, func(tx *sql.Tx) error {
		return mutateOne(n.ctx, tx, "UPDATE registration SET complete=1 WHERE singleton=1 AND complete=0")
	}); err != nil {
		return err
	}
	if len(list) != 0 {
		return fmt.Errorf("%w: reconciled with %d conflict(s); see status", ErrConflict, len(list))
	}
	return nil
}
