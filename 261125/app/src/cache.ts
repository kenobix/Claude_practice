import * as Y from 'yjs';

export type PendingUpdate = { messageId: string; bytes: Uint8Array; repair?: boolean };
type SavedState = { scope: string; bytes: Uint8Array; unconfirmed: number };

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
  db.onversionchange = () => { db.close(); onClosed(); };
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
      // Requests on this transaction run in order. Queue all writes before the callback returns.
      count.onsuccess = () => {
        try {
          const saved = previous.result as SavedState | undefined;
          const unconfirmed = pending?.repair ? count.result : Math.max(count.result, saved?.unconfirmed ?? 0);
          states.put({ scope, bytes: saved ? Y.mergeUpdates([saved.bytes, bytes]) : bytes,
            unconfirmed: unconfirmed + Number(Boolean(pending)) });
          if (pending) outbox.add({ ...pending, scope });
        } catch { tx.abort(); }
      };
      return complete(tx);
    },
    acknowledge(scope: string, messageId: string) {
      const tx = db.transaction(['states', 'outbox'], 'readwrite', { durability: 'strict' });
      const states = tx.objectStore('states');
      const outbox = tx.objectStore('outbox');
      const previous = states.get(scope);
      const key = outbox.index('message').getKey([scope, messageId]);
      key.onsuccess = () => {
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
    close() { db.close(); },
  };
}

export type LocalCache = Awaited<ReturnType<typeof openCache>>;
