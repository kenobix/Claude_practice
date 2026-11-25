import { createHash, randomUUID } from 'node:crypto';
import { WebSocket, WebSocketServer } from 'ws';
import type { IncomingMessage } from 'node:http';
import type { Duplex } from 'node:stream';
import * as Y from 'yjs';
import type { Access, DocumentSummary, Message, Role, User } from '../shared/protocol.ts';
import {
  decodeFrame, encodeFrame, isId, isUint, MAX_BODY, MAX_CONNECTIONS, MAX_FRAME, MAX_PAYLOAD, VERSION,
} from '../shared/protocol.ts';
import { encodePresence, readCursor } from './presence.ts';
import type { PresenceEntry } from './presence.ts';
import { createStorage } from './storage.ts';
import type { StorageOptions } from './storage.ts';

const HEARTBEAT_MS = 5_000;
const BURST = 20;
const UPDATE_INTERVAL_MS = 100;
const emptyDoc = new Y.Doc();
const EMPTY_STATE = Y.encodeStateAsUpdate(emptyDoc);
emptyDoc.destroy();
const COLORS: Record<string, [string, string]> = {
  aki: ['#244bc5', '#244bc540'],
  ren: ['#8c386f', '#8c386f40'],
  sora: ['#186354', '#18635440'],
};

type Peer = {
  socket: WebSocket;
  token: string;
  user: User;
  clientId?: string;
  presence?: PresenceEntry;
  alive: boolean;
};

type Room = {
  id: string;
  title: string;
  owner: string;
  grants: Map<string, { role: Access; revision: number }>;
  updateBudget: Map<string, { tokens: number; checked: number }>;
  aclRevision: number;
  sequence: number;
  snapshotSequence: number;
  state: Uint8Array;
  peers: Set<Peer>;
  presenceClock: number;
};

function roleOf(room: Room, userId: string): Role | null {
  return room.owner === userId ? 'owner' : room.grants.get(userId)?.role ?? null;
}

function grantRevision(room: Room, userId: string) {
  return room.grants.get(userId)?.revision ?? 0;
}

function summary(room: Room, userId: string): DocumentSummary | null {
  const role = roleOf(room, userId);
  if (!role) return null;
  return {
    id: room.id,
    title: room.title,
    role,
    ...(role === 'owner' ? { access: Object.fromEntries([...room.grants].map(([id, grant]) => [id, grant.role])) } : {}),
  };
}

function takeUpdateToken(room: Room, userId: string) {
  const now = performance.now();
  const previous = room.updateBudget.get(userId);
  const available = previous ? Math.min(BURST, previous.tokens + (now - previous.checked) / UPDATE_INTERVAL_MS) : BURST;
  const allowed = available >= 1;
  room.updateBudget.set(userId, { tokens: allowed ? available - 1 : available, checked: now });
  return allowed;
}

function validateUpdate(state: Uint8Array, payload: Uint8Array) {
  const candidate = new Y.Doc();
  try {
    const text = candidate.getText('body');
    Y.applyUpdate(candidate, state);
    let update: Uint8Array | undefined;
    candidate.once('update', (bytes: Uint8Array) => { update = bytes; });
    Y.applyUpdate(candidate, payload);
    if (candidate.store.pendingStructs || candidate.store.pendingDs) return { error: 'MISSING_DEPENDENCY' };
    if (text.length > MAX_BODY) return { error: 'TEXT_LIMIT' };
    if (candidate.share.size !== 1 || text.toDelta().some((part: { insert: unknown; attributes?: unknown }) =>
      typeof part.insert !== 'string' || part.attributes !== undefined)) return { error: 'INVALID_UPDATE' };
    const next = Y.encodeStateAsUpdate(candidate);
    if (next.length > MAX_PAYLOAD) return { error: 'STATE_LIMIT' };
    return { next, update };
  } catch {
    return { error: 'INVALID_UPDATE' };
  } finally {
    candidate.destroy();
  }
}

export function createRooms(validSession: (token: string) => boolean, pruneSessions: () => void, options: StorageOptions) {
  const snapshotEvery = options.snapshotEvery ?? 100;
  if (!Number.isSafeInteger(snapshotEvery) || snapshotEvery < 1) throw new Error('Invalid snapshot interval');
  const storage = createStorage(options);
  const rooms = new Map<string, Room>();
  let available = false;

  function load() {
    for (const row of storage.load()) {
      rooms.set(row.id, {
        id: row.id, title: row.title, owner: row.owner, aclRevision: row.acl_revision, sequence: row.sequence,
        state: row.state, snapshotSequence: row.snapshotSequence,
        grants: new Map(row.grants.map((grant) => [grant.user_id, { role: grant.role, revision: grant.revision }])),
        updateBudget: new Map(), peers: new Set(), presenceClock: 0,
      });
    }
    available = true;
  }

  try { load(); } catch (error) { storage.close(); throw error; }
  const wss = new WebSocketServer({ noServer: true, maxPayload: MAX_FRAME, perMessageDeflate: false });

  function send(peer: Peer, frame: Uint8Array, written?: () => void) {
    if (!validSession(peer.token) || peer.socket.readyState !== WebSocket.OPEN) return;
    if (peer.socket.bufferedAmount > 2 * MAX_FRAME) peer.socket.terminate();
    else peer.socket.send(frame, written ? (error) => { if (!error) written(); } : undefined);
  }

  function broadcast(room: Room, frame: Uint8Array, except?: Peer) {
    for (const peer of room.peers) if (peer.clientId && peer !== except) send(peer, frame);
  }

  function presenceFrame(entries: PresenceEntry[]) {
    return encodeFrame({ type: 'AWARENESS' }, encodePresence(entries));
  }

  function removePeer(room: Room, peer: Peer) {
    if (!room.peers.delete(peer) || !peer.presence) return;
    const left = { id: peer.presence.id, clock: ++room.presenceClock, state: null };
    broadcast(room, presenceFrame([left]));
  }

  function disconnect(room: Room, peer: Peer, code: string) {
    removePeer(room, peer);
    // The close reason survives even when the session already expired.
    if (validSession(peer.token)) send(peer, encodeFrame({ type: 'ERROR', code }));
    peer.socket.close(1008, code);
  }

  function recoverStorage(error: unknown) {
    console.error('SQLite operation failed; reloading durable state', error);
    available = false;
    for (const room of rooms.values()) for (const peer of room.peers) disconnect(room, peer, 'STORAGE_RECOVERY');
    rooms.clear();
    try {
      storage.reopen();
      load();
    } catch (reloadError) {
      console.error('SQLite recovery failed', reloadError);
    }
  }

  function updatePresence(room: Room, peer: Peer, entry: PresenceEntry, payload: Uint8Array) {
    if (!entry.state) return;
    const cursor = readCursor(payload, entry.id);
    entry.state = { user: entry.state.user, ...(cursor ? { cursor } : {}) };
    entry.clock = ++room.presenceClock;
    broadcast(room, presenceFrame([entry]), peer);
  }

  function handleMessage(room: Room, peer: Peer, header: Record<string, unknown>, payload: Uint8Array) {
    if (!room.peers.has(peer)) return;
    if (!validSession(peer.token)) return disconnect(room, peer, 'SESSION_EXPIRED');
    const role = roleOf(room, peer.user.id);
    if (!role) return disconnect(room, peer, 'ACCESS_DENIED');
    const revision = grantRevision(room, peer.user.id);

    if (!peer.clientId) {
      if (header.type !== 'HELLO' || header.version !== VERSION || !isId(header.client_id) ||
          !isUint(header.awareness_id) || header.awareness_id > 0xffff_ffff || payload.length !== 0 ||
          [...room.peers].some(({ presence }) => presence?.id === header.awareness_id)) {
        return disconnect(room, peer, 'INVALID_HELLO');
      }
      const [color, colorLight] = COLORS[peer.user.id];
      const others = [...room.peers].flatMap(({ presence }) => presence ? [presence] : []);
      peer.clientId = header.client_id;
      peer.presence = {
        id: header.awareness_id,
        clock: ++room.presenceClock,
        state: { user: { ...peer.user, color, colorLight } },
      };
      send(peer, encodeFrame({ type: 'STATE', version: VERSION, role, acl_revision: room.aclRevision,
        grant_revision: revision, sequence: room.sequence }, room.state));
      send(peer, presenceFrame(others));
      broadcast(room, presenceFrame([peer.presence]), peer);
      return;
    }

    if (header.type === 'AWARENESS' && peer.presence) {
      updatePresence(room, peer, peer.presence, payload);
      return;
    }
    if (header.type !== 'UPDATE' || header.client_id !== peer.clientId || !isId(header.message_id)) {
      return disconnect(room, peer, 'INVALID_MESSAGE');
    }

    const reject = (code: string) => {
      const response: Message = { type: 'ERROR', code, message_id: header.message_id as string };
      send(peer, encodeFrame(response));
    };
    if (role === 'viewer') return reject('WRITE_FORBIDDEN');
    if (header.grant_revision !== revision) return reject('GRANT_CHANGED');
    if (payload.length > MAX_PAYLOAD) return reject('UPDATE_LIMIT');
    if (!takeUpdateToken(room, peer.user.id)) return reject('RATE_LIMIT');
    const operation = {
      document: room.id, user: peer.user.id, client: peer.clientId, message: header.message_id,
      hash: createHash('sha256').update(payload).digest('hex'),
    };
    const ack = (sequence: number) => send(peer, encodeFrame({
      type: 'ACK', client_id: operation.client, message_id: operation.message, sequence,
    }), options.checkpoint ? () => options.checkpoint?.('after-ack') : undefined);
    try {
      const existing = storage.receipt(operation);
      if (existing) {
        if (existing.hash !== operation.hash) return reject('MESSAGE_ID_CONFLICT');
        return ack(existing.sequence);
      }

      const verdict = validateUpdate(room.state, payload);
      if ('error' in verdict && verdict.error) return reject(verdict.error);
      if (verdict.update && verdict.update.byteLength > MAX_PAYLOAD) return reject('UPDATE_LIMIT');
      const sequence = verdict.update ? room.sequence + 1 : room.sequence;
      const frame = verdict.update && encodeFrame({ type: 'APPLIED', sequence }, verdict.update);
      storage.accept(operation, sequence, verdict.update);
      if (frame && verdict.next) {
        room.state = verdict.next;
        room.sequence = sequence;
        broadcast(room, frame);
      }
      ack(sequence);

      if (room.sequence - room.snapshotSequence >= snapshotEvery) {
        storage.snapshot(room.id, room.sequence, room.state);
        room.snapshotSequence = room.sequence;
      }
    } catch (error) {
      recoverStorage(error);
    }
  }

  const heartbeat = setInterval(() => {
    pruneSessions();
    for (const room of rooms.values()) {
      for (const peer of room.peers) {
        if (!validSession(peer.token)) disconnect(room, peer, 'SESSION_EXPIRED');
        else if (!peer.alive || !peer.clientId) {
          removePeer(room, peer);
          peer.socket.terminate();
        } else {
          peer.alive = false;
          peer.socket.ping();
        }
      }
    }
  }, HEARTBEAT_MS);
  heartbeat.unref();

  return {
    get available() { return available; },
    list(userId: string) {
      return [...rooms.values()].reverse().flatMap((room) => {
        const entry = summary(room, userId);
        return entry ? [entry] : [];
      });
    },
    create(user: User, title: string) {
      const room: Room = {
        id: randomUUID(), owner: user.id, title, grants: new Map(), updateBudget: new Map(), aclRevision: 0, sequence: 0,
        state: EMPTY_STATE, snapshotSequence: 0, peers: new Set(), presenceClock: 0,
      };
      try { storage.create(room.id, title, user.id); } catch (error) { recoverStorage(error); throw error; }
      rooms.set(room.id, room);
      return summary(room, user.id);
    },
    setAccess(documentId: string, owner: string, target: string, role: Access) {
      const room = rooms.get(documentId);
      if (!room || room.owner !== owner || target === owner) return null;
      if (roleOf(room, target) !== role) {
        const revision = grantRevision(room, target) + 1;
        try {
          storage.setGrant(room.id, target, role, revision, room.aclRevision + 1);
        } catch (error) {
          recoverStorage(error);
          throw error;
        }
        room.grants.set(target, { role, revision });
        room.aclRevision += 1;
        for (const peer of room.peers) if (peer.user.id === target) disconnect(room, peer, 'GRANT_CHANGED');
      }
      return summary(room, owner);
    },
    closeSession(token: string) {
      for (const room of rooms.values()) {
        for (const peer of room.peers) if (peer.token === token) disconnect(room, peer, 'SESSION_EXPIRED');
      }
    },
    upgrade(request: IncomingMessage, socket: Duplex, head: Buffer, documentId: string, token: string, user: User) {
      if (!available) return 503;
      const room = rooms.get(documentId);
      if (!room || !roleOf(room, user.id)) return 403;
      if (room.peers.size >= MAX_CONNECTIONS) return 409;
      wss.handleUpgrade(request, socket, head, (ws) => {
        const peer: Peer = { socket: ws, token, user, alive: true };
        room.peers.add(peer);
        ws.on('error', () => { removePeer(room, peer); ws.terminate(); });
        ws.on('close', () => removePeer(room, peer));
        ws.on('pong', () => { peer.alive = true; });
        ws.on('message', (data, isBinary) => {
          try {
            if (!isBinary || !Buffer.isBuffer(data)) throw new Error('Expected a binary frame');
            const { header, payload } = decodeFrame(data);
            handleMessage(room, peer, header, payload);
          } catch {
            disconnect(room, peer, 'INVALID_MESSAGE');
          }
        });
      });
      return undefined;
    },
    close() {
      clearInterval(heartbeat);
      for (const socket of wss.clients) socket.terminate();
      wss.close();
      storage.close();
    },
  };
}
