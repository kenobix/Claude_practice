package local

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
)

const networkSchema = `
CREATE TABLE registration (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), space TEXT NOT NULL, client TEXT NOT NULL,
    generation TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('seed','pull')),
    complete INTEGER NOT NULL DEFAULT 0 CHECK(complete IN (0,1))
);
CREATE TABLE registration_items (
    path TEXT COLLATE BINARY PRIMARY KEY NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
    hash TEXT NOT NULL, size INTEGER NOT NULL CHECK(size BETWEEN 0 AND 10485760)
);
CREATE TABLE outbox (
    path TEXT COLLATE BINARY PRIMARY KEY NOT NULL, id TEXT UNIQUE NOT NULL, request BLOB NOT NULL,
    status TEXT NOT NULL DEFAULT 'staging' CHECK(status IN ('staging','ready','rejected','done'))
);
CREATE TABLE holds (path TEXT COLLATE BINARY PRIMARY KEY NOT NULL, reason TEXT NOT NULL);
PRAGMA user_version=2;
`

type Registration struct {
	Space      string `json:"space_id"`
	Client     string `json:"client_id"`
	Generation string `json:"hub_generation"`
	Mode       string `json:"mode"`
	Complete   bool   `json:"complete"`
}

type Outgoing struct {
	Change  protocol.Change `json:"change"`
	Status  string          `json:"status"`
	request []byte
}

func (o Outgoing) name() string { return o.Change.Operation + ".outgoing" }
func (o Outgoing) digest() safefs.Digest {
	return safefs.Digest{Hash: *o.Change.Hash, Size: o.Change.Size}
}

func (s *stateDB) registration(ctx context.Context) (*Registration, error) {
	var r Registration
	err := s.conn.QueryRowContext(ctx, "SELECT space,client,generation,mode,complete FROM registration WHERE singleton=1").
		Scan(&r.Space, &r.Client, &r.Generation, &r.Mode, &r.Complete)
	switch {
	case errors.Is(err, sql.ErrNoRows):
		return nil, nil
	case err != nil:
		return nil, err
	case !protocol.ValidName(r.Space) || !protocol.ValidName(r.Client) || !protocol.IsHex(r.Generation, 32):
		return nil, errors.New("invalid persisted client registration")
	default:
		return &r, nil
	}
}

type registrationItem struct {
	path string
	base Base
}

func (s *stateDB) registrationItems(ctx context.Context) ([]registrationItem, error) {
	rows, err := s.conn.QueryContext(ctx, "SELECT path,revision,hash,size FROM registration_items ORDER BY path")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var items []registrationItem
	for rows.Next() {
		var item registrationItem
		if err := rows.Scan(&item.path, &item.base.Revision, &item.base.Hash, &item.base.Size); err != nil {
			return nil, err
		}
		if len(items) >= safefs.MaxFiles || safefs.Validate(item.path) != nil || !protocol.IsHex(item.base.Hash, 64) {
			return nil, errors.New("invalid persisted registration plan")
		}
		items = append(items, item)
	}
	return items, rows.Err()
}

func (s *stateDB) outbox(ctx context.Context) ([]Outgoing, error) {
	rows, err := s.conn.QueryContext(ctx, "SELECT path,id,request,status FROM outbox ORDER BY path")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var result []Outgoing
	for rows.Next() {
		var item Outgoing
		var path, id string
		if err := rows.Scan(&path, &id, &item.request, &item.Status); err != nil {
			return nil, err
		}
		c, err := protocol.DecodeChange(item.request)
		if err != nil || c.Path != path || c.Operation != id || c.Base < 0 || c.Deleted ||
			!protocol.IsHex(id, 32) || !protocol.IsHex(c.Generation, 32) || !protocol.ValidName(c.Client) ||
			!protocol.MaxCandidate(c.Size, safefs.MaxSize, c.Hash, false) || safefs.Validate(path) != nil ||
			len(result) >= safefs.MaxManagedCopies {
			return nil, errors.New("invalid persisted outgoing operation")
		}
		item.Change = c
		result = append(result, item)
	}
	return result, rows.Err()
}

func outgoing(r *Registration, path string, base int64, d safefs.Digest) (Outgoing, error) {
	id, err := newID()
	if err != nil {
		return Outgoing{}, err
	}
	c := protocol.Change{Generation: r.Generation, Client: r.Client, Operation: id, Path: path, Base: base, Hash: &d.Hash, Size: d.Size}
	payload, err := json.Marshal(c)
	if err != nil {
		return Outgoing{}, err
	}
	if len(payload) > protocol.MaxJSON {
		return Outgoing{}, errors.New("outgoing request exceeds protocol limit")
	}
	return Outgoing{Change: c, Status: "staging", request: payload}, nil
}

func insertOutgoing(ctx context.Context, tx *sql.Tx, item Outgoing) error {
	_, err := tx.ExecContext(ctx, "INSERT INTO outbox (path,id,request) VALUES (?,?,?)", item.Change.Path, item.Change.Operation, item.request)
	return err
}

func (s *stateDB) setOutgoing(ctx context.Context, item Outgoing, status string) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		return mutateOne(ctx, tx, "UPDATE outbox SET status=? WHERE id=? AND status=?", status, item.Change.Operation, item.Status)
	})
}

func (s *stateDB) acknowledge(ctx context.Context, item Outgoing, receipt protocol.Receipt) error {
	target := Base{Revision: receipt.Entry.Revision, Digest: item.digest()}
	return s.write(ctx, func(tx *sql.Tx) error {
		if err := putBase(ctx, tx, item.Change.Path, item.Change.Base, target); err != nil {
			return err
		}
		return mutateOne(ctx, tx, "UPDATE outbox SET status='done' WHERE id=? AND status='ready'", item.Change.Operation)
	})
}

func (s *stateDB) holds(ctx context.Context) (map[string]string, error) {
	rows, err := s.conn.QueryContext(ctx, "SELECT path,reason FROM holds ORDER BY path")
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	result := make(map[string]string)
	for rows.Next() {
		var path, reason string
		if err := rows.Scan(&path, &reason); err != nil {
			return nil, err
		}
		if safefs.Validate(path) != nil || len(result) >= planLimit {
			return nil, errors.New("invalid persisted hold")
		}
		result[path] = reason
	}
	return result, rows.Err()
}

func (s *stateDB) setHold(ctx context.Context, path, reason string) error {
	return s.write(ctx, func(tx *sql.Tx) error {
		return putHold(ctx, tx, path, reason)
	})
}

func putHold(ctx context.Context, tx *sql.Tx, path, reason string) error {
	return mutateOne(ctx, tx, `
INSERT INTO holds (path,reason) SELECT ?,? WHERE EXISTS(SELECT 1 FROM holds WHERE path=?)
OR (SELECT count(*) FROM holds)<?
ON CONFLICT(path) DO UPDATE SET reason=excluded.reason`, path, reason, path, planLimit)
}
