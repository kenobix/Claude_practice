import * as Y from 'yjs';

export type PendingUpdate = { messageId: string; bytes: Uint8Array; repair?: boolean };
type SavedState = { scope: string; bytes: Uint8Array; unconfirmed: number; blocked?: string };
export type LocalDraft = { scope: string; documentId: string; revision: number; blocked?: string };
const EMPTY_STATE = new Uint8Array([0, 0]);

function result<T>(request: IDBRequest<T>) {
  return new Promise<T>((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function complete(transaction: IDBTransaction) {
  return new Promise<void>((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error('IndexedDB transaction aborted'));
  });
}

function databaseName(userId: string) { return `collab-${userId}`; }

export function withCacheLock<T>(userId: string, action: () => Promise<T>) {
  return navigator.locks.request(databaseName(userId), action);
}

export function deleteCache(userId: string) {
  return result(indexedDB.deleteDatabase(databaseName(userId)));
}

export async function openCache(userId: string, onClosed: () => void) {
  const request = indexedDB.open(databaseName(userId), 1);
  request.onupgradeneeded = () => {
    const db = request.result;
    db.createObjectStore('device');
    db.createObjectStore('states', { keyPath: 'scope' });
    const outbox = db.createObjectStore('outbox', { keyPath: 'order', autoIncrement: true });
    outbox.createIndex('scope', 'scope');
    outbox.createIndex('message', ['scope', 'messageId'], { unique: true });
  };
  const db = await result(request);
  const lifetime = new AbortController();
  function close() {
    db.close();
    lifetime.abort();
  }
  db.onversionchange = () => { close(); onClosed(); };
  const meta = db.transaction('device', 'readwrite', { durability: 'strict' });
  const metaDone = complete(meta);
  const device = meta.objectStore('device');
  let clientId: string;
  const lookup = device.get('clientId');
  lookup.onsuccess = () => {
    clientId = lookup.result ?? crypto.randomUUID();
    if (!lookup.result) device.put(clientId, 'clientId');
  };
  try { await metaDone; } catch (error) { db.close(); throw error; }

  return {
    clientId: clientId!,
    signal: lifetime.signal,
    async load(scope: string) {
      const tx = db.transaction(['states', 'outbox'], 'readonly');
      const completeRead = complete(tx);
      const [state, pending] = await Promise.all([
        result(tx.objectStore('states').get(scope)) as Promise<SavedState | undefined>,
        result(tx.objectStore('outbox').index('scope').getAll(scope)) as Promise<PendingUpdate[]>,
        completeRead,
      ]);
      return { state, pending };
    },
    save(scope: string, bytes: Uint8Array, pending?: PendingUpdate) {
      const tx = db.transaction(['states', 'outbox'], 'readwrite', { durability: 'strict' });
      const states = tx.objectStore('states');
      const outbox = tx.objectStore('outbox');
      const previous = states.get(scope);
      const count = outbox.index('scope').count(scope);
      let blocked = false;
      // Requests on this transaction run in order. Queue all writes before the callback returns.
      count.onsuccess = () => {
        try {
          const saved = previous.result as SavedState | undefined;
          blocked = Boolean(saved?.blocked);
          if (blocked && !pending) return;
          const unconfirmed = pending?.repair ? count.result : Math.max(count.result, saved?.unconfirmed ?? 0);
          states.put({ scope, bytes: saved ? Y.mergeUpdates([saved.bytes, bytes]) : bytes,
            unconfirmed: unconfirmed + Number(Boolean(pending)), blocked: saved?.blocked });
          if (pending) outbox.add({ ...pending, scope });
        } catch { tx.abort(); }
      };
      return complete(tx).then(() => !blocked);
    },
    acknowledge(scope: string, messageId: string) {
      const tx = db.transaction(['states', 'outbox'], 'readwrite', { durability: 'strict' });
      const states = tx.objectStore('states');
      const outbox = tx.objectStore('outbox');
      const previous = states.get(scope);
      const key = outbox.index('message').getKey([scope, messageId]);
      key.onsuccess = () => {
        if ((previous.result as SavedState | undefined)?.blocked) return;
        if (key.result === undefined) return;
        outbox.delete(key.result);
        const count = outbox.index('scope').count(scope);
        count.onsuccess = () => {
          const saved = previous.result as SavedState | undefined;
          if (saved) states.put({ ...saved, unconfirmed: Math.max(count.result, saved.unconfirmed - 1) });
        };
      };
      return complete(tx);
    },
    async drafts(documentId?: string): Promise<LocalDraft[]> {
      const tx = db.transaction(['states', 'outbox']);
      const range = documentId ? IDBKeyRange.bound(`${documentId}:`, `${documentId}:\uffff`) : undefined;
      const exportable = new Set<string>();
      const blockedScopes = new Map<string, string>();
      const pending = tx.objectStore('outbox').index('scope').openKeyCursor(range, 'nextunique');
      pending.onsuccess = () => {
        const cursor = pending.result;
        if (!cursor) return;
        exportable.add(String(cursor.key));
        cursor.continue();
      };
      const states = tx.objectStore('states').openCursor(range);
      states.onsuccess = () => {
        const cursor = states.result;
        if (!cursor) return;
        const { scope, unconfirmed, blocked } = cursor.value as SavedState;
        if (unconfirmed > 0 || blocked === 'LINE_ENDING') exportable.add(scope);
        if (blocked) blockedScopes.set(scope, blocked);
        cursor.continue();
      };
      await complete(tx);
      // Only a line-ending quarantine needs the cached body even without pending edits.
      return [...exportable].map((scope) => {
        const [id, revision] = scope.split(':');
        return { scope, documentId: id, revision: Number(revision), blocked: blockedScopes.get(scope) };
      });
    },
    quarantine(scope: string, code: string) {
      const tx = db.transaction('states', 'readwrite', { durability: 'strict' });
      const states = tx.objectStore('states');
      const previous = states.get(scope);
      previous.onsuccess = () => {
        const saved = previous.result as SavedState | undefined;
        if (!saved?.blocked) states.put({ scope, bytes: EMPTY_STATE, unconfirmed: 0, ...saved, blocked: code });
      };
      return complete(tx);
    },
    close,
  };
}

export type LocalCache = Awaited<ReturnType<typeof openCache>>;

export async function readDraftText(cache: LocalCache, scope: string) {
  const { state, pending } = await cache.load(scope);
  const doc = new Y.Doc();
  try {
    const body = doc.getText('body');
    if (state) Y.applyUpdate(doc, state.bytes);
    for (const { bytes } of pending) Y.applyUpdate(doc, bytes);
    return { text: body.toString(), incomplete: Boolean(doc.store.pendingStructs || doc.store.pendingDs) };
  } finally {
    doc.destroy();
  }
}
