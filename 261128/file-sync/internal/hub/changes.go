package hub

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"math"
	"net/http"
	"strings"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

const entryColumns = "path,revision,hash,size,deleted,client,op"

type rowScanner interface {
	Scan(...any) error
}

func entry(row rowScanner) (protocol.Entry, error) {
	var e protocol.Entry
	err := row.Scan(&e.Path, &e.Revision, &e.Hash, &e.Size, &e.Deleted, &e.Client, &e.Operation)
	return e, err
}

func (h *Hub) manifest(w http.ResponseWriter, r *http.Request, p principal) {
	h.mu.Lock()
	m, err := h.readManifest(context.WithoutCancel(r.Context()), p)
	var failure *protocol.Problem
	if err != nil {
		failure = h.failure(err)
	}
	h.mu.Unlock()
	if failure != nil {
		writeProblem(w, failure)
		return
	}
	w.Header().Set(protocol.ClientHeader, p.client)
	writeJSON(w, 200, m)
}

func (h *Hub) readManifest(ctx context.Context, p principal) (protocol.Manifest, error) {
	m := protocol.Manifest{Space: p.space, Entries: make([]protocol.Entry, 0)}
	if h.stopped {
		return m, errors.New("hub requires reopen")
	}
	tx, err := h.store.Conn.BeginTx(ctx, &sql.TxOptions{ReadOnly: true})
	if err != nil {
		return m, err
	}
	defer tx.Rollback()
	if err := tx.QueryRowContext(ctx, "SELECT generation FROM spaces WHERE id=?", p.space).Scan(&m.Generation); err != nil {
		return m, err
	}
	rows, err := tx.QueryContext(ctx, "SELECT "+entryColumns+" FROM entries WHERE space=? ORDER BY path COLLATE BINARY", p.space)
	if err != nil {
		return m, err
	}
	defer rows.Close()
	for rows.Next() {
		e, err := entry(rows)
		if err != nil {
			return m, err
		}
		m.Entries = append(m.Entries, e)
	}
	if err := rows.Err(); err != nil {
		return m, err
	}
	return m, tx.Commit()
}

func (h *Hub) accept(ctx context.Context, p principal, c protocol.Change) ([]byte, *protocol.Problem) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.stopped {
		return nil, problem(503, "storage_unavailable")
	}
	body, err := json.Marshal(c)
	if err != nil {
		return nil, h.failure(err)
	}
	var recorded, response []byte
	err = h.store.Conn.QueryRowContext(ctx, `
SELECT request,response FROM receipts WHERE space=? AND generation=? AND client=? AND op=?`,
		p.space, p.generation, p.client, c.Operation).Scan(&recorded, &response)
	switch {
	case err == nil:
		if !bytes.Equal(body, recorded) {
			return nil, problem(409, "op_id_reused")
		}
		return response, nil
	case !errors.Is(err, sql.ErrNoRows):
		return nil, h.failure(err)
	}
	current, err := entry(h.store.Conn.QueryRowContext(ctx, "SELECT "+entryColumns+" FROM entries WHERE space=? AND path=?", p.space, c.Path))
	if err != nil && !errors.Is(err, sql.ErrNoRows) {
		return nil, h.failure(err)
	}
	if c.Base != current.Revision {
		pb := &protocol.Problem{Status: 409, Code: "revision_mismatch", Generation: p.generation}
		if current.Revision != 0 {
			pb.Current = &current
		}
		return nil, pb
	}
	if c.Deleted && current.Revision == 0 {
		return nil, problem(422, "unknown_delete")
	}
	same := c.Deleted && current.Deleted
	if !c.Deleted {
		conflicts, err := h.namespace(ctx, p.space, c.Path)
		if err != nil {
			return nil, h.failure(err)
		}
		if len(conflicts) > 0 {
			return nil, &protocol.Problem{Status: 409, Code: "namespace_conflict", Conflicts: conflicts}
		}
		var size int64
		err = h.store.Conn.QueryRowContext(ctx, "SELECT size FROM blobs WHERE space=? AND hash=?", p.space, *c.Hash).Scan(&size)
		if errors.Is(err, sql.ErrNoRows) {
			return nil, problem(422, "blob_missing")
		}
		if err != nil {
			return nil, h.failure(err)
		}
		if c.Size != size {
			return nil, problem(422, "blob_size_mismatch")
		}
		same = current.Hash != nil && *current.Hash == *c.Hash && current.Size == c.Size && !current.Deleted
	}
	if !same && current.Revision == math.MaxInt64 {
		return nil, problem(507, "storage_limit")
	}
	var all, live, receipts int
	for _, count := range []struct {
		query  string
		result *int
	}{
		{"SELECT count(*) FROM entries", &all},
		{"SELECT count(*) FROM entries WHERE deleted=0", &live},
		{"SELECT count(*) FROM receipts", &receipts},
	} {
		if err := h.store.Conn.QueryRowContext(ctx, count.query).Scan(count.result); err != nil {
			return nil, h.failure(err)
		}
	}
	if receipts >= protocol.MaxReceipts || (current.Revision == 0 && all >= protocol.MaxPaths) ||
		(!c.Deleted && (current.Revision == 0 || current.Deleted) && live >= safefs.MaxFiles) {
		return nil, problem(507, "storage_limit")
	}
	if !c.Deleted {
		if _, err := h.verifyBlob(blobName(p.space, *c.Hash), safefs.Digest{Hash: *c.Hash, Size: c.Size}); err != nil {
			return nil, h.failure(err)
		}
	}
	r := protocol.Receipt{Generation: p.generation, Operation: c.Operation, Result: "unchanged", Entry: current}
	if !same {
		r.Result = "applied"
		r.Entry = protocol.Entry{Path: c.Path, Revision: current.Revision + 1, Hash: c.Hash, Size: c.Size, Deleted: c.Deleted, Client: p.client, Operation: c.Operation}
	}
	response, err = json.Marshal(r)
	if err != nil {
		return nil, h.failure(err)
	}
	err = h.store.Write(ctx, func(tx *sql.Tx) error {
		var revision int64
		err := tx.QueryRowContext(ctx, `
SELECT COALESCE((SELECT revision FROM entries WHERE space=? AND path=?),0)`, p.space, c.Path).Scan(&revision)
		if err != nil {
			return err
		}
		if revision != c.Base {
			return errors.New("entry changed outside hub serialization")
		}
		if !same {
			result, err := tx.ExecContext(ctx, `
INSERT INTO entries VALUES (?,?,?,?,?,?,?,?)
ON CONFLICT(space,path) DO UPDATE SET revision=excluded.revision,hash=excluded.hash,size=excluded.size,
deleted=excluded.deleted,client=excluded.client,op=excluded.op WHERE entries.revision=?`,
				p.space, c.Path, r.Entry.Revision, c.Hash, c.Size, c.Deleted, p.client, c.Operation, c.Base)
			if err != nil {
				return err
			}
			if n, err := result.RowsAffected(); err != nil || n != 1 {
				return errors.Join(err, errors.New("entry changed outside hub serialization"))
			}
		}
		_, err = tx.ExecContext(ctx, "INSERT INTO receipts VALUES (?,?,?,?,?,?)", p.space, p.generation, p.client, c.Operation, body, response)
		if err == nil && h.beforeChangeCommit != nil {
			h.beforeChangeCommit()
		}
		if err != nil {
			return err
		}
		return h.at("before-change-commit")
	})
	if err != nil {
		return nil, h.failure(err)
	}
	if err := h.at("change-committed"); err != nil {
		return nil, h.unavailable()
	}
	if h.afterChangeCommit != nil {
		h.afterChangeCommit()
	}
	return response, nil
}

func (h *Hub) namespace(ctx context.Context, space, path string) ([]string, error) {
	rows, err := h.store.Conn.QueryContext(ctx, "SELECT path FROM entries WHERE space=? AND deleted=0 ORDER BY path COLLATE BINARY", space)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var conflicts []string
	for rows.Next() {
		var existing string
		if err := rows.Scan(&existing); err != nil {
			return nil, err
		}
		if strings.HasPrefix(path, existing+"/") || strings.HasPrefix(existing, path+"/") {
			conflicts = append(conflicts, existing)
		}
	}
	return conflicts, rows.Err()
}
