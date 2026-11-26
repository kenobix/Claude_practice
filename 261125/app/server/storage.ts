import { DatabaseSync } from 'node:sqlite';
import * as Y from 'yjs';
import { MAX_PAYLOAD } from '../shared/protocol.ts';
import type { Access } from '../shared/protocol.ts';

export type Checkpoint = 'before-load' | 'before-commit' | 'after-commit' | 'after-ack' | 'snapshot-written';
export type StorageOptions = { databasePath: string; snapshotEvery?: number; checkpoint?: (point: Checkpoint) => void };
export type Operation = { document: string; user: string; client: string; message: string; hash: string };
type DocumentRow = { id: string; title: string; owner: string; acl_revision: number; sequence: number };
type GrantRow = { user_id: string; role: Access; revision: number };
type SnapshotRow = { sequence: number; state: Uint8Array };
type UpdateRow = { sequence: number; payload: Uint8Array };

const schema = `
  CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, owner TEXT NOT NULL, created_at INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','deleted')),
    acl_revision INTEGER NOT NULL DEFAULT 0, sequence INTEGER NOT NULL DEFAULT 0
  ) STRICT;
  CREATE TABLE IF NOT EXISTS grants (
    document_id TEXT NOT NULL REFERENCES documents(id), user_id TEXT NOT NULL,
    role TEXT CHECK(role IN ('editor','viewer')), revision INTEGER NOT NULL, changed_at INTEGER NOT NULL,
    PRIMARY KEY(document_id, user_id)
  ) STRICT;
  CREATE TABLE IF NOT EXISTS receipts (
    document_id TEXT NOT NULL REFERENCES documents(id), user_id TEXT NOT NULL,
    client_id TEXT NOT NULL, message_id TEXT NOT NULL, hash TEXT NOT NULL,
    sequence INTEGER NOT NULL, accepted_at INTEGER NOT NULL,
    PRIMARY KEY(document_id, user_id, client_id, message_id)
  ) STRICT;
  CREATE TABLE IF NOT EXISTS updates (
    document_id TEXT NOT NULL REFERENCES documents(id), sequence INTEGER NOT NULL,
    payload BLOB NOT NULL, PRIMARY KEY(document_id, sequence)
  ) STRICT;
  CREATE TABLE IF NOT EXISTS snapshots (
    document_id TEXT PRIMARY KEY REFERENCES documents(id), sequence INTEGER NOT NULL,
    state BLOB NOT NULL, created_at INTEGER NOT NULL
  ) STRICT;
`;

function open(filename: string) {
  const db = new DatabaseSync(filename);
  try {
    const mode = db.prepare('PRAGMA journal_mode=WAL').get()?.journal_mode;
    if (mode !== 'wal') throw new Error('The database needs file-backed WAL mode');
    db.exec('PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON; PRAGMA busy_timeout=5000;');
    db.exec(schema);
    return db;
  } catch (error) {
    db.close();
    throw error;
  }
}

export function createStorage({ databasePath, checkpoint }: StorageOptions) {
  let db = open(databasePath);

  function transaction(write: () => void, beforeCommit?: () => void) {
    db.exec('BEGIN IMMEDIATE');
    try {
      write();
      beforeCommit?.();
      db.exec('COMMIT');
    } catch (error) {
      if (db.isTransaction) {
        try { db.exec('ROLLBACK'); } catch { /* Reopen the handle to resolve an uncertain outcome. */ }
      }
      throw error;
    }
  }

  return {
    load() {
      checkpoint?.('before-load');
      const documents = db.prepare("SELECT id, title, owner, acl_revision, sequence FROM documents WHERE status='active' ORDER BY rowid");
      const snapshot = db.prepare('SELECT sequence, state FROM snapshots WHERE document_id=?');
      const updates = db.prepare('SELECT sequence, payload FROM updates WHERE document_id=? AND sequence>? ORDER BY sequence');
      const grants = db.prepare('SELECT user_id, role, revision FROM grants WHERE document_id=?');
      return (documents.all() as DocumentRow[]).map((row) => {
        const snap = snapshot.get(row.id) as SnapshotRow | undefined;
        const doc = new Y.Doc();
        try {
          doc.getText('body');
          let sequence = snap?.sequence ?? 0;
          if (snap) Y.applyUpdate(doc, snap.state);
          for (const update of updates.iterate(row.id, sequence) as Iterable<UpdateRow>) {
            if (update.sequence !== sequence + 1) throw new Error(`Update gap in ${row.id}`);
            Y.applyUpdate(doc, update.payload);
            sequence = update.sequence;
          }
          if (sequence !== row.sequence || doc.store.pendingStructs || doc.store.pendingDs) {
            throw new Error(`Incomplete stored state in ${row.id}`);
          }
          const state = Y.encodeStateAsUpdate(doc);
          if (state.byteLength > MAX_PAYLOAD) throw new Error(`Stored state too large in ${row.id}`);
          return { ...row, state, snapshotSequence: snap?.sequence ?? 0, grants: grants.all(row.id) as GrantRow[] };
        } finally {
          doc.destroy();
        }
      });
    },
    create(id: string, title: string, owner: string) {
      db.prepare('INSERT INTO documents (id,title,owner,created_at) VALUES (?,?,?,?)').run(id, title, owner, Date.now());
    },
    setGrant(id: string, user: string, role: Access, revision: number, aclRevision: number) {
      transaction(() => {
        db.prepare(`INSERT INTO grants VALUES (?,?,?,?,?) ON CONFLICT(document_id,user_id)
          DO UPDATE SET role=excluded.role, revision=excluded.revision, changed_at=excluded.changed_at`)
          .run(id, user, role, revision, Date.now());
        db.prepare('UPDATE documents SET acl_revision=? WHERE id=?').run(aclRevision, id);
      });
    },
    deleteDocument(id: string) {
      db.prepare("UPDATE documents SET status='deleted', acl_revision=acl_revision+1 WHERE id=? AND status='active'").run(id);
    },
    deletedFor(id: string, user: string) {
      return Boolean(db.prepare(`SELECT 1 FROM documents d LEFT JOIN grants g
        ON g.document_id=d.id AND g.user_id=?
        WHERE d.id=? AND d.status='deleted' AND (d.owner=? OR g.role IS NOT NULL)`).get(user, id, user));
    },
    receipt(operation: Operation) {
      return db.prepare(`SELECT hash, sequence FROM receipts WHERE document_id=? AND user_id=? AND client_id=? AND message_id=?`)
        .get(operation.document, operation.user, operation.client, operation.message) as { hash: string; sequence: number } | undefined;
    },
    accept(operation: Operation, sequence: number, update?: Uint8Array) {
      transaction(() => {
        const { document, user, client, message, hash } = operation;
        if (update) {
          db.prepare('INSERT INTO updates VALUES (?,?,?)').run(document, sequence, update);
          db.prepare('UPDATE documents SET sequence=? WHERE id=?').run(sequence, document);
        }
        db.prepare('INSERT INTO receipts VALUES (?,?,?,?,?,?,?)').run(document, user, client, message, hash, sequence, Date.now());
      }, () => checkpoint?.('before-commit'));
      checkpoint?.('after-commit');
    },
    snapshot(id: string, sequence: number, state: Uint8Array) {
      transaction(() => {
        db.prepare(`INSERT INTO snapshots VALUES (?,?,?,?) ON CONFLICT(document_id)
          DO UPDATE SET sequence=excluded.sequence, state=excluded.state, created_at=excluded.created_at`)
          .run(id, sequence, state, Date.now());
        checkpoint?.('snapshot-written');
        db.prepare('DELETE FROM updates WHERE document_id=? AND sequence<=?').run(id, sequence);
      });
    },
    reopen() {
      if (db.isOpen) db.close();
      db = open(databasePath);
    },
    close() {
      if (db.isOpen) db.close();
    },
  };
}
