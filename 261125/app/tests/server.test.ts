import assert from 'node:assert/strict';
import { fork } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { on, once } from 'node:events';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
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
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness';
import { startApplication } from '../server/application.ts';
import { decodeFrame, encodeFrame, VERSION } from '../shared/protocol.ts';
import type { Access, Message } from '../shared/protocol.ts';
import type { Checkpoint } from '../server/storage.ts';

type Application = Pick<Awaited<ReturnType<typeof startApplication>>, 'origin' | 'logins'>;

async function setup(t: TestContext) {
  const dir = await mkdtemp(join(tmpdir(), 'collab-server-'));
  const databasePath = join(dir, 'collab.sqlite');
  const app = await startApplication({ databasePath });
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

async function connect(t: TestContext, app: Application, id: string, cookie: string, clientId = randomUUID()) {
  const ws = new WebSocket(wsUrl(app, id), { origin: app.origin, headers: { Cookie: cookie }, handshakeTimeout: 2_000 });
  const frames = on(ws, 'message', { signal: AbortSignal.timeout(5_000) });
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
    ws, doc, text, next, observed, initial: initial.header, clientId,
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
