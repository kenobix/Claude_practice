package local

import (
	"bytes"
	"database/sql"
	"os"
	"path/filepath"
	"testing"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/dbstore"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

func TestClientMigratesV1WithoutReplacingManualHistory(t *testing.T) {
	volume := t.TempDir()
	must(t, os.Mkdir(filepath.Join(volume, "A"), 0700))
	must(t, os.Mkdir(filepath.Join(volume, "B"), 0700))
	p, pDigest := fixture(t, "P")
	q, _ := fixture(t, "Q")
	must(t, os.WriteFile(filepath.Join(volume, "A", notes), q, 0600))
	must(t, os.WriteFile(filepath.Join(volume, "B", notes), p, 0600))
	v, err := safefs.OpenVolume(volume)
	must(t, err)
	root, err := v.LockRoot("B")
	must(t, err)
	id, err := newID()
	must(t, err)
	m, err := root.InitManagement(id)
	must(t, err)
	s, err := dbstore.Open(t.Context(), m, true, func(db *dbstore.Store, marker string) error {
		return db.Write(t.Context(), func(tx *sql.Tx) error {
			if _, err := tx.Exec(schema); err != nil {
				return err
			}
			if _, err := tx.Exec("INSERT INTO identity VALUES (1,?)", marker); err != nil {
				return err
			}
			_, err := tx.Exec("INSERT INTO baseline VALUES (?,1,?,?)", notes, pDigest.Hash, pDigest.Size)
			return err
		})
	})
	must(t, err)
	must(t, s.Close())
	m.Close()
	root.Close()
	v.Close()
	status, err := Inspect(t.Context(), volume, "B")
	must(t, err)
	if status.Registration != nil || status.Bases[notes] != (Base{Revision: 1, Digest: pDigest}) {
		t.Fatalf("v1 state changed during migration: %+v", status)
	}
	must(t, Copy(t.Context(), Request{Volume: volume, Source: "A", Destination: "B", Revision: 2}))
	status, err = Inspect(t.Context(), volume, "B")
	must(t, err)
	if status.Bases[notes].Revision != 2 || len(status.Plans) != 1 || status.Plans[0].Status != "complete" {
		t.Fatalf("manual copy broken by migration: %+v", status)
	}
}

func TestP3MigratesV2WithUnfinishedApplyAndOutbox(t *testing.T) {
	volume := t.TempDir()
	must(t, os.Mkdir(filepath.Join(volume, "B"), 0700))
	v, err := safefs.OpenVolume(volume)
	must(t, err)
	defer v.Close()
	root, err := v.LockRoot("B")
	must(t, err)
	defer root.Close()
	id, err := newID()
	must(t, err)
	m, err := root.InitManagement(id)
	must(t, err)
	defer m.Close()
	p, pd := fixture(t, "P")
	q, qd := fixture(t, "Q")
	r := &Registration{Space: "test", Client: "client-b", Generation: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}
	out, err := outgoing(r, "other.txt", 0, qd)
	must(t, err)
	planID, err := newID()
	must(t, err)
	persist := func(name string, data []byte) {
		t.Helper()
		f, _, err := m.Stage.Create(name)
		must(t, err)
		_, err = f.Write(data)
		must(t, err)
		must(t, f.Sync())
		must(t, f.Close())
	}
	persist(out.name(), q)
	persist(planID+".incoming", q[:2])
	must(t, m.Stage.Sync())
	must(t, os.WriteFile(filepath.Join(volume, "B", notes), p, 0600))
	old, err := dbstore.Open(t.Context(), m, true, func(store *dbstore.Store, marker string) error {
		return store.Write(t.Context(), func(tx *sql.Tx) error {
			if _, err := tx.Exec(schema + networkSchema); err != nil {
				return err
			}
			for _, sql := range []struct {
				query string
				args  []any
			}{
				{"INSERT INTO identity VALUES (1,?)", []any{marker}},
				{"INSERT INTO registration VALUES (1,?,?,?,'pull',1)", []any{r.Space, r.Client, r.Generation}},
				{"INSERT INTO baseline VALUES (?,1,?,?)", []any{notes, pd.Hash, pd.Size}},
				{"INSERT INTO plans (id,path,source,base_revision,before_hash,before_size,target_revision,target_hash,target_size) VALUES (?,?,?,1,?,?,2,?,?)",
					[]any{planID, notes, "hub-" + r.Generation, pd.Hash, pd.Size, qd.Hash, qd.Size}},
				{"INSERT INTO outbox VALUES (?,?,?,'ready')", []any{out.Change.Path, out.Change.Operation, out.request}},
			} {
				if _, err := tx.Exec(sql.query, sql.args...); err != nil {
					return err
				}
			}
			return nil
		})
	})
	must(t, err)
	must(t, old.Close())
	db, err := openState(t.Context(), m, false)
	must(t, err)
	defer db.close()
	status, err := db.inspect(t.Context())
	must(t, err)
	if len(status.Plans) != 1 || status.Plans[0].ID != planID || status.Plans[0].Status != "pending" ||
		status.Plans[0].Target != (Base{Revision: 2, Digest: qd}) || len(status.Outbox) != 1 ||
		status.Outbox[0].Status != "ready" || status.Outbox[0].Change.Operation != out.Change.Operation ||
		!bytes.Equal(status.Outbox[0].request, out.request) || status.Bases[notes] != (Base{Revision: 1, Digest: pd}) {
		t.Fatalf("migration lost old work: %+v", status)
	}
	count, used, err := db.usage(t.Context(), status.Plans)
	must(t, err)
	if count != 3 || used != 2*qd.Size+pd.Size {
		t.Fatalf("old reservations %d/%d", count, used)
	}
	assertFile(t, filepath.Join(volume, "B", safefs.Management, "stage"), out.name(), q, qd)
	assertFile(t, filepath.Join(volume, "B", safefs.Management, "stage"), planID+".incoming", q[:2], independentDigest(q[:2]))
}
