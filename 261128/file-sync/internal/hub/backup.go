package hub

import (
	"bytes"
	"context"
	"crypto/rand"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"slices"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/dbstore"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

const (
	backupManifest      = "backup.json"
	backupManifestLimit = 2 << 20
)

type backupBlob struct {
	Space string `json:"space"`
	safefs.Digest
}

type backupSpace struct {
	ID         string `json:"space"`
	Generation string `json:"hub_generation"`
}

type BackupInfo struct {
	Format   int           `json:"format"`
	Database safefs.Digest `json:"database"`
	Spaces   []backupSpace `json:"spaces"`
	Blobs    []backupBlob  `json:"blobs"`
}

type RestoreInfo struct {
	Space      string `json:"space"`
	Generation string `json:"hub_generation"`
}

func Backup(ctx context.Context, volume, source, destination string) (BackupInfo, error) {
	return backup(ctx, volume, source, destination, nil)
}

func backup(ctx context.Context, volume, source, destination string, notify func(string)) (info BackupInfo, err error) {
	h, err := Open(ctx, volume, source)
	if err != nil {
		return info, err
	}
	defer func() { err = errors.Join(err, h.Close()) }()
	if err := h.store.Checkpoint(ctx); err != nil {
		return info, err
	}
	info, err = snapshotInventory(ctx, h.store.Conn)
	if err != nil {
		return info, err
	}
	err = h.store.Close()
	h.store = nil
	if err != nil {
		return info, err
	}
	err = prepareOffline(h.volume, source, destination, notify, func(dir *safefs.Dir) error {
		blobs, err := dir.PrivateDir("blobs", true)
		if err != nil {
			return err
		}
		defer blobs.Close()
		info.Database, err = copyPrivate(h.manager.State, safefs.StateDB, dir, safefs.StateDB, dbstore.MaxDatabaseBytes, nil)
		if err != nil {
			return err
		}
		for _, b := range info.Blobs {
			name := blobName(b.Space, b.Hash)
			if _, err := copyPrivate(h.blobs, name, blobs, name, safefs.MaxSize, &b.Digest); err != nil {
				return err
			}
		}
		if err := blobs.Sync(); err != nil {
			return err
		}
		data, err := json.Marshal(info)
		if err != nil {
			return err
		}
		if len(data) > backupManifestLimit {
			return safefs.ErrLimit
		}
		f, _, err := dir.Create(backupManifest)
		if err != nil {
			return err
		}
		_, err = f.Write(data)
		if err := errors.Join(err, f.Sync(), f.Close()); err != nil {
			return err
		}
		_, err = verifyBackup(ctx, dir, blobs)
		return err
	})
	return info, err
}

func Restore(ctx context.Context, volume, source, destination string, provision Provision) (RestoreInfo, error) {
	return restore(ctx, volume, source, destination, provision, nil)
}

func restore(ctx context.Context, volume, source, destination string, provision Provision, notify func(string)) (RestoreInfo, error) {
	var info RestoreInfo
	digests, err := provisionDigests(provision)
	if err != nil {
		return info, err
	}
	v, err := safefs.OpenVolume(volume)
	if err != nil {
		return info, err
	}
	defer v.Close()
	src, err := v.LockRoot(source)
	if err != nil {
		return info, err
	}
	defer src.Close()
	blobs, err := src.PrivateDir("blobs", false)
	if err != nil {
		return info, err
	}
	defer blobs.Close()
	record, err := verifyBackup(ctx, src, blobs)
	if err != nil {
		return info, err
	}
	if len(record.Spaces) != 1 || record.Spaces[0].ID != provision.Space {
		return info, errors.New("backup does not match current single-space configuration")
	}
	generation, err := protocol.NewID()
	if err != nil {
		return info, err
	}
	if generation == record.Spaces[0].Generation {
		return info, errors.New("generated hub generation already used")
	}
	err = prepareOffline(v, source, destination, notify, func(dir *safefs.Dir) (err error) {
		rootID, err := protocol.NewID()
		if err != nil {
			return err
		}
		// Keep an unfinished restore unservable even if the process is killed.
		marker, markerID, err := dir.Create("restore-incomplete")
		if err != nil {
			return err
		}
		if err := errors.Join(marker.Sync(), marker.Close(), dir.Sync()); err != nil {
			return err
		}
		manager, err := dir.InitManagement(rootID)
		if err != nil {
			return err
		}
		defer manager.Close()
		objects, err := dir.PrivateDir("blobs", true)
		if err != nil {
			return err
		}
		defer objects.Close()
		_, err = copyPrivate(src, safefs.StateDB, manager.State, "restore.sqlite", dbstore.MaxDatabaseBytes, &record.Database)
		if err != nil {
			return err
		}
		copied, _, err := manager.State.Info("restore.sqlite")
		if err != nil {
			return err
		}
		if err := manager.State.Install("restore.sqlite", copied, manager.State, safefs.StateDB, true); err != nil {
			return err
		}
		if err := manager.State.Sync(); err != nil {
			return err
		}
		for _, b := range record.Blobs {
			name := blobName(b.Space, b.Hash)
			if _, err := copyPrivate(blobs, name, objects, name, safefs.MaxSize, &b.Digest); err != nil {
				return err
			}
		}
		if err := objects.Sync(); err != nil {
			return err
		}
		if notify != nil {
			notify("copied-before-generation")
		}
		store, err := dbstore.Open(ctx, manager, false, func(store *dbstore.Store, _ string) error {
			return store.Write(ctx, func(tx *sql.Tx) error {
				return restoreIdentity(ctx, tx, provision, digests, rootID, generation, record.Spaces[0].Generation)
			})
		})
		if err != nil {
			return err
		}
		check := &Hub{blobs: objects, store: store}
		err = check.loadPrincipals(ctx)
		if err == nil {
			err = check.recoverBlobs(ctx)
		}
		if err == nil {
			err = store.Checkpoint(ctx)
		}
		if err := errors.Join(err, store.Close(), manager.State.Sync()); err != nil {
			return err
		}
		if notify != nil {
			notify("generation-committed")
		}
		return dir.Remove("restore-incomplete", markerID)
	})
	return RestoreInfo{Space: provision.Space, Generation: generation}, err
}

func restoreIdentity(ctx context.Context, tx *sql.Tx, provision Provision, digests [2][32]byte, rootID, generation, old string) error {
	var total int
	if err := tx.QueryRowContext(ctx, "SELECT count(*) FROM clients WHERE space=?", provision.Space).Scan(&total); err != nil {
		return err
	}
	if total != len(provision.Clients) {
		return errors.New("current client IDs must match the backup")
	}
	// Two passes also allow the current configuration to exchange the two tokens.
	for _, client := range provision.Clients {
		var temporary [32]byte
		if _, err := rand.Read(temporary[:]); err != nil {
			return err
		}
		result, err := tx.ExecContext(ctx, "UPDATE clients SET token_digest=? WHERE space=? AND id=?", temporary[:], provision.Space, client.ID)
		if err != nil {
			return err
		}
		if n, err := result.RowsAffected(); err != nil || n != 1 {
			return errors.Join(err, errors.New("current client IDs must match the backup"))
		}
	}
	for i, client := range provision.Clients {
		if _, err := tx.ExecContext(ctx, "UPDATE clients SET token_digest=? WHERE space=? AND id=?", digests[i][:], provision.Space, client.ID); err != nil {
			return err
		}
	}
	if _, err := tx.ExecContext(ctx, "UPDATE identity SET root_id=? WHERE singleton=1", rootID); err != nil {
		return err
	}
	var used int
	if err := tx.QueryRowContext(ctx, "SELECT count(*) FROM receipts WHERE generation=?", generation).Scan(&used); err != nil {
		return err
	}
	if used != 0 {
		return errors.New("generated hub generation already used")
	}
	result, err := tx.ExecContext(ctx, "UPDATE spaces SET generation=? WHERE id=? AND generation=?", generation, provision.Space, old)
	if err != nil {
		return err
	}
	if n, err := result.RowsAffected(); err != nil || n != 1 {
		return errors.Join(err, errors.New("backup generation changed"))
	}
	return nil
}

func prepareOffline(v *safefs.Volume, source, dest string, notify func(string), populate func(*safefs.Dir) error) error {
	if source == dest || strings.HasPrefix(dest, source+"/") || strings.HasPrefix(source, dest+"/") {
		return errors.New("source and destination must be separate roots")
	}
	parent, leaf, err := v.Destination(dest)
	if err != nil {
		return err
	}
	defer parent.Close()
	id, err := protocol.NewID()
	if err != nil {
		return err
	}
	name := ".file-sync-offline-" + id
	dir, err := parent.PrivateDir(name, true)
	if err != nil {
		return err
	}
	defer dir.Close()
	if err := dir.Lock(); err != nil {
		return err
	}
	if err := parent.Sync(); err != nil {
		return err
	}
	if notify != nil {
		notify("preparing")
	}
	if err := populate(dir); err != nil {
		return fmt.Errorf("unpublished work retained at %s: %w", name, err)
	}
	if notify != nil {
		notify("before-publish")
	}
	if err := parent.PublishDir(name, dir, leaf); err != nil {
		return fmt.Errorf("publication requires inspection of %s and %s: %w", name, dest, err)
	}
	if notify != nil {
		notify("published")
	}
	return nil
}

func copyPrivate(src *safefs.Dir, from string, dst *safefs.Dir, to string, limit int64, want *safefs.Digest) (safefs.Digest, error) {
	out, _, err := dst.Create(to)
	if err != nil {
		return safefs.Digest{}, err
	}
	got, _, err := src.ReadPrivateLimit(from, out, limit)
	if err != nil || (want != nil && got != *want) {
		return got, errors.Join(err, out.Close(), fmt.Errorf("source file %q failed verification", from))
	}
	if err := errors.Join(out.Sync(), out.Close()); err != nil {
		return got, err
	}
	check, _, err := dst.ReadPrivateLimit(to, nil, limit)
	if err != nil || check != got {
		return got, errors.Join(err, fmt.Errorf("copy of %q failed verification", from))
	}
	return got, nil
}

func snapshotInventory(ctx context.Context, conn *sql.Conn) (info BackupInfo, err error) {
	info.Format = 1
	info.Spaces = make([]backupSpace, 0)
	info.Blobs = make([]backupBlob, 0)
	var version int
	if err := conn.QueryRowContext(ctx, "PRAGMA user_version").Scan(&version); err != nil || version != 1 {
		return info, errors.Join(err, errors.New("unsupported hub database"))
	}
	rows, err := conn.QueryContext(ctx, "SELECT id,generation FROM spaces ORDER BY id")
	if err != nil {
		return info, err
	}
	for rows.Next() {
		var s backupSpace
		if err := rows.Scan(&s.ID, &s.Generation); err != nil {
			return info, errors.Join(err, rows.Close())
		}
		if !protocol.ValidName(s.ID) || !protocol.IsHex(s.Generation, 32) || len(info.Spaces) >= 1 {
			return info, errors.Join(errors.New("invalid or unsupported space inventory"), rows.Close())
		}
		info.Spaces = append(info.Spaces, s)
	}
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return info, err
	}
	if len(info.Spaces) != 1 {
		return info, errors.New("backup requires a single provisioned space")
	}
	rows, err = conn.QueryContext(ctx, "SELECT space,hash,size FROM blobs ORDER BY space,hash")
	if err != nil {
		return info, err
	}
	defer rows.Close()
	var total int64
	for rows.Next() {
		var b backupBlob
		if err := rows.Scan(&b.Space, &b.Hash, &b.Size); err != nil {
			return info, err
		}
		total += b.Size
		if b.Space != info.Spaces[0].ID || !protocol.IsHex(b.Hash, 64) || b.Size < 0 || b.Size > safefs.MaxSize ||
			len(info.Blobs) >= safefs.MaxManagedCopies || total > safefs.MaxManagedBytes {
			return info, errors.New("invalid blob inventory")
		}
		info.Blobs = append(info.Blobs, b)
	}
	return info, rows.Err()
}

func verifyBackup(ctx context.Context, dir, objects *safefs.Dir) (BackupInfo, error) {
	var record BackupInfo
	if err := dir.Names(func(name string) error {
		switch name {
		case backupManifest, safefs.StateDB, "blobs":
			return nil
		}
		return fmt.Errorf("unexpected backup entry %q", name)
	}); err != nil {
		return record, err
	}
	var data bytes.Buffer
	if _, _, err := dir.ReadPrivateLimit(backupManifest, &data, backupManifestLimit); err != nil {
		return record, err
	}
	if err := json.Unmarshal(data.Bytes(), &record); err != nil {
		return record, err
	}
	digest, _, err := dir.ReadPrivateLimit(safefs.StateDB, nil, dbstore.MaxDatabaseBytes)
	if err != nil || digest != record.Database {
		return record, errors.Join(err, errors.New("backup database digest mismatch"))
	}
	err = dbstore.InspectSnapshot(ctx, dir, func(conn *sql.Conn) error {
		actual, err := snapshotInventory(ctx, conn)
		if err != nil {
			return err
		}
		if actual.Format != record.Format || !slices.Equal(actual.Spaces, record.Spaces) || !slices.Equal(actual.Blobs, record.Blobs) {
			return errors.New("backup manifest disagrees with database inventory")
		}
		return nil
	})
	if err != nil {
		return record, err
	}
	expected := make(map[string]safefs.Digest, len(record.Blobs))
	for _, b := range record.Blobs {
		expected[blobName(b.Space, b.Hash)] = b.Digest
	}
	err = objects.Names(func(name string) error {
		want, ok := expected[name]
		if !ok {
			return fmt.Errorf("unexpected backup blob %q", name)
		}
		got, _, err := objects.ReadPrivate(name, nil)
		if err != nil || got != want {
			return errors.Join(err, fmt.Errorf("backup blob %q failed verification", name))
		}
		delete(expected, name)
		return nil
	})
	if err == nil && len(expected) != 0 {
		err = errors.New("backup is missing registered blobs")
	}
	return record, err
}
