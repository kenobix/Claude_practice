package local

import (
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
