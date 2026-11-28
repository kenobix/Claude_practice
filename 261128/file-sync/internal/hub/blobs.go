package hub

import (
	"bytes"
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"io"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"golang.org/x/sys/unix"
)

func blobName(space, hash string) string { return space + "." + hash }

func (h *Hub) recoverBlobs(ctx context.Context) error {
	// Verify registered blobs before touching any interrupted upload.
	rows, err := h.store.Conn.QueryContext(ctx, "SELECT space,hash,size FROM blobs")
	if err != nil {
		return err
	}
	registered := make(map[string]bool)
	for rows.Next() {
		var space, hash string
		var size int64
		if err := rows.Scan(&space, &hash, &size); err != nil {
			rows.Close()
			return err
		}
		name := blobName(space, hash)
		if !protocol.ValidName(space) || !protocol.IsHex(hash, 64) || len(registered) >= safefs.MaxManagedCopies {
			rows.Close()
			return errors.New("invalid persisted blob table")
		}
		if _, err := h.verifyBlob(name, safefs.Digest{Hash: hash, Size: size}); err != nil {
			rows.Close()
			return err
		}
		registered[name] = true
	}
	if err := errors.Join(rows.Err(), rows.Close()); err != nil {
		return err
	}
	var invalid, paths, live, receipts int
	for _, q := range []struct {
		sql string
		out *int
	}{
		{"SELECT count(*) FROM entries JOIN blobs USING(space,hash) WHERE entries.size<>blobs.size", &invalid},
		{"SELECT count(*) FROM entries", &paths},
		{"SELECT count(*) FROM entries WHERE deleted=0", &live},
		{"SELECT count(*) FROM receipts", &receipts},
	} {
		if err := h.store.Conn.QueryRowContext(ctx, q.sql).Scan(q.out); err != nil {
			return err
		}
	}
	if invalid != 0 || paths > protocol.MaxPaths || live > safefs.MaxFiles || receipts > protocol.MaxReceipts {
		return errors.New("invalid persisted entry or receipt counts")
	}
	for _, p := range h.principals {
		m, err := h.readManifest(ctx, p)
		if err != nil {
			return err
		}
		if !protocol.ValidManifestEntries(m.Entries) {
			return errors.New("invalid persisted manifest")
		}
	}
	var partials, orphans []string
	spaces := make(map[string]bool)
	for _, p := range h.principals {
		spaces[p.space] = true
	}
	if err := h.blobs.Names(func(name string) error {
		final, partial := strings.CutSuffix(name, ".part")
		space, hash, ok := strings.Cut(final, ".")
		if !ok || !spaces[space] || !protocol.IsHex(hash, 64) {
			return errors.New("unexpected name in hub blob directory")
		}
		_, size, err := h.blobs.Info(name)
		if err != nil {
			return err
		}
		h.blobCount++
		h.blobBytes += size
		if size > safefs.MaxSize || h.blobCount > safefs.MaxManagedCopies || h.blobBytes > safefs.MaxManagedBytes {
			return safefs.ErrLimit
		}
		if partial {
			if registered[final] {
				return errors.New("interrupted upload overlaps registered blob")
			}
			partials = append(partials, name)
		} else if !registered[name] {
			if _, err := h.verifyBlob(name, safefs.Digest{Hash: hash, Size: size}); err != nil {
				return err
			}
			orphans = append(orphans, name)
		}
		return nil
	}); err != nil {
		return err
	}
	for _, name := range partials {
		id, size, err := h.blobs.Info(name)
		if err != nil {
			return err
		}
		if err := h.removePartial(name, id); err != nil {
			return err
		}
		h.blobCount--
		h.blobBytes -= size
	}
	for _, name := range orphans {
		space, hash, _ := strings.Cut(name, ".")
		id, size, err := h.blobs.Info(name)
		if err != nil {
			return err
		}
		if err := h.syncFinal(name, id); err != nil {
			return err
		}
		if err := h.registerBlob(ctx, space, hash, size); err != nil {
			if h.failure(err).Code != "storage_full" {
				return err
			}
			// Keep the verified file charged. A later PUT can retry registration.
		}
	}
	return nil
}

func (h *Hub) removePartial(name string, id safefs.Identity) error {
	if err := h.blobs.Remove(name, id); err != nil {
		return err
	}
	return h.blobs.Sync()
}

func (h *Hub) syncFinal(name string, id safefs.Identity) error {
	if err := h.blobs.SyncPrivate(name, id); err != nil {
		return err
	}
	return h.blobs.Sync()
}

func (h *Hub) registerBlob(ctx context.Context, space, hash string, size int64) error {
	return h.store.Write(ctx, func(tx *sql.Tx) error {
		_, err := tx.ExecContext(ctx, "INSERT INTO blobs VALUES (?,?,?)", space, hash, size)
		return err
	})
}

func (h *Hub) getBlob(ctx context.Context, p principal, hash string) ([]byte, *protocol.Problem) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped {
		return nil, problem(503, "storage_unavailable")
	}
	var size int64
	err := h.store.Conn.QueryRowContext(ctx, "SELECT size FROM blobs WHERE space=? AND hash=?", p.space, hash).Scan(&size)
	if errors.Is(err, sql.ErrNoRows) {
		return nil, problem(404, "blob_not_found")
	}
	if err != nil {
		return nil, h.failure(err)
	}
	var body bytes.Buffer
	got, _, err := h.blobs.ReadPrivate(blobName(p.space, hash), &body)
	if err != nil || got != (safefs.Digest{Hash: hash, Size: size}) {
		return nil, h.failure(errors.Join(err, errors.New("blob integrity check failed")))
	}
	return body.Bytes(), nil
}

type upload struct {
	final      string
	id         safefs.Identity
	reuse      bool
	registered bool
	size       int64
}

func (h *Hub) reserveUpload(ctx context.Context, p principal, hash string, size int64) (upload, *protocol.Problem) {
	h.mu.Lock()
	defer h.mu.Unlock()
	u := upload{final: blobName(p.space, hash), size: size}
	if h.stopped {
		return u, problem(503, "storage_unavailable")
	}
	var stored int64
	err := h.store.Conn.QueryRowContext(ctx, "SELECT size FROM blobs WHERE space=? AND hash=?", p.space, hash).Scan(&stored)
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return u, h.failure(err)
	}
	u.registered = err == nil
	id, onDisk, err := h.blobs.Info(u.final)
	switch {
	case err == nil:
		u.reuse, u.id, u.size = true, id, onDisk
		if u.registered && stored != onDisk {
			return u, h.failure(errors.New("registered blob size changed"))
		}
		if _, err := h.verifyBlob(u.final, safefs.Digest{Hash: hash, Size: onDisk}); err != nil {
			return u, h.failure(err)
		}
	case !errors.Is(err, unix.ENOENT) || u.registered:
		return u, h.failure(err)
	default:
		if h.blobCount >= safefs.MaxManagedCopies || h.blobBytes+size > safefs.MaxManagedBytes {
			return u, problem(507, "storage_limit")
		}
		h.blobCount++
		h.blobBytes += size
	}
	return u, nil
}

type uploadWriter struct {
	io.Writer
	err error
}

func (w *uploadWriter) Write(b []byte) (int, error) {
	n, err := w.Writer.Write(b)
	w.err = err
	return n, err
}

func (h *Hub) putBlob(ctx context.Context, p principal, hash string, size int64, body io.Reader) (int, *protocol.Problem) {
	h.put.Lock()
	defer h.put.Unlock()
	u, failure := h.reserveUpload(ctx, p, hash, size)
	if failure != nil {
		return 0, failure
	}
	if body == nil {
		body = strings.NewReader("")
	}
	if u.reuse {
		if failure := receive(body, io.Discard, size, hash); failure != nil {
			return 0, failure
		}
		h.mu.Lock()
		defer h.mu.Unlock()
		if h.stopped {
			return 0, problem(503, "storage_unavailable")
		}
		if u.size != size {
			return 0, problem(422, "length_mismatch")
		}
		if _, err := h.verifyBlob(u.final, safefs.Digest{Hash: hash, Size: size}); err != nil {
			return 0, h.failure(err)
		}
		if u.registered {
			return 200, nil
		}
		if err := h.syncFinal(u.final, u.id); err != nil {
			return 0, h.unavailable()
		}
		if err := h.registerBlob(ctx, p.space, hash, size); err != nil {
			return 0, h.failure(err)
		}
		return 201, nil
	}
	name := u.final + ".part"
	f, id, err := h.blobs.Create(name)
	if err != nil {
		h.mu.Lock()
		defer h.mu.Unlock()
		h.blobCount--
		h.blobBytes -= size
		return 0, h.failure(err)
	}
	writer := &uploadWriter{Writer: f}
	failure = receive(body, writer, size, hash)
	if failure != nil {
		closeErr := f.Close()
		h.mu.Lock()
		defer h.mu.Unlock()
		if closeErr != nil || (writer.err != nil && !errors.Is(writer.err, unix.ENOSPC)) {
			return 0, h.unavailable()
		}
		if err := h.removePartial(name, id); err != nil {
			return 0, h.unavailable()
		}
		h.blobCount--
		h.blobBytes -= size
		if writer.err != nil {
			return 0, h.failure(writer.err)
		}
		return 0, failure
	}
	stageID, verifyErr := h.verifyBlob(name, safefs.Digest{Hash: hash, Size: size})
	if verifyErr != nil || stageID != id {
		_ = f.Close()
		h.mu.Lock()
		defer h.mu.Unlock()
		return 0, h.unavailable()
	}
	err = errors.Join(f.Sync(), f.Close())
	h.mu.Lock()
	defer h.mu.Unlock()
	if err != nil {
		return 0, h.unavailable()
	}
	if h.stopped {
		return 0, problem(503, "storage_unavailable")
	}
	if err := h.blobs.Install(name, id, h.blobs, u.final, false); err != nil {
		return 0, h.unavailable()
	}
	if err := h.blobs.Sync(); err != nil {
		return 0, h.unavailable()
	}
	if err := h.registerBlob(ctx, p.space, hash, size); err != nil {
		return 0, h.failure(err)
	}
	return 201, nil
}

func receive(body io.Reader, dest io.Writer, size int64, want string) *protocol.Problem {
	hash := sha256.New()
	// Read at most the declared length into storage; any extra byte is
	// inspected separately and never consumes the reserved file budget.
	n, err := io.Copy(io.MultiWriter(dest, hash), io.LimitReader(body, size))
	if err != nil || n != size {
		return problem(422, "length_mismatch")
	}
	var extra [1]byte
	if n, err := io.ReadFull(body, extra[:]); n != 0 || err != io.EOF {
		return problem(422, "length_mismatch")
	}
	if hex.EncodeToString(hash.Sum(nil)) != want {
		return problem(422, "hash_mismatch")
	}
	return nil
}
