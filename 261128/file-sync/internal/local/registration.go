package local

import (
	"database/sql"
	"errors"
	"fmt"
	"maps"
	"slices"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func (n *networkSync) beginRegistration(mode string, m protocol.Manifest, s safefs.Snapshot) error {
	if mode == "seed" && len(m.Entries) != 0 {
		return errors.New("seed requires a hub with no manifest rows")
	}
	if mode == "pull" && len(s.Files) != 0 {
		return errors.New("pull requires a root with no user files")
	}
	plans, err := n.db.plans(n.ctx)
	if err != nil {
		return err
	}
	bases, err := n.db.bases(n.ctx)
	if err != nil {
		return err
	}
	if len(plans)+len(bases) != 0 {
		return errors.New("manual copy state cannot become a hub baseline")
	}
	if _, _, err := n.db.usage(n.ctx, plans); err != nil {
		return err
	}
	r := &Registration{Space: m.Space, Client: m.Client, Generation: m.Generation, Mode: mode}
	var items []registrationItem
	var out []Outgoing
	if mode == "seed" {
		var size int64
		for _, e := range s.Entries() {
			item, err := outgoing(r, e.Path, 0, e.Digest)
			if err != nil {
				return err
			}
			out = append(out, item)
			items = append(items, registrationItem{path: e.Path, base: Base{Revision: 1, Digest: e.Digest}})
			size += e.Size
		}
		if err := checkCopyBudget(len(out), size); err != nil {
			return err
		}
	} else {
		if len(structuralPaths(s, m.Entries)) != 0 {
			return fmt.Errorf("%w: pull plan conflicts with a local directory", ErrConflict)
		}
		var paths []string
		for _, e := range m.Entries {
			if !e.Deleted {
				paths = append(paths, e.Path)
			}
			items = append(items, registrationItem{path: e.Path, base: Base{Revision: e.Revision, Digest: digestOf(e)}})
		}
		if err := checkIncomingSpace(s, paths); err != nil {
			return err
		}
	}
	if err := n.db.write(n.ctx, func(tx *sql.Tx) error {
		if _, err := tx.ExecContext(n.ctx, "INSERT INTO registration VALUES (1,?,?,?,?,0)", r.Space, r.Client, r.Generation, r.Mode); err != nil {
			return err
		}
		for _, item := range items {
			if _, err := tx.ExecContext(n.ctx, "INSERT INTO registration_items VALUES (?,?,?,?)",
				item.path, item.base.Revision, item.base.Hash, item.base.Size); err != nil {
				return err
			}
		}
		for _, item := range out {
			if err := insertOutgoing(n.ctx, tx, item); err != nil {
				return err
			}
		}
		return nil
	}); err != nil {
		return err
	}
	n.reg = r
	return nil
}

func (n *networkSync) finishRegistration() error {
	items, err := n.db.registrationItems(n.ctx)
	if err != nil {
		return err
	}
	if n.reg.Mode == "seed" {
		if err := n.resumeSeed(items); err != nil {
			return err
		}
	} else if err := n.resumePull(items); err != nil {
		return err
	}
	s, err := n.scan()
	if err != nil {
		return err
	}
	bases, err := n.db.bases(n.ctx)
	if err != nil {
		return err
	}
	expected := make(map[string]safefs.Digest, len(items))
	for _, item := range items {
		if bases[item.path] != item.base {
			return errors.New("registration baseline is incomplete")
		}
		expected[item.path] = item.base.Digest
	}
	edits := make(map[string]bool)
	for path, d := range s.Files {
		if d != expected[path] {
			edits[path] = true
		}
	}
	for path, d := range expected {
		if s.Files[path] != d {
			edits[path] = true
		}
	}
	if err := n.db.write(n.ctx, func(tx *sql.Tx) error {
		for _, path := range slices.Sorted(maps.Keys(edits)) {
			if _, err := tx.ExecContext(n.ctx, "INSERT INTO holds VALUES (?,'registration_local_edit')", path); err != nil {
				return err
			}
		}
		return mutateOne(n.ctx, tx, "UPDATE registration SET complete=1 WHERE singleton=1 AND complete=0")
	}); err != nil {
		return err
	}
	n.reg.Complete = true
	return n.registrationHolds()
}

func (n *networkSync) registrationHolds() error {
	holds, err := n.db.holds(n.ctx)
	if err != nil {
		return err
	}
	for _, reason := range holds {
		if reason == "registration_local_edit" {
			return fmt.Errorf("%w: local changes outside the initial registration plan; see status", ErrConflict)
		}
	}
	return nil
}

func (n *networkSync) resumeSeed(items []registrationItem) error {
	out, err := n.db.outbox(n.ctx)
	if err != nil {
		return err
	}
	expected := make(map[string]Base, len(items))
	for _, item := range items {
		expected[item.path] = item.base
	}
	// Secure every planned body before the first POST. On restart a complete
	// stage is independent of the current user file.
	for _, item := range out {
		if err := n.checkOutgoing(item); err != nil {
			return err
		}
		if item.Change.Base != 0 || expected[item.Change.Path] != (Base{Revision: 1, Digest: item.digest()}) {
			return errors.New("outgoing seed operation differs from its registration plan")
		}
		switch item.Status {
		case "staging":
			if err := n.stageOutgoing(item); err != nil {
				return fmt.Errorf("%w: seed body %q: %w", ErrConflict, item.Change.Path, err)
			}
		case "rejected":
			return fmt.Errorf("%w: seed no longer matches the hub", ErrConflict)
		}
	}
	for _, item := range out {
		if item.Status == "done" {
			err = n.cleanupOutgoing(item)
		} else {
			err = n.send(item)
		}
		if err != nil {
			if errors.Is(err, errRejudge) {
				return errors.Join(ErrConflict, err)
			}
			return err
		}
	}
	return nil
}

func (n *networkSync) resumePull(items []registrationItem) error {
	s, err := n.scan()
	if err != nil {
		return err
	}
	bases, err := n.db.bases(n.ctx)
	if err != nil {
		return err
	}
	var paths []string
	var entries []protocol.Entry
	for _, item := range items {
		entries = append(entries, protocol.Entry{Path: item.path, Deleted: item.base.Hash == ""})
		base := bases[item.path]
		if base == item.base {
			continue
		}
		if base.Revision != 0 || s.Files[item.path].Hash != "" {
			return fmt.Errorf("%w: unplanned local state at %q during pull", ErrConflict, item.path)
		}
		if item.base.Hash != "" {
			paths = append(paths, item.path)
		}
	}
	if len(structuralPaths(s, entries)) != 0 {
		return fmt.Errorf("%w: pull plan conflicts with the local namespace", ErrConflict)
	}
	if err := checkIncomingSpace(s, paths); err != nil {
		return err
	}
	for _, item := range items {
		if bases[item.path] == item.base {
			continue
		}
		var err error
		if item.base.Hash == "" {
			err = n.adopt(item.path, Base{}, item.base)
		} else {
			err = n.install(item.path, Base{}, item.base)
		}
		if err != nil {
			return err
		}
	}
	return nil
}
