import { Compartment, EditorState } from '@codemirror/state';
import { EditorView, highlightActiveLineGutter, keymap, lineNumbers } from '@codemirror/view';
import { defaultKeymap } from '@codemirror/commands';
import * as Y from 'yjs';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness';
import { yCollab, yUndoManagerKeymap } from 'y-codemirror.next';
import { decodeFrame, encodeFrame, isRole, isUint, VERSION } from '../shared/protocol.ts';
import type { Role, User } from '../shared/protocol.ts';
import { errorText } from './words.ts';

export type RoomStatus = {
  connection: 'joining' | 'online' | 'offline';
  sequence: number | null;
  role: Role | null;
  users: User[];
  problem: string;
};

export function openRoom(documentId: string, user: User, parent: HTMLElement, initialStatus: RoomStatus, onStatus: (status: RoomStatus) => void) {
  const doc = new Y.Doc();
  const text = doc.getText('body');
  const awareness = new Awareness(doc);
  const undo = new Y.UndoManager(text);
  const remote = Symbol('WebSocket');
  const clientId = crypto.randomUUID();
  const url = new URL(`/ws/${documentId}`, window.location.href);
  url.protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const ws = new WebSocket(url);
  ws.binaryType = 'arraybuffer';
  const editability = new Compartment();
  let editor: EditorView | undefined;
  let status = initialStatus;
  let grantRevision = 0;
  let disposed = false;
  let joinTimer: number;

  function publish(change: Partial<RoomStatus>) {
    if (disposed) return;
    status = { ...status, ...change };
    onStatus(status);
  }

  function renderUsers() {
    if (status.connection !== 'online') return;
    const users = new Map([[user.id, user]]);
    for (const state of awareness.getStates().values()) {
      if (state.user) users.set(state.user.id, state.user);
    }
    publish({ users: [...users.values()] });
  }

  function editAccess(allowed: boolean) {
    return [EditorState.readOnly.of(!allowed), EditorView.editable.of(allowed)];
  }

  function stop(problem: string) {
    window.clearTimeout(joinTimer);
    if (disposed || status.connection === 'offline') return;
    publish({ connection: 'offline', problem, users: [] });
    editor?.dispatch({ effects: editability.reconfigure(editAccess(false)) });
    ws.close();
  }

  doc.on('update', (update: Uint8Array, origin: unknown, _doc: Y.Doc, transaction: Y.Transaction) => {
    if (origin === remote || !transaction.local || status.connection !== 'online' || status.role === 'viewer') return;
    if (ws.readyState !== WebSocket.OPEN) {
      queueMicrotask(() => stop('接続が切れました。本文を控えてから接続し直してください。'));
      return;
    }
    try {
      ws.send(encodeFrame({ type: 'UPDATE', client_id: clientId, grant_revision: grantRevision,
        message_id: crypto.randomUUID() }, update));
    } catch {
      queueMicrotask(() => stop(errorText('UPDATE_LIMIT')));
    }
  });
  awareness.on('change', renderUsers);
  awareness.on('update', (_change: unknown, origin: unknown) => {
    if (origin === 'local' && status.connection === 'online' && ws.readyState === WebSocket.OPEN) {
      ws.send(encodeFrame({ type: 'AWARENESS' }, encodeAwarenessUpdate(awareness, [doc.clientID])));
    }
  });

  ws.onopen = () => {
    ws.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: clientId, awareness_id: doc.clientID }));
  };
  ws.onmessage = ({ data }: MessageEvent<ArrayBuffer>) => {
    if (disposed || status.connection === 'offline') return;
    try {
      const { header, payload } = decodeFrame(new Uint8Array(data));
      if (header.type === 'ERROR' && typeof header.code === 'string') return stop(errorText(header.code));
      if (status.connection === 'joining') {
        if (header.type !== 'STATE' || header.version !== VERSION || !isRole(header.role) ||
            !isUint(header.grant_revision) || !isUint(header.sequence)) throw new Error('Expected initial document');
        Y.applyUpdate(doc, payload, remote);
        grantRevision = header.grant_revision;
        editor = new EditorView({
          parent,
          doc: text.toString(),
          extensions: [
            lineNumbers(), highlightActiveLineGutter(), EditorView.lineWrapping,
            keymap.of([...yUndoManagerKeymap, ...defaultKeymap]), yCollab(text, awareness, { undoManager: undo }),
            editability.of(editAccess(header.role !== 'viewer')),
            EditorView.contentAttributes.of({ 'aria-label': '本文' }),
          ],
        });
        window.clearTimeout(joinTimer);
        publish({ connection: 'online', role: header.role, sequence: header.sequence, users: [user] });
      } else if (header.type === 'APPLIED' && status.sequence !== null && header.sequence === status.sequence + 1) {
        Y.applyUpdate(doc, payload, remote);
        publish({ sequence: header.sequence });
      } else if (header.type === 'AWARENESS') {
        applyAwarenessUpdate(awareness, payload, remote);
      } else {
        throw new Error('Unexpected frame or sequence');
      }
    } catch {
      stop('更新の順序を確認できませんでした。本文を控えてから接続し直してください。');
    }
  };
  ws.onclose = ({ reason }) => {
    if (reason) stop(errorText(reason));
    else if (status.connection === 'joining') stop('接続できませんでした。共有設定と接続枠を確認してください。');
    else stop('接続が切れました。本文を控えてから接続し直してください。');
  };
  joinTimer = window.setTimeout(() => stop('接続がタイムアウトしました。接続し直してください。'), 8_000);

  return () => {
    disposed = true;
    window.clearTimeout(joinTimer);
    ws.onopen = ws.onmessage = ws.onclose = null;
    ws.close();
    editor?.destroy();
    undo.destroy();
    doc.destroy();
  };
}
