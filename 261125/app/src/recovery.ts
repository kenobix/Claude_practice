import * as Y from 'yjs';
import type { PendingUpdate } from './cache.ts';

export function prepareRecovery(serverState: Uint8Array, cached: Uint8Array | undefined, outbox: PendingUpdate[], missingReceipts: boolean) {
  const replay = new Y.Doc();
  const desired = new Y.Doc();
  try {
    replay.getText('body');
    desired.getText('body');
    Y.applyUpdate(replay, serverState);
    const serverVector = Y.encodeStateVector(replay);
    Y.applyUpdate(desired, serverState);
    if (cached) Y.applyUpdate(desired, cached);
    const ordered = [...outbox.filter(({ repair }) => repair), ...outbox.filter(({ repair }) => !repair)];
    let missingDependency = false;
    for (const { bytes } of ordered) {
      Y.applyUpdate(replay, bytes);
      Y.applyUpdate(desired, bytes);
      if (replay.store.pendingStructs || replay.store.pendingDs) missingDependency = true;
    }
    if (desired.store.pendingStructs || desired.store.pendingDs) throw new Error('Incomplete local update history');
    const complete = Y.encodeStateAsUpdate(desired);
    const replayed = Y.encodeStateAsUpdate(replay);
    const differs = complete.length !== replayed.length || complete.some((byte, index) => byte !== replayed[index]);
    const repair = differs || missingDependency || missingReceipts ? Y.encodeStateAsUpdate(desired, serverVector) : undefined;
    return { complete, ordered, repair };
  } finally {
    replay.destroy();
    desired.destroy();
  }
}
