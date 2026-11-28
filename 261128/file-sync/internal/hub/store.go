// Package hub stores verified blobs and serializes metadata changes for the v1 API.
package hub

import (
	"context"
	"crypto/subtle"
	"database/sql"
	"errors"
	"fmt"
	"sync"

	"github.com/kenobix/Claude_practice/261128/file-sync/internal/dbstore"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/protocol"
	"github.com/kenobix/Claude_practice/261128/file-sync/internal/safefs"
	"github.com/mattn/go-sqlite3"
	"golang.org/x/sys/unix"
)

const schema = `
CREATE TABLE identity (singleton INTEGER PRIMARY KEY CHECK (singleton=1), root_id TEXT NOT NULL);
CREATE TABLE spaces (id TEXT PRIMARY KEY NOT NULL, generation TEXT NOT NULL);
CREATE TABLE clients (
    space TEXT NOT NULL REFERENCES spaces(id), id TEXT NOT NULL,
    token_digest BLOB NOT NULL UNIQUE CHECK(length(token_digest)=32), PRIMARY KEY(space,id)
);
CREATE TABLE blobs (
    space TEXT NOT NULL REFERENCES spaces(id), hash TEXT NOT NULL,
    size INTEGER NOT NULL CHECK(size BETWEEN 0 AND 10485760), PRIMARY KEY(space,hash)
);
CREATE TABLE entries (
    space TEXT NOT NULL, path TEXT COLLATE BINARY NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
    hash TEXT, size INTEGER NOT NULL CHECK(size BETWEEN 0 AND 10485760),
    deleted INTEGER NOT NULL CHECK((deleted=0 AND hash IS NOT NULL) OR (deleted=1 AND hash IS NULL AND size=0)),
    client TEXT NOT NULL, op TEXT NOT NULL,
    PRIMARY KEY(space,path), FOREIGN KEY(space,hash) REFERENCES blobs(space,hash),
    FOREIGN KEY(space,client) REFERENCES clients(space,id)
);
CREATE TABLE receipts (
    space TEXT NOT NULL, generation TEXT NOT NULL, client TEXT NOT NULL, op TEXT NOT NULL,
    request BLOB NOT NULL, response BLOB NOT NULL,
    PRIMARY KEY(space,generation,client,op), FOREIGN KEY(space,client) REFERENCES clients(space,id)
);
PRAGMA user_version=1;
`

type Client struct {
	ID    string
	Token string
}

type Provision struct {
	Space   string
	Clients [2]Client
}

type principal struct {
	space, generation, client string
	digest                    []byte
}

type Hub struct {
	volume     *safefs.Volume
	root       *safefs.Dir
	manager    *safefs.Managed
	blobs      *safefs.Dir
	store      *dbstore.Store
	principals []principal

	// put serializes reservations and body writes. mu protects SQLite and the
	// storage health flag, and is released while reading the PUT body.
	put       sync.Mutex
	mu        sync.Mutex
	stopped   bool
	blobCount int
	blobBytes int64

	beforeChangeCommit func()
	afterChangeCommit  func()
}

func Init(ctx context.Context, volume, path string, provision Provision) error {
	if !protocol.ValidName(provision.Space) {
		return errors.New("invalid space ID")
	}
	var digests [2][32]byte
	for i, client := range provision.Clients {
		digest, valid := protocol.TokenDigest(client.Token)
		if !valid || !protocol.ValidName(client.ID) {
			return errors.New("invalid client ID or token configuration")
		}
		digests[i] = digest
	}
	if digests[0] == digests[1] || provision.Clients[0].ID == provision.Clients[1].ID {
		return errors.New("client IDs and tokens must be distinct")
	}
	setup := func(store *dbstore.Store, rootID string) error {
		generation, err := protocol.NewID()
		if err != nil {
			return err
		}
		return store.Write(ctx, func(tx *sql.Tx) error {
			if _, err := tx.ExecContext(ctx, schema); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, "INSERT INTO identity VALUES (1,?)", rootID); err != nil {
				return err
			}
			if _, err := tx.ExecContext(ctx, "INSERT INTO spaces VALUES (?,?)", provision.Space, generation); err != nil {
				return err
			}
			for i, client := range provision.Clients {
				if _, err := tx.ExecContext(ctx, "INSERT INTO clients VALUES (?,?,?)", provision.Space, client.ID, digests[i][:]); err != nil {
					return err
				}
			}
			return nil
		})
	}
	h, err := open(ctx, volume, path, setup)
	if err != nil {
		return err
	}
	return errors.Join(h.manager.State.Sync(), h.Close())
}

func Open(ctx context.Context, volume, path string) (*Hub, error) {
	return open(ctx, volume, path, nil)
}

func open(ctx context.Context, volume, path string, setup func(*dbstore.Store, string) error) (_ *Hub, err error) {
	h := &Hub{}
	defer func() {
		if err != nil {
			err = errors.Join(err, h.Close())
		}
	}()
	h.volume, err = safefs.OpenVolume(volume)
	if err != nil {
		return nil, err
	}
	h.root, err = h.volume.LockRoot(path)
	if err != nil {
		return nil, err
	}
	initial := setup != nil
	if err := h.root.Names(func(name string) error {
		if initial || (name != "blobs" && name != safefs.Management) {
			return errors.New("unexpected entry in dedicated hub root")
		}
		return nil
	}); err != nil {
		return nil, err
	}
	if initial {
		id, err := protocol.NewID()
		if err != nil {
			return nil, err
		}
		h.manager, err = h.root.InitManagement(id)
		if err != nil {
			return nil, err
		}
	} else {
		h.manager, err = h.root.OpenManagement()
		if err != nil {
			return nil, err
		}
		setup = func(store *dbstore.Store, rootID string) error {
			if err := store.Expect(ctx, "PRAGMA user_version", "1"); err != nil {
				return err
			}
			return store.Expect(ctx, "SELECT root_id FROM identity WHERE singleton=1", rootID)
		}
	}
	if err := h.manager.Stage.Names(func(string) error { return errors.New("unexpected client staging file in hub state") }); err != nil {
		return nil, err
	}
	h.blobs, err = h.root.PrivateDir("blobs", initial)
	if err != nil {
		return nil, err
	}
	if initial {
		if err := errors.Join(h.blobs.Sync(), h.root.Sync()); err != nil {
			return nil, err
		}
	}
	h.store, err = dbstore.Open(ctx, h.manager, initial, setup)
	if err != nil {
		return nil, err
	}
	if err := h.loadPrincipals(ctx); err != nil {
		return nil, err
	}
	if err := h.recoverBlobs(ctx); err != nil {
		return nil, err
	}
	return h, nil
}

// Close is called after all HTTP handlers have terminated.
func (h *Hub) Close() (err error) {
	if h.store != nil {
		err = h.store.Close()
	}
	if h.blobs != nil {
		h.blobs.Close()
	}
	if h.manager != nil {
		h.manager.Close()
	}
	if h.root != nil {
		h.root.Close()
	}
	if h.volume != nil {
		h.volume.Close()
	}
	return err
}

func (h *Hub) loadPrincipals(ctx context.Context) error {
	rows, err := h.store.Conn.QueryContext(ctx, "SELECT space,generation,clients.id,token_digest FROM clients JOIN spaces ON space=spaces.id")
	if err != nil {
		return err
	}
	defer rows.Close()
	for rows.Next() {
		var p principal
		if err := rows.Scan(&p.space, &p.generation, &p.client, &p.digest); err != nil {
			return err
		}
		if !protocol.ValidName(p.space) || !protocol.ValidName(p.client) || !protocol.IsHex(p.generation, 32) || len(p.digest) != 32 {
			return errors.New("invalid persisted hub registration")
		}
		h.principals = append(h.principals, p)
	}
	if len(h.principals) == 0 {
		return errors.New("no registered hub clients")
	}
	return rows.Err()
}

func (h *Hub) authenticate(token string) (principal, bool) {
	digest, valid := protocol.TokenDigest(token)
	var found principal
	for _, p := range h.principals {
		if subtle.ConstantTimeCompare(digest[:], p.digest) == 1 {
			found = p
		}
	}
	return found, valid && found.client != ""
}

// failure is called with mu held. Capacity exhaustion before a known commit
// can be reported without making verified stored objects unavailable.
func (h *Hub) failure(err error) *protocol.Problem {
	var unknown *dbstore.OutcomeError
	var database sqlite3.Error
	if !errors.As(err, &unknown) && (errors.Is(err, unix.ENOSPC) || (errors.As(err, &database) && database.Code == sqlite3.ErrFull)) {
		return problem(507, "storage_full")
	}
	return h.unavailable()
}

func (h *Hub) unavailable() *protocol.Problem {
	h.stopped = true
	return problem(503, "storage_unavailable")
}

func (h *Hub) verifyBlob(name string, expected safefs.Digest) (safefs.Identity, error) {
	digest, id, err := h.blobs.ReadPrivate(name, nil)
	if err != nil {
		return id, err
	}
	if digest != expected {
		return id, fmt.Errorf("stored blob %s does not match its registration", name)
	}
	return id, nil
}
