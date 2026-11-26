import { Compartment, EditorState } from '@codemirror/state';
import { EditorView, highlightActiveLineGutter, keymap, lineNumbers } from '@codemirror/view';
import { defaultKeymap } from '@codemirror/commands';
import * as Y from 'yjs';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate, removeAwarenessStates } from 'y-protocols/awareness';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import { decodeFrame, encodeFrame, isId, isRole, isUint, MAX_PAYLOAD, VERSION } from '../shared/protocol.ts';
import type { Role, User } from '../shared/protocol.ts';
import type { LocalCache, PendingUpdate } from './cache.ts';
import { lookupAccess, quarantineDrafts } from './access.ts';
import { prepareRecovery } from './recovery.ts';
import { errorText } from './words.ts';

export type RoomStatus = {
  connection: 'joining' | 'online' | 'offline';
  sequence: number | null;
  role: Role | null;
  users: User[];
  problem: string;
  localSave: 'idle' | 'writing' | 'saved' | 'failed';
  serverSave: 'idle' | 'waiting' | 'saved' | 'blocked';
  retrying: boolean;
  sessionExpired: boolean;
  volatileDraft?: string;
};

const ACK_TIMEOUT = 2_500;
const BLOCKED = new Set(['DOCUMENT_DELETED', 'ACCESS_DENIED', 'GRANT_CHANGED', 'WRITE_FORBIDDEN',
  'TEXT_LIMIT', 'UPDATE_LIMIT', 'STATE_LIMIT', 'FRAME_LIMIT', 'INVALID_UPDATE', 'MESSAGE_ID_CONFLICT']);
const HIDE_BODY = new Set(['DOCUMENT_DELETED', 'ACCESS_DENIED', 'GRANT_CHANGED', 'WRITE_FORBIDDEN']);

export function openRoom(documentId: string, user: User, parent: HTMLElement, initialStatus: RoomStatus,
  cache: LocalCache, onStatus: (status: RoomStatus) => void, draftsChanged: () => void, reconnect: () => void) {
  const doc = new Y.Doc();
  const text = doc.getText('body');
  const awareness = new Awareness(doc);
  const undo = new Y.UndoManager(text);
  const remote = Symbol('WebSocket');
  const editability = new Compartment();
  let editor: EditorView | undefined;
  let status = initialStatus;
  let ws: WebSocket | undefined;
  let scope: string | undefined;
  let grantRevision = 0;
  let pending: PendingUpdate[] = [];
  let inflight: string | undefined;
  let localWrites = 0;
  let confirmed = false;
  let failed = false;
  let blocked = false;
  let disposed = false;
  let joinTimer: number | undefined;
  let sendTimer: number | undefined;
  let work = Promise.resolve();

  function publish(change: Partial<RoomStatus>) {
    if (disposed) return;
    status = { ...status, ...change };
    onStatus(status);
  }

  function publishSave() {
    publish({
      localSave: failed ? 'failed' : localWrites > 0 ? 'writing' : scope ? 'saved' : 'idle',
      serverSave: blocked ? 'blocked' : localWrites > 0 || pending.length > 0
        ? 'waiting'
        : confirmed && !failed ? 'saved' : 'idle',
    });
  }

  function renderUsers() {
    if (status.connection !== 'online') return;
    const users = new Map([[user.id, user]]);
    for (const state of awareness.getStates().values()) if (state.user) users.set(state.user.id, state.user);
    publish({ users: [...users.values()] });
  }

  function editAccess(allowed: boolean) {
    return [EditorState.readOnly.of(!allowed), EditorView.editable.of(allowed)];
  }

  function stop(problem: string, allowOffline = false) {
    window.clearTimeout(joinTimer);
    window.clearTimeout(sendTimer);
    inflight = undefined;
    if (disposed) return;
    publish({ connection: 'offline', problem, users: [], retrying: false });
    editor?.dispatch({ effects: editability.reconfigure(editAccess(allowOffline && !failed && !blocked && status.role !== 'viewer')) });
    removeAwarenessStates(awareness, [...awareness.getStates().keys()].filter((id) => id !== doc.clientID), remote);
    ws?.close();
  }

  function failLocal() {
    if (disposed) return;
    failed = true;
    const hasDraft = pending.length > 0 || localWrites > 0;
    if (hasDraft) publish({ volatileDraft: text.toString() });
    stop(hasDraft ? '端末への保存に失敗しました。必要な本文を書き出してから接続し直してください。'
      : '端末内のデータを処理できませんでした。接続し直してください。');
    publishSave();
  }

  function enqueue(action: () => Promise<void>) {
    work = work.then(async () => { if (!failed) await action(); }).catch(failLocal);
  }

  async function refuse(code: string) {
    if (disposed) return;
    stop(errorText(code), code === 'STORAGE_RECOVERY');
    if (code === 'SESSION_EXPIRED') publish({ sessionExpired: true });
    if (!BLOCKED.has(code)) return;
    blocked = true;
    confirmed = false;
    if (HIDE_BODY.has(code)) {
      editor?.destroy();
      editor = undefined;
      publish({ role: null, sequence: null });
    }
    publishSave();
    if (scope) await cache.quarantine(scope, code);
    if (code === 'ACCESS_DENIED' || code === 'DOCUMENT_DELETED') {
      await quarantineDrafts(cache, await cache.drafts(documentId), documentId, code);
    }
    draftsChanged();
    if (code === 'GRANT_CHANGED') void beforeReconnect().then(() => { if (!disposed) reconnect(); });
  }

  function pump() {
    if (disposed || failed || blocked || status.connection !== 'online' || ws?.readyState !== WebSocket.OPEN || inflight) return;
    const item = pending[0];
    if (!item) return;
    if (item.bytes.byteLength > MAX_PAYLOAD) {
      enqueue(() => refuse('UPDATE_LIMIT'));
      return;
    }
    inflight = item.messageId;
    ws.send(encodeFrame({ type: 'UPDATE', client_id: cache.clientId, grant_revision: grantRevision, message_id: item.messageId }, item.bytes));
    sendTimer = window.setTimeout(() => { inflight = undefined; pump(); }, ACK_TIMEOUT);
  }

  doc.on('update', (bytes: Uint8Array, origin: unknown, _doc: Y.Doc, transaction: Y.Transaction) => {
    const activeScope = scope;
    if (origin === remote || !transaction.local || !activeScope || failed || blocked || status.role === 'viewer') return;
    const item = { bytes, messageId: crypto.randomUUID() };
    const snapshot = Y.encodeStateAsUpdate(doc);
    localWrites += 1;
    confirmed = false;
    publishSave();
    enqueue(async () => {
      const writable = await cache.save(activeScope, snapshot, item);
      localWrites -= 1;
      if (!writable) {
        draftsChanged();
        if (!blocked) await refuse('GRANT_CHANGED');
        return;
      }
      pending.push(item);
      publishSave();
      pump();
    });
  });
  awareness.on('change', renderUsers);
  awareness.on('update', (_change: unknown, origin: unknown) => {
    if (origin === 'local' && status.connection === 'online' && ws?.readyState === WebSocket.OPEN) {
      ws.send(encodeFrame({ type: 'AWARENESS' }, encodeAwarenessUpdate(awareness, [doc.clientID])));
    }
  });

  async function receive(header: Record<string, unknown>, payload: Uint8Array) {
    if (disposed || status.connection === 'offline') return;
    if (header.type === 'ERROR' && typeof header.code === 'string') {
      if (header.code === 'RATE_LIMIT') {
        if (inflight && header.message_id === inflight) {
          publish({ retrying: true });
          window.clearTimeout(sendTimer);
          sendTimer = window.setTimeout(() => { inflight = undefined; pump(); }, 350);
        }
        return;
      }
      await refuse(header.code);
      return;
    }
    if (status.connection === 'joining') {
      if (header.type !== 'STATE' || header.version !== VERSION || !isRole(header.role) ||
          !isUint(header.grant_revision) || !isUint(header.sequence)) return stop(errorText('SYNC_SEQUENCE'));
      const nextScope = `${documentId}:${header.grant_revision}`;
      const drafts = await cache.drafts(documentId);
      await quarantineDrafts(cache, drafts, documentId, 'GRANT_CHANGED', header.grant_revision);
      const { state, pending: stored } = await cache.load(nextScope);
      if (disposed || status.connection !== 'joining') return;
      blocked = Boolean(state?.blocked);
      const plan = header.role === 'viewer' || blocked
        ? { complete: payload, ordered: [], repair: undefined }
        : prepareRecovery(payload, state?.bytes, stored, (state?.unconfirmed ?? 0) > stored.length);
      const repair = plan.repair && { messageId: crypto.randomUUID(), bytes: plan.repair, repair: true };
      if (!blocked && !(await cache.save(nextScope, plan.complete, repair))) {
        await refuse('GRANT_CHANGED');
        return;
      }
      if (disposed || status.connection !== 'joining') return;
      scope = nextScope;
      grantRevision = header.grant_revision;
      pending = repair ? [repair, ...plan.ordered] : plan.ordered;
      Y.applyUpdate(doc, plan.complete, remote);
      editor = new EditorView({
        parent,
        doc: text.toString(),
        extensions: [
          lineNumbers(), highlightActiveLineGutter(), EditorView.lineWrapping,
          keymap.of([...yUndoManagerKeymap, ...defaultKeymap]), yCollab(text, awareness, { undoManager: undo }),
          editability.of(editAccess(!blocked && header.role !== 'viewer')),
          EditorView.contentAttributes.of({ 'aria-label': '本文' }),
        ],
      });
      window.clearTimeout(joinTimer);
      publish({ connection: 'online', role: header.role, sequence: header.sequence, users: [user],
        problem: state?.blocked ? errorText(state.blocked) : '' });
      publishSave();
      draftsChanged();
      pump();
      return;
    }
    if (header.type === 'APPLIED' && status.sequence !== null && header.sequence === status.sequence + 1 && scope) {
      Y.applyUpdate(doc, payload, remote);
      publish({ sequence: header.sequence });
      if (!blocked) await cache.save(scope, payload);
      return;
    }
    if (header.type === 'ACK' && isId(header.message_id) && header.client_id === cache.clientId &&
        isUint(header.sequence) && scope) {
      const id = header.message_id;
      if (!pending.some(({ messageId }) => messageId === id)) return;
      await cache.acknowledge(scope, id);
      pending = pending.filter(({ messageId }) => messageId !== id);
      if (inflight === id) {
        window.clearTimeout(sendTimer);
        inflight = undefined;
      }
      confirmed = true;
      publish({ retrying: false });
      publishSave();
      pump();
      return;
    }
    if (header.type === 'AWARENESS') {
      applyAwarenessUpdate(awareness, payload, remote);
      return;
    }
    stop(errorText('SYNC_SEQUENCE'), true);
  }

  function connect() {
    const url = new URL(`/ws/${documentId}`, window.location.href);
    url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const socket = new WebSocket(url);
    ws = socket;
    socket.binaryType = 'arraybuffer';
    socket.onopen = () => {
      socket.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: cache.clientId, awareness_id: doc.clientID }));
    };
    socket.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
      enqueue(async () => {
        let frame;
        try { frame = decodeFrame(new Uint8Array(data)); }
        catch { stop(errorText('SYNC_SEQUENCE'), true); return; }
        await receive(frame.header, frame.payload);
      });
    };
    socket.onclose = ({ reason, code }) => {
      // Process the preceding frames before reacting to the close.
      enqueue(async () => {
        if (disposed || status.connection === 'offline') return;
        if (code === 1009) await refuse('FRAME_LIMIT');
        else if (reason) await refuse(reason);
        else if (status.connection === 'joining') {
          const access = await lookupAccess(documentId).catch(() => null);
          if (typeof access === 'string') await refuse(access);
          else stop('接続できませんでした。共有設定と接続枠を確認してください。');
        } else if (status.role === 'viewer') stop('接続が切れました。表示中の本文を閲覧できます。');
        else stop('接続が切れました。編集は端末に保存し、次の接続時に送ります。', true);
      });
    };
    joinTimer = window.setTimeout(() => stop('接続がタイムアウトしました。接続し直してください。'), 8_000);
  }

  async function beforeReconnect() {
    editor?.dispatch({ effects: editability.reconfigure(editAccess(false)) });
    await work;
  }

  function close() {
    if (disposed) return;
    disposed = true;
    cache.signal.removeEventListener('abort', close);
    window.clearTimeout(joinTimer);
    window.clearTimeout(sendTimer);
    if (ws) {
      ws.onopen = ws.onmessage = ws.onclose = null;
      ws.close();
    }
    editor?.destroy();
    undo.destroy();
    doc.destroy();
  }

  if (cache.signal.aborted) close();
  else {
    cache.signal.addEventListener('abort', close);
    connect();
  }
  return { beforeReconnect, close };
}
