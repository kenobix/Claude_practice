package local

import (
	"database/sql"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

type clientSizes struct {
	Database, WAL, SHM, Other, Archived, Stage int64
	Archives                                   int
}

func measureClient(root string) (clientSizes, error) {
	var s clientSizes
	management := filepath.Join(root, safefs.Management)
	err := filepath.WalkDir(management, func(p string, d os.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() {
			if filepath.Dir(p) == management && safefs.ReconcileDirName(d.Name()) {
				s.Archives++
			}
			return nil
		}
		st, err := d.Info()
		if err != nil {
			return err
		}
		switch parent := filepath.Base(filepath.Dir(p)); {
		case parent == "stage":
			s.Stage += st.Size()
		case safefs.ReconcileDirName(parent):
			s.Archived += st.Size()
		case d.Name() == safefs.StateDB:
			s.Database += st.Size()
		case d.Name() == safefs.StateDB+"-wal":
			s.WAL += st.Size()
		case d.Name() == safefs.StateDB+"-shm":
			s.SHM += st.Size()
		default:
			s.Other += st.Size()
		}
		return nil
	})
	if err == nil && (s.Database+s.WAL+s.SHM+s.Other+s.Archived > 128<<20 || s.Archived > 60<<20 || s.Archives > 16 || s.Stage > safefs.MaxManagedBytes) {
		err = fmt.Errorf("client management envelope exceeded %+v", s)
	}
	return s, err
}

func TestP4_11_ReconcileDirectoryAndByteBudgets(t *testing.T) {
	t.Run("prepare-sixteenth-and-refuse-seventeenth", func(t *testing.T) {
		n := newNetworkLab(t)
		for i := 0; i < 15; i++ {
			must(t, os.Mkdir(filepath.Join(n.b, safefs.Management, fmt.Sprintf("reconcile-%032x", i)), 0700))
		}
		req := n.req(t, "B")
		req.notify = func(e event) error {
			s, err := measureClient(n.b)
			t.Logf("client reconcile %s: %+v", e.point, s)
			if err == nil && s.Archives != 16 {
				return fmt.Errorf("expected 16 state directories, got %d", s.Archives)
			}
			return err
		}
		p, err := Reconcile(t.Context(), req, "")
		must(t, err)
		_, err = Reconcile(t.Context(), req, p.Approval)
		must(t, err)
		p, err = Reconcile(t.Context(), req, "")
		must(t, err)
		if _, err := Reconcile(t.Context(), req, p.Approval); !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("expected 17th archive refusal: %v", err)
		}
		s, err := measureClient(n.b)
		must(t, err)
		if s.Archives != 16 {
			t.Fatal("state directory count changed")
		}
		body, d := fixture(t, "P")
		assertFile(t, n.b, notes, body, d)
		assertBase(t, n.status(t), notes, 1, d)
	})
	t.Run("archive-exact-and-plus-one", func(t *testing.T) {
		n := newNetworkLab(t)
		d := filepath.Join(n.b, safefs.Management, fmt.Sprintf("reconcile-%032x", 1))
		must(t, os.Mkdir(d, 0700))
		name := filepath.Join(d, safefs.StateDB)
		must(t, os.WriteFile(name, nil, 0600))
		must(t, os.Truncate(name, 60<<20))
		s, err := measureClient(n.b)
		must(t, err)
		if s.Archived != 60<<20 {
			t.Fatal("archive logical length wrong")
		}
		req := n.req(t, "B")
		p, err := Reconcile(t.Context(), req, "")
		must(t, err)
		if _, err := Reconcile(t.Context(), req, p.Approval); !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("no room for new preparation: %v", err)
		}
		body, data := fixture(t, "P")
		assertFile(t, n.b, notes, body, data)
		assertBase(t, n.status(t), notes, 1, data)
		must(t, os.Truncate(name, (60<<20)+1))
		if _, err := Inspect(t.Context(), n.volume, "B"); !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("accepted oversized archived state: %v", err)
		}
		st, err := os.Stat(name)
		must(t, err)
		if st.Size() != (60<<20)+1 {
			t.Fatal("oversized archive removed or changed")
		}
		t.Logf("archived logical length exact then one extra byte: %d, %d", s.Archived, st.Size())
	})
	t.Run("all-metadata-charged", func(t *testing.T) {
		n := newNetworkLab(t)
		root := filepath.Join(n.b, safefs.Management)
		d := filepath.Join(root, fmt.Sprintf("reconcile-%032x", 1))
		must(t, os.Mkdir(d, 0700))
		name := filepath.Join(d, safefs.StateDB)
		must(t, os.WriteFile(name, nil, 0600))
		must(t, os.Truncate(name, 60<<20))
		lock := filepath.Join(root, safefs.LockFile)
		must(t, os.Truncate(lock, 68<<20))
		if _, err := Inspect(t.Context(), n.volume, "B"); !errors.Is(err, safefs.ErrLimit) {
			t.Fatalf("aggregate metadata budget did not stop open: %v", err)
		}
		body, digest := fixture(t, "P")
		assertFile(t, n.b, notes, body, digest)
		st, err := os.Stat(lock)
		must(t, err)
		if st.Size() != 68<<20 {
			t.Fatal("oversized metadata overwritten")
		}
	})
}

func TestP4_11_ClientSQLitePlansAtFixedLimit(t *testing.T) {
	n := newNetworkLab(t)
	_, p := fixture(t, "P")
	var sizes [3]clientSizes
	source := "hub-" + n.status(t).Registration.Generation
	must(t, withDeleteEngine(t.Context(), n.lab, func(e *engine) error {
		ctx := e.ctx
		if err := e.db.write(ctx, func(tx *sql.Tx) error {
			stmt, err := tx.Prepare("INSERT INTO plans(id,path,source,base_revision,before_hash,before_size,target_revision,target_hash,target_size,status) VALUES(?,?,?,0,'',0,1,?,3,'complete')")
			if err != nil {
				return err
			}
			defer stmt.Close()
			for i := 1; i < planLimit; i++ {
				path := strings.Join([]string{strings.Repeat("a", 255), strings.Repeat("b", 255), strings.Repeat("c", 255), strings.Repeat("d", 250), fmt.Sprintf("%05d", i)}, "/")
				if len(path) != 1024 || safefs.Validate(path) != nil {
					return fmt.Errorf("invalid fixture path length %d", len(path))
				}
				if _, err := stmt.Exec(fmt.Sprintf("%032x", i), path, source, p.Hash); err != nil {
					return err
				}
			}
			sizes[0], err = measureClient(n.b)
			return err
		}); err != nil {
			return err
		}
		var err error
		sizes[1], err = measureClient(n.b)
		if err != nil {
			return err
		}
		return e.db.checkpoint(ctx)
	}))
	var err error
	sizes[2], err = measureClient(n.b)
	must(t, err)
	s := n.status(t)
	if len(s.Plans) != 4096 {
		t.Fatalf("wrong plan count %d", len(s.Plans))
	}
	assertBase(t, s, notes, 1, p)
	m := n.publish(t, "Q")
	if err := Sync(t.Context(), n.req(t, "B")); !errors.Is(err, ErrConflict) {
		t.Fatalf("plan limit: %v", err)
	}
	s = n.status(t)
	if len(s.Plans) != 4096 || s.Holds[notes] != "apply_pending" {
		t.Fatalf("client changed full plan table rows=%d holds=%v", len(s.Plans), s.Holds)
	}
	assertBase(t, s, notes, 1, p)
	n.assertHubUnchanged(t, m)
	t.Logf("client plans=4096 sqlite sizes before/bulk-commit/reopen: %+v", sizes)
}

func TestP4_11_CombinedArchiveAndWALBudget(t *testing.T) {
	n := newNetworkLab(t)
	name := filepath.Join(n.b, safefs.Management, fmt.Sprintf("reconcile-%032x", 1))
	must(t, os.Mkdir(name, 0700))
	old := filepath.Join(name, safefs.StateDB)
	must(t, os.WriteFile(old, nil, 0600))
	must(t, os.Truncate(old, 60<<20))
	ctx := t.Context()
	// Use the SQLite table that holds the approved manifest. Whitespace
	// gives deterministic JSON sizes without treating it as a real approval.
	data := []byte("\n" + strings.Repeat(" ", 30<<20) + "{}")
	var before, after clientSizes
	must(t, withDeleteEngine(ctx, n.lab, func(e *engine) error {
		write := func(tx *sql.Tx) error {
			if _, err := tx.Exec("DELETE FROM reconcile_plan"); err != nil {
				return err
			}
			if _, err := tx.Exec("INSERT INTO reconcile_plan VALUES(1,?)", data); err != nil {
				return err
			}
			var err error
			before, err = measureClient(n.b)
			return err
		}
		if err := e.db.write(ctx, write); err != nil {
			return err
		}
		data[0] = ' '
		if err := e.db.write(ctx, write); err != nil {
			return err
		}
		var err error
		after, err = measureClient(n.b)
		return err
	}))
	if before.Archived != 60<<20 || after.Archived != before.Archived || after.Database < 30<<20 || after.WAL < 30<<20 || before.WAL != 0 {
		t.Fatalf("fixture did not combine near-full DB and live WAL: before=%+v after=%+v", before, after)
	}
	p, d := fixture(t, "P")
	assertFile(t, n.b, notes, p, d)
	assertBase(t, n.status(t), notes, 1, d)
	final, err := measureClient(n.b)
	must(t, err)
	t.Logf("combined archive+SQLite: before=%+v after=%+v reopened=%+v", before, after, final)
}
