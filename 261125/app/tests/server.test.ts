import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { on, once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { request } from 'node:http';
import { createConnection } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as pause } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { WebSocket } from 'ws';
import * as Y from 'yjs';
import * as encoding from 'lib0/encoding';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness';
import { startApplication } from '../server/application.ts';
import { decodeFrame, encodeFrame, MAX_BODY, MAX_FRAME, MAX_HEADER, MAX_PAYLOAD, VERSION } from '../shared/protocol.ts';
import type { Access, Message } from '../shared/protocol.ts';
import type { Checkpoint } from '../server/storage.ts';

type Application = Pick<Awaited<ReturnType<typeof startApplication>>, 'origin' | 'logins'>;

async function setup(t: TestContext, options: Pick<Parameters<typeof startApplication>[0], 'updateNow' | 'sessionNow'> = {}) {
  const dir = await mkdtemp(join(tmpdir(), 'collab-server-'));
  const databasePath = join(dir, 'collab.sqlite');
  const app = await startApplication({ databasePath, ...options });
  t.after(async () => {
    await app.close();
    await rm(dir, { recursive: true, force: true });
  });
  return { ...app, databasePath };
}

function http(app: Application, path: string, method = 'GET', cookie = '', body?: unknown, origin = app.origin) {
  const headers: Record<string, string> = { Origin: origin };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  return fetch(`${app.origin}/api/${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
}

async function login(app: Application, id: string) {
  const account = app.logins.find((login) => login.id === id)!;
  const response = await http(app, 'session', 'POST', '', { userId: id, password: account.password });
  assert.equal(response.status, 200);
  const cookie = response.headers.get('set-cookie')!;
  assert.match(cookie, /; Path=\/; HttpOnly; SameSite=Strict; Max-Age=3600$/);
  assert.deepEqual(await response.json(), { user: { id, name: account.name } });
  return cookie.split(';')[0];
}

async function createDocument(app: Application, cookie: string, title = '勉強会') {
  const response = await http(app, 'documents', 'POST', cookie, { title });
  assert.equal(response.status, 201);
  return (await response.json()).document.id as string;
}

function grant(app: Application, documentId: string, cookie: string, user: string, role: Access) {
  return http(app, `documents/${documentId}/access/${user}`, 'PUT', cookie, { role });
}

function wsUrl(app: Application, id: string) {
  return `${app.origin.replace('http:', 'ws:')}/ws/${id}`;
}

async function expectUpgradeDenied(app: Application, id: string, status: number, cookie = '', origin = app.origin) {
  const ws = new WebSocket(wsUrl(app, id), { origin, headers: { Cookie: cookie }, handshakeTimeout: 2_000 });
  const outcome = await new Promise<number>((resolve, reject) => {
    ws.once('unexpected-response', (_request, response) => {
      response.resume();
      resolve(response.statusCode ?? 0);
    });
    ws.once('error', reject);
    ws.once('open', () => {
      ws.terminate();
      reject(new Error('Unexpected WebSocket upgrade'));
    });
  });
  ws.terminate();
  assert.equal(outcome, status);
}

async function connect(t: TestContext, app: Application, id: string, cookie: string, clientId = randomUUID(), timeout = 30_000) {
  const ws = new WebSocket(wsUrl(app, id), { origin: app.origin, headers: { Cookie: cookie }, handshakeTimeout: 2_000 });
  const frames = on(ws, 'message', { signal: AbortSignal.timeout(timeout) });
  const doc = new Y.Doc();
  const text = doc.getText('body');
  t.after(async () => {
    ws.terminate();
    doc.destroy();
    await frames.return?.();
  });
  const observed: string[] = [];
  ws.on('message', (bytes: Buffer) => observed.push(String(decodeFrame(bytes).header.type)));
  await once(ws, 'open');
  ws.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: clientId, awareness_id: doc.clientID }));

  async function next(type: Message['type']) {
    for (;;) {
      const event = await frames.next();
      if (event.done) throw new Error('Connection ended');
      const frame = decodeFrame(event.value[0]);
      if (frame.header.type === type) return frame;
      if (frame.header.type !== 'AWARENESS' && frame.header.type !== 'ACK') {
        assert.fail(`Expected ${type}, received ${JSON.stringify(frame.header)}`);
      }
    }
  }

  const initial = await next('STATE');
  Y.applyUpdate(doc, initial.payload);
  const revision = Number(initial.header.grant_revision);
  return {
    ws, doc, text, next, observed, initial: initial.header, initialBytes: initial.payload, clientId,
    send(payload: Uint8Array, grantRevision = revision, messageId = randomUUID()) {
      ws.send(encodeFrame({ type: 'UPDATE', client_id: clientId, message_id: messageId, grant_revision: grantRevision }, payload));
      return messageId;
    },
    async applied(sequence: number) {
      const frame = await next('APPLIED');
      assert.equal(frame.header.sequence, sequence);
      Y.applyUpdate(doc, frame.payload);
    },
    async ack(messageId: string, sequence: number) {
      assert.deepEqual((await next('ACK')).header, { type: 'ACK', client_id: clientId, message_id: messageId, sequence });
    },
  };
}

function edit(doc: Y.Doc, change: (text: Y.Text) => void) {
  let bytes: Uint8Array | undefined;
  doc.once('update', (update: Uint8Array) => { bytes = update; });
  change(doc.getText('body'));
  assert.ok(bytes);
  return bytes;
}

test('HTTP requires authentication and same-Origin writes; only owners manage grants', async (t) => {
  const app = await setup(t);
  assert.equal((await http(app, 'documents')).status, 401);
  assert.equal((await http(app, 'session', 'POST', '', { userId: 'aki', password: '' })).status, 401);
  const owner = await login(app, 'aki');
  const editor = await login(app, 'ren');
  const viewer = await login(app, 'sora');
  assert.equal((await http(app, 'documents', 'POST', owner, { title: '不可' }, 'http://localhost')).status, 403);
  const documentId = await createDocument(app, owner, ' 文書 ');
  assert.equal((await grant(app, documentId, editor, 'sora', 'viewer')).status, 403);
  assert.equal((await grant(app, documentId, owner, 'aki', 'viewer')).status, 403);
  assert.deepEqual(await (await http(app, 'documents', 'GET', viewer)).json(), { documents: [] });
  assert.equal((await grant(app, documentId, owner, 'ren', 'editor')).status, 200);
  assert.equal((await grant(app, documentId, owner, 'sora', 'viewer')).status, 200);
  assert.deepEqual(await (await http(app, 'documents', 'GET', editor)).json(), {
    documents: [{ id: documentId, title: '文書', role: 'editor' }],
  });
  assert.equal((await http(app, 'session', 'DELETE', editor)).status, 200);
  assert.equal((await http(app, 'documents', 'GET', editor)).status, 401);
});

test('the WebSocket upgrade applies origin, session, document role and three-slot capacity', async (t) => {
  const app = await setup(t);
  const [owner, editor, viewer] = await Promise.all(['aki', 'ren', 'sora'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  await expectUpgradeDenied(app, id, 401);
  await expectUpgradeDenied(app, id, 403, owner, 'http://localhost');
  await expectUpgradeDenied(app, id, 403, editor);
  await expectUpgradeDenied(app, randomUUID(), 403, owner);
  assert.equal((await grant(app, id, owner, 'ren', 'editor')).status, 200);
  assert.equal((await grant(app, id, owner, 'sora', 'viewer')).status, 200);
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, editor);
  const c = await connect(t, app, id, viewer);
  assert.deepEqual([a.initial.role, b.initial.role, c.initial.role], ['owner', 'editor', 'viewer']);
  await expectUpgradeDenied(app, id, 409, viewer);
  c.ws.close();
  await once(c.ws, 'close');
  const replacement = await connect(t, app, id, viewer);
  assert.equal(replacement.initial.role, 'viewer');
});

test('accepted changes reach all peers and rejected updates leave body and sequence intact', async (t) => {
  const app = await setup(t);
  const [owner, editor, viewer] = await Promise.all(['aki', 'ren', 'sora'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  assert.equal((await grant(app, id, owner, 'ren', 'editor')).status, 200);
  assert.equal((await grant(app, id, owner, 'sora', 'viewer')).status, 200);
  const writer = await connect(t, app, id, owner);
  const collaborator = await connect(t, app, id, editor);
  const reader = await connect(t, app, id, viewer);

  writer.send(edit(writer.doc, (text) => text.insert(0, '共同編集')));
  await Promise.all([writer, collaborator, reader].map((peer) => peer.applied(1)));
  for (const peer of [writer, collaborator, reader]) assert.equal(peer.text.toString(), '共同編集');

  const rejected = new Y.Doc();
  t.after(() => rejected.destroy());
  Y.applyUpdate(rejected, Y.encodeStateAsUpdate(reader.doc));
  const idOfRejected = reader.send(edit(rejected, (text) => text.insert(text.length, ' 不可')));
  assert.deepEqual((await reader.next('ERROR')).header, { type: 'ERROR', code: 'WRITE_FORBIDDEN', message_id: idOfRejected });
  const malformed = writer.send(new Uint8Array([255]));
  assert.deepEqual((await writer.next('ERROR')).header, { type: 'ERROR', code: 'INVALID_UPDATE', message_id: malformed });
  const formatted = new Y.Doc();
  t.after(() => formatted.destroy());
  Y.applyUpdate(formatted, Y.encodeStateAsUpdate(writer.doc));
  const format = writer.send(edit(formatted, (text) => text.insert(0, '太字', { bold: true })));
  assert.deepEqual((await writer.next('ERROR')).header, { type: 'ERROR', code: 'INVALID_UPDATE', message_id: format });
  const dependent = writer.send(edit(rejected, (text) => text.insert(text.length, ' 後続')));
  assert.deepEqual((await writer.next('ERROR')).header, { type: 'ERROR', code: 'MISSING_DEPENDENCY', message_id: dependent });

  collaborator.send(edit(collaborator.doc, (text) => text.delete(0, 1)));
  await Promise.all([writer, collaborator, reader].map((peer) => peer.applied(2)));
  for (const peer of [writer, collaborator, reader]) assert.equal(peer.text.toString(), '同編集');
  reader.ws.close();
  await once(reader.ws, 'close');
  const rejoined = await connect(t, app, id, viewer);
  assert.equal(rejoined.initial.sequence, 2);
  assert.equal(rejoined.text.toString(), '同編集');
});

test('presence uses server account names and roles take effect on existing sockets', async (t) => {
  const app = await setup(t);
  const [owner, editor] = await Promise.all(['aki', 'ren'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  assert.equal((await grant(app, id, owner, 'ren', 'editor')).status, 200);
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, editor);
  const awareness = new Awareness(b.doc);
  const observed = new Awareness(a.doc);
  const applyPresence = async () => applyAwarenessUpdate(observed, (await a.next('AWARENESS')).payload, 'network');
  await applyPresence(); // Initial snapshot before ren joined.
  await applyPresence();
  assert.equal(observed.getStates().get(b.doc.clientID)?.user.name, 'レン');
  awareness.setLocalStateField('user', { name: '自称表示名' });
  b.ws.send(encodeFrame({ type: 'AWARENESS' }, encodeAwarenessUpdate(awareness, [b.doc.clientID])));
  await applyPresence();
  assert.equal(observed.getStates().get(b.doc.clientID)?.user.name, 'レン');

  const closed = once(b.ws, 'close');
  assert.equal((await grant(app, id, owner, 'ren', 'viewer')).status, 200);
  assert.deepEqual((await b.next('ERROR')).header, { type: 'ERROR', code: 'GRANT_CHANGED' });
  await closed;
  await applyPresence();
  assert.equal(observed.getStates().has(b.doc.clientID), false);
  const after = await connect(t, app, id, editor);
  assert.equal(after.initial.role, 'viewer');
  assert.equal(after.initial.grant_revision, 2);
});

async function fixtureDirectory(t: TestContext) {
  const directory = await mkdtemp(join(tmpdir(), 'collab-crash-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

function launch(t: TestContext, directory: string, point?: Checkpoint, mode = 'pause', interval = 100, port = 0) {
  const databasePath = join(directory, 'collab.sqlite');
  const child = fork(fileURLToPath(new URL('./storage-child.ts', import.meta.url)), [
    databasePath, String(port), directory, point ?? '', mode, String(interval),
  ], { stdio: ['ignore', 'ignore', 'inherit', 'ipc'], execArgv: [] });
  const ready = once(child, 'message', { signal: AbortSignal.timeout(8_000) }).then(([message]) => message as Application);
  const exited = once(child, 'exit');
  const stop = async () => { child.kill('SIGKILL'); return exited; };
  t.after(stop);
  async function reached() {
    for (let attempts = 0; attempts < 500; attempts += 1) {
      const current = await readFile(join(directory, 'reached'), 'utf8').catch((error: NodeJS.ErrnoException) => {
        if (error.code !== 'ENOENT') throw error;
      });
      if (current === point) return;
      await pause(10);
    }
    assert.fail(`Checkpoint not reached: ${point}`);
  }
  return { ready, stop, reached, resume: () => writeFile(join(directory, 'resume'), ''), databasePath };
}

function inspect(databasePath: string, id: string) {
  const db = new DatabaseSync(databasePath, { readOnly: true });
  try {
    return {
      sequence: db.prepare('SELECT sequence FROM documents WHERE id=?').get(id)?.sequence,
      snapshot: db.prepare('SELECT sequence FROM snapshots WHERE document_id=?').get(id)?.sequence,
      updates: db.prepare('SELECT sequence FROM updates WHERE document_id=? ORDER BY sequence').all(id).map((row) => row.sequence),
      receipts: db.prepare('SELECT COUNT(*) AS count FROM receipts WHERE document_id=?').get(id)?.count,
    };
  } finally {
    db.close();
  }
}

for (const point of ['before-commit', 'after-commit', 'after-ack'] as const) {
  test(`P2-1: SIGKILL at ${point} and restart the same database`, { timeout: 20_000 }, async (t) => {
    const directory = await fixtureDirectory(t);
    const running = launch(t, directory, point);
    const app = await running.ready;
    const owner = await login(app, 'aki');
    const id = await createDocument(app, owner, '停止試験');
    assert.equal((await grant(app, id, owner, 'sora', 'viewer')).status, 200);
    const writer = await connect(t, app, id, owner);
    const bytes = edit(writer.doc, (text) => text.insert(0, '永続化'));
    const operation = writer.send(bytes);
    await running.reached();
    if (point === 'after-ack') {
      await writer.applied(1);
      await writer.ack(operation, 1);
    } else {
      assert.equal(writer.observed.includes('APPLIED'), false);
      assert.equal(writer.observed.includes('ACK'), false);
    }
    assert.deepEqual(await running.stop(), [null, 'SIGKILL']);
    const persisted = point === 'before-commit' ? 0 : 1;
    assert.deepEqual(inspect(running.databasePath, id), {
      sequence: persisted, snapshot: undefined, updates: persisted ? [1] : [], receipts: persisted,
    });

    const restarted = await launch(t, directory).ready;
    const reader = await connect(t, restarted, id, await login(restarted, 'sora'));
    assert.equal(reader.initial.role, 'viewer');
    assert.equal(reader.initial.sequence, persisted);
    assert.equal(reader.text.toString(), persisted ? '永続化' : '');
    const again = await connect(t, restarted, id, await login(restarted, 'aki'), writer.clientId);
    again.send(bytes, 0, operation);
    if (!persisted) await again.applied(1);
    await again.ack(operation, 1);
    assert.equal(again.observed.filter((type) => type === 'APPLIED').length, persisted ? 0 : 1);
    assert.equal(inspect(running.databasePath, id).receipts, 1);
  });
}

test('P2-1: storage reload precedes listener startup and uncertain commit forces reload', { timeout: 20_000 }, async (t) => {
  const directory = await fixtureDirectory(t);
  const running = launch(t, directory, 'after-commit', 'throw');
  const app = await running.ready;
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const writer = await connect(t, app, id, owner);
  const committed = edit(writer.doc, (text) => text.insert(0, '確認前'));
  const operation = writer.send(committed);
  assert.equal((await writer.next('ERROR')).header.code, 'STORAGE_RECOVERY');
  assert.equal(writer.observed.includes('APPLIED'), false);
  assert.equal(writer.observed.includes('ACK'), false);
  const reloaded = await connect(t, app, id, owner, writer.clientId);
  assert.equal(reloaded.text.toString(), '確認前');
  assert.equal(reloaded.initial.sequence, 1);
  reloaded.send(committed, 0, operation);
  await reloaded.ack(operation, 1);
  assert.equal(reloaded.observed.includes('APPLIED'), false);

  await running.stop();
  await rm(join(directory, 'reached'));
  const port = Number(new URL(app.origin).port);
  const restarted = launch(t, directory, 'before-load', 'pause', 100, port);
  await restarted.reached();
  const probe = createConnection({ host: '127.0.0.1', port });
  try {
    await assert.rejects(once(probe, 'connect', { signal: AbortSignal.timeout(2_000) }), { code: 'ECONNREFUSED' });
  } finally { probe.destroy(); }
  await restarted.resume();
  const loaded = await restarted.ready;
  const peer = await connect(t, loaded, id, await login(loaded, 'aki'));
  assert.equal(peer.text.toString(), '確認前');
  assert.equal(peer.initial.sequence, 1);
});

test('P2-2: discard the ACK, replay the same bytes, and distinguish a no-op from an ID conflict', async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const writer = await connect(t, app, id, owner);
  const witness = await connect(t, app, id, owner);
  const empty = writer.send(Y.encodeStateAsUpdate(writer.doc));
  await writer.ack(empty, 0);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 0, snapshot: undefined, updates: [], receipts: 1 });
  const bytes = edit(writer.doc, (text) => text.insert(0, '確認'));
  const first = writer.send(bytes);
  await writer.applied(1);
  await witness.applied(1);
  // Consume the first ACK as lost at the client; replay the original operation.
  await writer.next('ACK');
  writer.send(bytes, 0, first);
  await writer.ack(first, 1);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 2 });

  const different = edit(writer.doc, (text) => text.insert(0, '拒否'));
  writer.send(different, 0, first);
  assert.equal((await writer.next('ERROR')).header.code, 'MESSAGE_ID_CONFLICT');
  const noChange = writer.send(bytes);
  await writer.ack(noChange, 1);
  writer.send(bytes, 0, noChange);
  await writer.ack(noChange, 1);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 3 });
  assert.equal(witness.text.toString(), '確認');
  assert.equal(witness.observed.filter((type) => type === 'APPLIED').length, 1);
});

test('P2-4: a snapshot covers a fixed prefix while an update is waiting on the socket', { timeout: 20_000 }, async (t) => {
  const directory = await fixtureDirectory(t);
  const running = launch(t, directory, 'snapshot-written', 'pause', 2);
  const app = await running.ready;
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const writer = await connect(t, app, id, owner);

  const first = edit(writer.doc, (text) => text.insert(0, 'A'));
  const firstId = writer.send(first);
  await writer.applied(1);
  await writer.ack(firstId, 1);
  const second = writer.send(edit(writer.doc, (text) => text.insert(1, 'B')));
  await running.reached();
  const third = writer.send(edit(writer.doc, (text) => text.insert(2, 'C')));
  // SQLite readers still see the old log until snapshot and pruning commit together.
  assert.deepEqual(inspect(running.databasePath, id), { sequence: 2, snapshot: undefined, updates: [1, 2], receipts: 2 });
  await running.resume();
  await writer.applied(2);
  await writer.ack(second, 2);
  await writer.applied(3);
  await writer.ack(third, 3);
  assert.deepEqual(inspect(running.databasePath, id), { sequence: 3, snapshot: 2, updates: [3], receipts: 3 });

  await running.stop();
  const next = await launch(t, directory).ready;
  const recovered = await connect(t, next, id, await login(next, 'aki'), writer.clientId);
  assert.equal(recovered.text.toString(), 'ABC');
  assert.equal(recovered.initial.sequence, 3);
  recovered.send(first, 0, firstId);
  await recovered.ack(firstId, 1);
  assert.equal(recovered.observed.includes('APPLIED'), false);
  assert.deepEqual(inspect(running.databasePath, id), { sequence: 3, snapshot: 2, updates: [3], receipts: 3 });
});

type Connection = Awaited<ReturnType<typeof connect>>;

function sent(peer: Connection, type: Message['type']) {
  return peer.observed.filter((observed) => observed === type).length;
}

async function expectRejection(peer: Connection, code: string, messageId: string) {
  assert.deepEqual((await peer.next('ERROR')).header, { type: 'ERROR', code, message_id: messageId });
}

test('P3-1: the in-flight commit precedes revocation, and the stale grant stays rejected after restart', { timeout: 20_000 }, async (t) => {
  const directory = await fixtureDirectory(t);
  const running = launch(t, directory, 'before-commit');
  const app = await running.ready;
  const [owner, editor, viewer] = await Promise.all(['aki', 'ren', 'sora'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  await grant(app, id, owner, 'ren', 'editor');
  await grant(app, id, owner, 'sora', 'viewer');
  const writer = await connect(t, app, id, editor);
  const witness = await connect(t, app, id, viewer);
  const firstBytes = edit(writer.doc, (text) => text.insert(0, '受理済み'));
  const first = writer.send(firstBytes);
  await running.reached();
  assert.deepEqual(inspect(running.databasePath, id), { sequence: 0, snapshot: undefined, updates: [], receipts: 0 });
  const closed = once(writer.ws, 'close');
  const httpChange = request(`${app.origin}/api/documents/${id}/access/ren`, {
    method: 'PUT', headers: { Origin: app.origin, Cookie: owner, 'Content-Type': 'application/json' },
  });
  const revoking = once(httpChange, 'response').then(([response]) => { response.resume(); return response.statusCode; });
  await new Promise<void>((resolve) => httpChange.end(JSON.stringify({ role: 'viewer' }), resolve));
  await running.resume();
  await writer.applied(1);
  await writer.ack(first, 1);
  await witness.applied(1);
  assert.equal(await revoking, 200);
  assert.equal((await writer.next('ERROR')).header.code, 'GRANT_CHANGED');
  assert.equal((await closed)[1].toString(), 'GRANT_CHANGED');

  const refused = edit(writer.doc, (text) => text.insert(text.length, '旧版'));
  const oldId = randomUUID();
  const reading = await connect(t, app, id, editor, writer.clientId);
  const accepted = { sequence: 1, snapshot: undefined, updates: [1], receipts: 1 };
  assert.equal(reading.initial.grant_revision, 2);
  assert.equal(reading.initial.role, 'viewer');
  assert.equal(reading.text.toString(), '受理済み');
  await expectRejection(reading, 'GRANT_CHANGED', reading.send(refused, 1, oldId));
  await expectRejection(reading, 'GRANT_CHANGED', reading.send(firstBytes, 1, first));
  await expectRejection(reading, 'WRITE_FORBIDDEN', reading.send(refused, 2, oldId));
  assert.deepEqual(inspect(running.databasePath, id), accepted);
  assert.equal(sent(witness, 'APPLIED'), 1);
  assert.equal(sent(reading, 'APPLIED'), 0);
  assert.equal(sent(reading, 'ACK'), 0);

  await running.stop();
  const restarted = await launch(t, directory).ready;
  const [newOwner, newEditor] = await Promise.all(['aki', 'ren'].map((name) => login(restarted, name)));
  const readAfterRestart = await connect(t, restarted, id, newEditor, writer.clientId);
  assert.equal(readAfterRestart.initial.grant_revision, 2);
  assert.equal(readAfterRestart.text.toString(), '受理済み');
  await expectRejection(readAfterRestart, 'GRANT_CHANGED', readAfterRestart.send(refused, 1, oldId));
  const closedAgain = once(readAfterRestart.ws, 'close');
  assert.equal((await grant(restarted, id, newOwner, 'ren', null)).status, 200);
  await closedAgain;
  await expectUpgradeDenied(restarted, id, 403, newEditor);
  assert.equal((await grant(restarted, id, newOwner, 'ren', 'editor')).status, 200);
  const regranted = await connect(t, restarted, id, newEditor, writer.clientId);
  assert.equal(regranted.initial.grant_revision, 4);
  await expectRejection(regranted, 'GRANT_CHANGED', regranted.send(refused, 1, oldId));
  await expectRejection(regranted, 'GRANT_CHANGED', regranted.send(refused, 3, oldId));
  assert.deepEqual(inspect(running.databasePath, id), accepted);
  assert.equal(sent(regranted, 'APPLIED'), 0);
  assert.equal(sent(regranted, 'ACK'), 0);
  const complete = await connect(t, restarted, id, newOwner);
  assert.equal(complete.text.toString(), '受理済み');
  assert.equal(complete.initial.sequence, 1);
});

test('P3-2: a durable tombstone rejects old sockets and permits a deletion reason only for readers at deletion', async (t) => {
  const app = await setup(t);
  const [owner, editor, stranger] = await Promise.all(['aki', 'ren', 'sora'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  await grant(app, id, owner, 'ren', 'editor');
  // The stranger once had access but it ended before deletion.
  await grant(app, id, owner, 'sora', 'viewer');
  await grant(app, id, owner, 'sora', null);
  const concealed = await http(app, 'documents/' + id, 'GET', stranger);
  assert.equal(concealed.status, 403);
  assert.deepEqual(await concealed.json(), { error: 'ACCESS_DENIED' });
  const remove = (cookie: string, origin?: string) => http(app, 'documents/' + id, 'DELETE', cookie, undefined, origin);
  assert.equal((await remove(editor)).status, 403);
  assert.equal((await remove(owner, 'http://localhost')).status, 403);
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, editor);
  const first = a.send(edit(a.doc, (text) => text.insert(0, '削除前')));
  await Promise.all([a.applied(1), b.applied(1)]);
  await a.ack(first, 1);
  const original = inspect(app.databasePath, id);
  const local = edit(b.doc, (text) => text.insert(text.length, '編集中'));
  const disconnected = Promise.all([once(a.ws, 'close'), once(b.ws, 'close')]);
  assert.deepEqual(await (await remove(owner)).json(), { deleted: true });
  // A late send on the old socket cannot be accepted even around the close handshake.
  b.send(local);
  for (const socket of [a, b]) assert.equal((await socket.next('ERROR')).header.code, 'DOCUMENT_DELETED');
  for (const [, reason] of await disconnected) assert.equal(reason.toString(), 'DOCUMENT_DELETED');
  assert.equal(sent(b, 'ACK'), 0);
  assert.equal(sent(a, 'APPLIED'), 1);
  assert.equal(sent(b, 'APPLIED'), 1);
  assert.deepEqual(inspect(app.databasePath, id), original);
  assert.equal((await grant(app, id, owner, 'ren', 'editor')).status, 403);
  assert.equal((await remove(owner)).status, 403);
  assert.deepEqual(await (await http(app, 'documents', 'GET', owner)).json(), { documents: [] });

  async function verify(application: Application, before: string, denied: string) {
    const unknown = randomUUID();
    const readDeleted = await http(application, 'documents/' + id, 'GET', before);
    assert.equal(readDeleted.status, 410);
    assert.deepEqual(await readDeleted.json(), { error: 'DOCUMENT_DELETED' });
    for (const candidate of [id, unknown]) {
      const read = await http(application, 'documents/' + candidate, 'GET', denied);
      assert.equal(read.status, 403);
      assert.deepEqual(await read.json(), { error: 'ACCESS_DENIED' });
      await expectUpgradeDenied(application, candidate, 403, denied);
    }
    await expectUpgradeDenied(application, id, 410, before);
  }

  await verify(app, editor, stranger);
  const replacementId = await createDocument(app, owner);
  assert.notEqual(replacementId, id);
  const db = new DatabaseSync(app.databasePath, { readOnly: true });
  try {
    assert.equal(db.prepare('SELECT status FROM documents WHERE id=?').get(id)?.status, 'deleted');
  } finally { db.close(); }
  await app.close();
  const restarted = await startApplication({ databasePath: app.databasePath });
  t.after(() => restarted.close());
  await verify(restarted, await login(restarted, 'ren'), await login(restarted, 'sora'));
});

async function assertState(t: TestContext, app: Application, id: string, reader: string, sequence: number, expected: string) {
  const peer = await connect(t, app, id, reader);
  assert.equal(peer.initial.sequence, sequence);
  assert.equal(peer.text.toString(), expected);
  peer.ws.close();
  await once(peer.ws, 'close');
}

test('P3-3: body bounds count UTF-16 units and rejection changes neither history nor delivery', async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, owner);
  const before = '🙂'.repeat((MAX_BODY - 2) / 2) + 'A';
  const near = a.send(edit(a.doc, (text) => text.insert(0, before)));
  await Promise.all([a.applied(1), b.applied(1)]);
  await a.ack(near, 1);
  assert.equal(b.text.length, MAX_BODY - 1);
  const exact = a.send(edit(a.doc, (text) => text.insert(text.length, 'B')));
  await Promise.all([a.applied(2), b.applied(2)]);
  await a.ack(exact, 2);
  assert.equal(b.text.length, MAX_BODY);
  await expectRejection(a, 'TEXT_LIMIT', a.send(edit(a.doc, (text) => text.insert(text.length, 'C'))));
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 2, snapshot: undefined, updates: [1, 2], receipts: 2 });
  await assertState(t, app, id, owner, 2, before + 'B');
  assert.equal(sent(b, 'APPLIED'), 2);
  assert.equal(sent(a, 'ACK'), 2);
});

function paddedFrame(header: Message, payload: Uint8Array, headerSize: number) {
  const base = JSON.stringify({ ...header, padding: '' });
  assert.ok(base.length + 2 <= headerSize);
  const json = Buffer.from(JSON.stringify({ ...header, padding: ' '.repeat(headerSize - 2 - base.length) }));
  const prefix = Buffer.alloc(2);
  prefix.writeUInt16BE(json.length);
  return Buffer.concat([prefix, json, payload]);
}

// V1 encodes each redundant delete range explicitly. The deletes address already deleted text.
function redundantDeletes(client: number, size: number) {
  const newHeader = (count: number) => {
    const encoder = encoding.createEncoder();
    for (const value of [0, 1, client, count]) encoding.writeVarUint(encoder, value);
    return encoding.toUint8Array(encoder);
  };
  const count = Math.floor((size - newHeader(Math.floor(size / 2)).length) / 2);
  const header = newHeader(count);
  const result = Buffer.alloc(size, 1);
  result.set(header);
  let pos = header.length;
  for (let index = 0; index < count; index += 1) {
    if (index === 0 && (size - header.length) % 2 === 1) {
      result[pos++] = 128;
      result[pos++] = 1;
    } else result[pos++] = 0;
    pos++;
  }
  assert.equal(pos, size);
  return result;
}

test('P3-3: independently bound UPDATE, JSON header and complete WebSocket message sizes', { timeout: 35_000 }, async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const a = await connect(t, app, id, owner);
  const witness = await connect(t, app, id, owner);
  a.doc.transact(() => { a.text.insert(0, 'a'.repeat(129)); a.text.delete(0, 129); });
  const seed = a.send(Y.encodeStateAsUpdate(a.doc));
  await Promise.all([a.applied(1), witness.applied(1)]);
  await a.ack(seed, 1);
  const update = (messageId: string): Message => ({ type: 'UPDATE', client_id: a.clientId, message_id: messageId, grant_revision: 0 });
  for (const [size, headerSize] of [[MAX_PAYLOAD - 1, MAX_HEADER], [MAX_PAYLOAD, MAX_HEADER - 1], [MAX_PAYLOAD, MAX_HEADER]]) {
    const id = randomUUID();
    const bytes = redundantDeletes(a.doc.clientID, size);
    const frame = paddedFrame(update(id), bytes, headerSize);
    assert.equal(frame.length, size === MAX_PAYLOAD && headerSize === MAX_HEADER ? MAX_FRAME : MAX_FRAME - 1);
    a.ws.send(frame);
    await a.ack(id, 1);
  }

  const tooBig = randomUUID();
  const over = redundantDeletes(a.doc.clientID, MAX_PAYLOAD + 1);
  a.ws.send(paddedFrame(update(tooBig), over, MAX_HEADER - 1)); // Total still fits.
  await expectRejection(a, 'UPDATE_LIMIT', tooBig);
  const closes = once(a.ws, 'close');
  a.ws.send(paddedFrame(update(randomUUID()), new Uint8Array([0, 0]), MAX_HEADER + 1));
  assert.equal((await a.next('ERROR')).header.code, 'FRAME_LIMIT');
  assert.equal((await closes)[0], 1009);

  const other = await connect(t, app, id, owner, a.clientId);
  const tooLong = once(other.ws, 'close');
  const large = paddedFrame(update(randomUUID()), over, MAX_HEADER);
  assert.equal(large.length, MAX_FRAME + 1);
  other.ws.send(large);
  assert.equal((await tooLong)[0], 1009);
  assert.equal(sent(other, 'ACK'), 0);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 4 });
  assert.equal(sent(a, 'ACK'), 4);
  assert.equal(sent(witness, 'APPLIED'), 1);
  assert.ok(witness.initialBytes.length < 100);
  await assertState(t, app, id, owner, 1, '');
});

test('P3-3: complete Yjs state can hit the limit with a much smaller final UPDATE', { timeout: 75_000 }, async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const a = await connect(t, app, id, owner, randomUUID(), 70_000);
  const source = new Y.Doc();
  t.after(() => source.destroy());
  source.clientID = 0x7000_0000;
  const first = edit(source, (text) => text.insert(0, 'A'));
  const seed = a.send(first);
  await a.applied(1);
  await a.ack(seed, 1);

  // A distinct GC-only client contributes 17 bytes, plus four bytes for the two widened group counts.
  const near = MAX_PAYLOAD - 1;
  const initialSize = Y.encodeStateAsUpdate(source).length;
  const available = near - initialSize - 4;
  const remainder = available % 17;
  const clients = Math.floor(available / 17) - remainder % 2;
  const extra = available - clients * 17;
  const gc = new Y.Doc();
  try {
    for (let index = 0; index < clients; index += 1) {
      const client = 0x2000_0000 + index;
      const length = index < extra / 2 ? 128 : 1;
      gc.store.clients.set(client, [new Y.GC(Y.createID(client, 0), length)]);
    }
    const state = Y.encodeStateAsUpdate(gc);
    assert.ok(state.length < MAX_PAYLOAD);
    const bulk = a.send(state);
    // Observe receipt and delivery without retaining a second copy of the synthetic GC history.
    assert.equal((await a.next('APPLIED')).header.sequence, 2);
    await a.ack(bulk, 2);
  } finally { gc.destroy(); gc.store.clients.clear(); }

  const verifySize = async (sequence: number, size: number) => {
    const ws = new WebSocket(wsUrl(app, id), { origin: app.origin, headers: { Cookie: owner } });
    await once(ws, 'open');
    const message = once(ws, 'message');
    ws.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: randomUUID(), awareness_id: 17 }));
    const frame = decodeFrame((await message)[0]);
    assert.equal(frame.header.type, 'STATE');
    assert.equal(frame.header.sequence, sequence);
    assert.equal(frame.payload.length, size);
    const closing = once(ws, 'close');
    ws.close();
    await closing;
    return createHash('sha256').update(frame.payload).digest('hex');
  };

  await verifySize(2, near);
  const b = await connect(t, app, id, owner);
  const tiny = edit(source, (text) => text.insert(text.length, 'B'));
  assert.ok(tiny.length < 100);
  assert.equal(Y.encodeStateAsUpdate(source).length, initialSize + 1);
  const exact = a.send(tiny);
  assert.equal((await a.next('APPLIED')).header.sequence, 3);
  await b.applied(3);
  await a.ack(exact, 3);
  const before = await verifySize(3, MAX_PAYLOAD);
  const excess = edit(source, (text) => text.insert(text.length, 'C'));
  assert.ok(excess.length < 100);
  assert.equal(Y.encodeStateAsUpdate(source).length, initialSize + 2);
  await expectRejection(a, 'STATE_LIMIT', a.send(excess));
  assert.equal(await verifySize(3, MAX_PAYLOAD), before);
  assert.equal(b.text.toString(), 'AB');
  assert.equal(sent(b, 'APPLIED'), 1);
  assert.equal(sent(a, 'ACK'), 3);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 3, snapshot: undefined, updates: [1, 2, 3], receipts: 3 });
});

test('P3-3: the per-user bucket shares 20 burst tokens across sockets and refills at ten per second', async (t) => {
  let time = 0;
  const app = await setup(t, { updateNow: () => time });
  const [owner, viewer] = await Promise.all(['aki', 'sora'].map((id) => login(app, id)));
  const id = await createDocument(app, owner);
  await grant(app, id, owner, 'sora', 'viewer');
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, owner);
  const witness = await connect(t, app, id, viewer);
  const empty = Y.encodeStateAsUpdate(a.doc);
  for (let index = 0; index < 20; index += 1) {
    const writer = index % 2 === 0 ? a : b;
    await writer.ack(writer.send(empty), 0);
    if (index >= 18) assert.equal(inspect(app.databasePath, id).receipts, index + 1);
  }
  const change = edit(a.doc, (text) => text.insert(0, '再送'));
  const retry = a.send(change);
  await expectRejection(a, 'RATE_LIMIT', retry);
  time = 99;
  await expectRejection(a, 'RATE_LIMIT', a.send(change, 0, retry));
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 0, snapshot: undefined, updates: [], receipts: 20 });
  assert.equal(sent(witness, 'APPLIED'), 0);
  assert.equal(a.ws.readyState, WebSocket.OPEN);
  time = 100;
  a.send(change, 0, retry);
  await Promise.all([a.applied(1), b.applied(1), witness.applied(1)]);
  await a.ack(retry, 1);
  for (let index = 0; index < 9; index += 1) {
    time += 100;
    await a.ack(a.send(empty), 1);
  }
  await expectRejection(b, 'RATE_LIMIT', b.send(empty));
  assert.equal(witness.text.toString(), '再送');
  assert.equal(sent(witness, 'APPLIED'), 1);
  assert.equal(sent(a, 'ACK') + sent(b, 'ACK'), 30);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 30 });
});

test('P3-3: the third connection is permitted, the fourth rejected, and a closed slot can be reused', async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const a = await connect(t, app, id, owner); // 1
  const b = await connect(t, app, id, owner); // 2
  assert.equal(b.initial.sequence, 0);
  const c = await connect(t, app, id, owner); // 3
  assert.equal(c.initial.sequence, 0);
  await expectUpgradeDenied(app, id, 409, owner); // 4
  b.ws.close();
  await once(b.ws, 'close');
  const replacement = await connect(t, app, id, owner);
  assert.equal(replacement.text.toString(), '');
  assert.equal(replacement.initial.sequence, 0);
  assert.equal(sent(a, 'APPLIED') + sent(c, 'APPLIED'), 0);
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 0, snapshot: undefined, updates: [], receipts: 0 });
});

test('P3-3: invalid and dependent updates are atomic, and resending the bundled dependency succeeds', async (t) => {
  const app = await setup(t);
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  const a = await connect(t, app, id, owner);
  const b = await connect(t, app, id, owner);
  const first = edit(a.doc, (text) => text.insert(0, 'A'));
  const second = edit(a.doc, (text) => text.insert(1, 'B'));
  const removal = edit(a.doc, (text) => text.delete(0, 1));
  const styled = new Y.Doc();
  t.after(() => styled.destroy());
  const metadata = edit(styled, (text) => text.setAttribute('title', 'test'));
  await expectRejection(a, 'INVALID_UPDATE', a.send(new Uint8Array([255])));
  await expectRejection(a, 'INVALID_UPDATE', a.send(new Uint8Array([0, 0, 0])));
  await expectRejection(a, 'INVALID_UPDATE', a.send(metadata));
  await expectRejection(a, 'MISSING_DEPENDENCY', a.send(second));
  await expectRejection(a, 'MISSING_DEPENDENCY', a.send(removal));
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 0, snapshot: undefined, updates: [], receipts: 0 });
  assert.equal(sent(a, 'ACK'), 0);
  assert.equal(sent(b, 'APPLIED'), 0);
  await assertState(t, app, id, owner, 0, '');

  const bundle = a.send(Y.mergeUpdates([first, second, removal]));
  await Promise.all([a.applied(1), b.applied(1)]);
  await a.ack(bundle, 1);
  assert.equal(b.text.toString(), 'B');
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 1 });
});

test('P3-3: expiration rejects an existing socket and fresh upgrades until the account signs in again', async (t) => {
  let time = 0;
  const app = await setup(t, { sessionNow: () => time });
  const owner = await login(app, 'aki');
  const id = await createDocument(app, owner);
  await grant(app, id, owner, 'sora', 'viewer');
  const a = await connect(t, app, id, owner);
  time = 3_600_000 - 1;
  const witnessCookie = await login(app, 'sora');
  const witness = await connect(t, app, id, witnessCookie);
  const seed = a.send(edit(a.doc, (text) => text.insert(0, '有効')));
  await Promise.all([a.applied(1), witness.applied(1)]);
  await a.ack(seed, 1);

  time += 1;
  const closed = once(a.ws, 'close');
  const before = edit(a.doc, (text) => text.insert(text.length, '期限後'));
  const messageId = a.send(before);
  assert.equal((await closed)[1].toString(), 'SESSION_EXPIRED');
  assert.equal((await http(app, 'documents/' + id, 'GET', owner)).status, 401);
  await expectUpgradeDenied(app, id, 401, owner);
  await expectUpgradeDenied(app, randomUUID(), 403, witnessCookie);
  const noAccess = await http(app, 'documents/' + randomUUID(), 'GET', witnessCookie);
  assert.equal(noAccess.status, 403);
  assert.deepEqual(await noAccess.json(), { error: 'ACCESS_DENIED' });
  assert.deepEqual(inspect(app.databasePath, id), { sequence: 1, snapshot: undefined, updates: [1], receipts: 1 });
  assert.equal(sent(a, 'ACK'), 1);
  assert.equal(sent(witness, 'APPLIED'), 1);
  assert.equal(witness.text.toString(), '有効');

  const fresh = await connect(t, app, id, await login(app, 'aki'), a.clientId);
  assert.equal(fresh.text.toString(), '有効');
  assert.equal(fresh.initial.sequence, 1);
  fresh.send(before, 0, messageId);
  await Promise.all([fresh.applied(2), witness.applied(2)]);
  await fresh.ack(messageId, 2);
  assert.equal(witness.text.toString(), '有効期限後');
});
