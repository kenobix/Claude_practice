import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { on, once } from 'node:events';
import { test } from 'node:test';
import type { TestContext } from 'node:test';
import { WebSocket } from 'ws';
import * as Y from 'yjs';
import { Awareness, applyAwarenessUpdate, encodeAwarenessUpdate } from 'y-protocols/awareness';
import { startApplication } from '../server/application.ts';
import { decodeFrame, encodeFrame, VERSION } from '../shared/protocol.ts';
import type { Access, Message } from '../shared/protocol.ts';

type Application = Awaited<ReturnType<typeof startApplication>>;

async function setup(t: TestContext) {
  const app = await startApplication();
  t.after(() => app.close());
  return app;
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

async function connect(t: TestContext, app: Application, id: string, cookie: string) {
  const ws = new WebSocket(wsUrl(app, id), { origin: app.origin, headers: { Cookie: cookie }, handshakeTimeout: 2_000 });
  const frames = on(ws, 'message', { signal: AbortSignal.timeout(5_000) });
  const doc = new Y.Doc();
  const text = doc.getText('body');
  t.after(async () => {
    ws.terminate();
    doc.destroy();
    await frames.return?.();
  });
  const clientId = randomUUID();
  await once(ws, 'open');
  ws.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: clientId, awareness_id: doc.clientID }));

  async function next(type: Message['type']) {
    for (;;) {
      const event = await frames.next();
      if (event.done) throw new Error('Connection ended');
      const frame = decodeFrame(event.value[0]);
      if (frame.header.type === type) return frame;
      if (frame.header.type !== 'AWARENESS') assert.fail(`Expected ${type}, received ${JSON.stringify(frame.header)}`);
    }
  }

  const initial = await next('STATE');
  Y.applyUpdate(doc, initial.payload);
  const revision = Number(initial.header.grant_revision);
  return {
    ws, doc, text, next, initial: initial.header,
    send(payload: Uint8Array, grantRevision = revision) {
      const messageId = randomUUID();
      ws.send(encodeFrame({ type: 'UPDATE', client_id: clientId, message_id: messageId, grant_revision: grantRevision }, payload));
      return messageId;
    },
    async applied(sequence: number) {
      const frame = await next('APPLIED');
      assert.equal(frame.header.sequence, sequence);
      Y.applyUpdate(doc, frame.payload);
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
