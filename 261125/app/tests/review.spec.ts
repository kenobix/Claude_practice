import { createHash, randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { readFile } from 'node:fs/promises';
import { DatabaseSync } from 'node:sqlite';
import { expect, test } from '@playwright/test';
import type { Page, WebSocketRoute } from '@playwright/test';
import * as Y from 'yjs';
import { decodeFrame, encodeFrame } from '../shared/protocol.ts';
import { append, browserFixture, connection, createDocument, grant, moveToEnd, saveStatus, sequence, watchWire } from '../tools/browser.ts';
import { probe } from '../tools/probe.ts';
import { body, content, openDocument, signIn } from './browser-helpers.ts';

const title = '再レビュー用の文書';

function editState(state: Uint8Array, change: (body: Y.Text) => void) {
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, state);
    change(doc.getText('body'));
    return Y.encodeStateAsUpdate(doc);
  } finally { doc.destroy(); }
}

function textOf(state: Uint8Array) {
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, state);
    return doc.getText('body').toString();
  } finally { doc.destroy(); }
}

function history(path: string, id: string) {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    return {
      sequence: db.prepare('SELECT sequence FROM documents WHERE id=?').get(id)?.sequence,
      updates: db.prepare('SELECT sequence, hex(payload) AS payload FROM updates WHERE document_id=? ORDER BY sequence').all(id),
      receipts: db.prepare('SELECT sequence, message_id, hash FROM receipts WHERE document_id=? ORDER BY message_id').all(id),
      snapshots: db.prepare('SELECT sequence, hex(state) AS state FROM snapshots WHERE document_id=?').all(id),
    };
  } finally { db.close(); }
}

async function expectDocument(page: Page, expected: string, seq: number) {
  await expect(sequence(page)).toHaveText(String(seq));
  await expect.poll(() => body(page)).toBe(expected);
}

async function seedDraft(page: Page, scope: string, pending: Uint8Array, state?: Uint8Array, blocked?: string) {
  const messageId = randomUUID();
  await page.evaluate(async ({ scope, messageId, pending, state, blocked }) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('collab-ren');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const tx = db.transaction(['states', 'outbox'], 'readwrite');
      if (state || blocked) {
        tx.objectStore('states').put({ scope, bytes: Uint8Array.from(state ?? [0, 0]), unconfirmed: Number(Boolean(state)), blocked });
      }
      else tx.objectStore('states').delete(scope);
      tx.objectStore('outbox').add({ scope, messageId, bytes: Uint8Array.from(pending) });
      await new Promise<void>((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onabort = () => reject(tx.error);
      });
    } finally { db.close(); }
  }, { scope, messageId, pending: [...pending], state: state && [...state], blocked });
  return { messageId, bytes: [...pending] };
}

function loadDraftRecords(page: Page, scope: string) {
  return page.evaluate(async (scope) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('collab-ren');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const tx = db.transaction(['states', 'outbox']);
      const state = tx.objectStore('states').get(scope);
      const outbox = tx.objectStore('outbox').index('scope').get(scope);
      await new Promise<void>((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onabort = () => reject(tx.error);
      });
      return {
        blocked: state.result?.blocked,
        base: state.result?.draftBase && Array.from<number>(state.result.draftBase),
        cached: state.result?.bytes && Array.from<number>(state.result.bytes),
        pending: outbox.result && { messageId: outbox.result.messageId, bytes: Array.from<number>(outbox.result.bytes) },
      };
    } finally { db.close(); }
  }, scope);
}

function appendDelta(state: Uint8Array, addition: string) {
  const doc = new Y.Doc();
  try {
    Y.applyUpdate(doc, state);
    const vector = Y.encodeStateVector(doc);
    const body = doc.getText('body');
    body.insert(body.length, addition);
    return Y.encodeStateAsUpdate(doc, vector);
  } finally { doc.destroy(); }
}

function draftExport(page: Page, label: string) {
  return page.getByRole('region', { name: '同期を停止した下書き' })
    .getByRole('listitem').filter({ hasText: label }).getByRole('button', { name: 'テキストを書き出す' });
}

async function downloadDraft(page: Page, label: string) {
  const download = page.waitForEvent('download');
  await draftExport(page, label).click();
  const file = await download;
  return { text: await readFile((await file.path())!, 'utf8'), name: file.suggestedFilename() };
}

test('review High: direct CRLF and CR updates are rejected without changing history or delivery', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const owner = await f.profile('owner');
    const editor = await f.profile('editor');
    const observed: number[] = [];
    await watchWire(editor.page, ({ direction, header }) => {
      if (direction === 'received' && header.type === 'APPLIED') observed.push(Number(header.sequence));
    });
    await signIn(owner.page, app.origin, app.logins[0]);
    await signIn(editor.page, app.origin, app.logins[1]);
    const id = await createDocument(owner.page, title);
    await grant(owner.page, 'レン', 'editor');
    await openDocument(editor.page, title);
    await expectDocument(editor.page, '', 0);
    await append(owner.page, 'a\nb');
    await expectDocument(editor.page, 'a\nb', 1);
    const before = history(f.databasePath, id);
    const clientId = randomUUID();
    for (const line of ['c\r\nd', 'c\rd']) {
      await probe(owner.context, app.origin, id, clientId, async (socket, next) => {
        const { header, payload } = await next('STATE');
        expect(header.sequence).toBe(1);
        expect(textOf(payload)).toBe('a\nb');
        const messageId = randomUUID();
        socket.send(encodeFrame({ type: 'UPDATE', client_id: clientId, message_id: messageId, grant_revision: 0 },
          editState(payload, (body) => body.insert(body.length, line))));
        expect((await next('ERROR')).header).toMatchObject({ code: 'LINE_ENDING', message_id: messageId });
      });
      expect(history(f.databasePath, id)).toEqual(before);
    }
    // A valid edit also gives the receiving socket a deterministic boundary for detecting stray APPLIED frames.
    await append(editor.page, 'X');
    await expectDocument(owner.page, 'a\nbX', 2);
    await expectDocument(editor.page, 'a\nbX', 2);
    expect(observed).toEqual([1, 2]);
    await owner.page.reload();
    await openDocument(owner.page, title);
    await expectDocument(owner.page, 'a\nbX', 2);
  } finally { await f.close(); }
});

test('review High: browser input and paste normalize CRLF, CR and LF before positional edits and recovery', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const owner = await f.profile('owner');
    const editor = await f.profile('editor');
    const { page: a } = owner;
    const { page: b } = editor;
    const tap = await watchWire(b);
    await signIn(a, app.origin, app.logins[0]);
    await signIn(b, app.origin, app.logins[1]);
    const id = await createDocument(a, title);
    await grant(a, 'レン', 'editor');
    await openDocument(b, title);

    await append(a, 'a\r\nb\rc\nd');
    await expectDocument(b, 'a\nb\nc\nd', 1);
    await owner.context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: app.origin });
    await moveToEnd(a);
    await a.evaluate(() => navigator.clipboard.writeText('\r\ne\rf\ng'));
    await a.keyboard.press('Control+v');
    await expectDocument(b, 'a\nb\nc\nd\ne\nf\ng', 2);

    await content(b).focus();
    await b.keyboard.press('Control+Home');
    await b.keyboard.press('ArrowDown');
    await b.keyboard.insertText('I');
    await b.keyboard.press('Delete');
    await append(b, 'X');
    const expected = 'a\nI\nc\nd\ne\nf\ngX';
    for (const page of [a, b]) await expectDocument(page, expected, 5);
    await expect(saveStatus(b)).toHaveText('サーバー保存済み');

    tap.cut();
    await expect(connection(b)).toHaveText('未接続');
    await b.getByRole('button', { name: '接続し直す' }).click();
    await expect(connection(b)).toHaveText('接続中');
    await expectDocument(b, expected, 5);
    await b.reload();
    await openDocument(b, title);
    await expectDocument(b, expected, 5);

    await app.close();
    const restarted = await f.start({ port: Number(new URL(app.origin).port) });
    for (const [index, page] of [a, b].entries()) {
      await signIn(page, restarted.origin, restarted.logins[index]);
      await openDocument(page, title);
      await expectDocument(page, expected, 5);
    }
    await probe(owner.context, restarted.origin, id, randomUUID(), async (_socket, next) => {
      expect(textOf((await next('STATE')).payload)).toBe(expected);
    });
  } finally { await f.close(); }
});

test('review High: an existing SQLite document with CR is exportable without editing or rewriting it', async () => {
  const f = await browserFixture();
  try {
    const original = await f.start();
    const owner = await f.profile('owner');
    const reader = await f.profile('reader');
    await signIn(owner.page, original.origin, original.logins[0]);
    const id = await createDocument(owner.page, title);
    await grant(owner.page, 'レン', 'viewer');
    await original.close();
    const legacyText = 'a\r\nb\rc';
    const payload = editState(Uint8Array.of(0, 0), (body) => body.insert(0, legacyText));
    const db = new DatabaseSync(f.databasePath);
    try {
      // Seed the rows produced by the previous implementation, including the receipt.
      db.exec('BEGIN');
      db.prepare('INSERT INTO updates VALUES (?,1,?)').run(id, payload);
      db.prepare('UPDATE documents SET sequence=1 WHERE id=?').run(id);
      db.prepare('INSERT INTO receipts VALUES (?,?,?,?,?,1,?)').run(
        id, 'aki', randomUUID(), randomUUID(), createHash('sha256').update(payload).digest('hex'), Date.now());
      db.exec('COMMIT');
    } finally { db.close(); }
    const before = history(f.databasePath, id);
    const restarted = await f.start({ port: Number(new URL(original.origin).port) });
    await signIn(owner.page, restarted.origin, restarted.logins[0]);
    await signIn(reader.page, restarted.origin, restarted.logins[1]);
    const outsider = await f.profile('outsider');
    await signIn(outsider.page, restarted.origin, restarted.logins[2]);
    expect((await outsider.page.request.get(`${restarted.origin}/api/documents/${id}`)).status()).toBe(403);
    await probe(owner.context, restarted.origin, id, randomUUID(), async (socket, next) => {
      const closed = once(socket, 'close', { signal: AbortSignal.timeout(5_000) });
      const { header, payload: saved } = await next('LEGACY_STATE');
      expect(header.sequence).toBe(1);
      expect(textOf(saved)).toBe(legacyText);
      expect(await closed).toEqual([1000, Buffer.alloc(0)]);
    });

    for (const page of [owner.page, reader.page]) {
      await openDocument(page, title);
      await expect(sequence(page)).toHaveText('1');
      await expect(content(page)).toHaveCount(0);
      const download = page.waitForEvent('download');
      await page.getByRole('button', { name: '保存済みの本文を書き出す' }).click();
      expect(await readFile((await (await download).path())!, 'utf8')).toBe(legacyText);
    }
    expect(history(f.databasePath, id)).toEqual(before);
    await createDocument(owner.page, '復旧用の新しい文書');
    await append(owner.page, legacyText);
    await expectDocument(owner.page, 'a\nb\nc', 1);
    expect(history(f.databasePath, id)).toEqual(before);
  } finally { await f.close(); }
});

for (const mode of ['state-only', 'outbox-only']) {
  test(`review High: a legacy IndexedDB ${mode} draft preserves its original text without replay`, async () => {
    const f = await browserFixture();
    try {
      const app = await f.start();
      const owner = await f.profile('owner');
      const editor = await f.profile('editor');
      const { page: a } = owner;
      const { page: b } = editor;
      const writes: string[] = [];
      await watchWire(b, ({ direction, header }) => {
        if (direction === 'sent' && header.type === 'UPDATE') writes.push(String(header.message_id));
      });
      await signIn(a, app.origin, app.logins[0]);
      await signIn(b, app.origin, app.logins[1]);
      const id = await createDocument(a, title);
      await grant(a, 'レン', 'editor');
      await openDocument(b, title);
      await expectDocument(b, '', 0);
      await b.reload(); // Finish the previous room before seeding the cache.
      await expect(b.getByRole('button', { name: 'ログアウト' })).toBeEnabled();

      const oldText = 'draft\r\nline\r';
      const bytes = editState(Uint8Array.of(0, 0), (body) => body.insert(0, oldText));
      const scope = `${id}:1`;
      const messageId = randomUUID();
      await b.evaluate(async ({ bytes, scope, messageId, mode }) => {
        const db = await new Promise<IDBDatabase>((resolve, reject) => {
          const request = indexedDB.open('collab-ren');
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
        try {
          const tx = db.transaction(['states', 'outbox'], 'readwrite');
          const update = Uint8Array.from(bytes);
          if (mode === 'state-only') tx.objectStore('states').put({ scope, bytes: update, unconfirmed: 0 });
          else tx.objectStore('outbox').add({ scope, messageId, bytes: update });
          await new Promise<void>((resolve, reject) => {
            tx.oncomplete = () => resolve();
            tx.onabort = () => reject(tx.error);
          });
        } finally { db.close(); }
      }, { bytes: Array.from(bytes), scope, messageId, mode });
      const before = history(f.databasePath, id);
      await openDocument(b, title);
      await expectDocument(b, '', 0);
      await expect(content(b)).toHaveAttribute('contenteditable', 'false');
      await expect(saveStatus(b)).toHaveAttribute('data-server-save', 'blocked');
      const download = b.waitForEvent('download');
      await b.getByRole('region', { name: '同期を停止した下書き' }).getByRole('button', { name: 'テキストを書き出す' }).click();
      expect(await readFile((await (await download).path())!, 'utf8')).toBe(oldText);
      await expectDocument(a, '', 0);
      expect(history(f.databasePath, id)).toEqual(before);
      expect(writes).toEqual([]);
    } finally { await f.close(); }
  });
}

for (const mode of ['legacy-outbox', 'lf-outbox', 'lf-already-quarantined', 'lf-state-and-outbox']) {
  test(`review Medium: export recovers a dependent ${mode} draft without uploading it`, async () => {
    const f = await browserFixture();
    try {
      const first = await f.start();
      const owner = await f.profile('owner');
      const editor = await f.profile('editor');
      const { page: a } = owner;
      const { page: b } = editor;
      const writes: unknown[] = [];
      const broadcasts: unknown[] = [];
      await watchWire(b, ({ direction, header }) => {
        if (direction === 'sent' && header.type === 'UPDATE') writes.push(header.message_id);
      });
      await watchWire(a, ({ direction, header }) => {
        if (direction === 'received' && header.type === 'APPLIED') broadcasts.push(header.sequence);
      });
      await signIn(a, first.origin, first.logins[0]);
      await signIn(b, first.origin, first.logins[1]);
      const id = await createDocument(a, title);
      await grant(a, 'レン', 'editor');
      const legacy = mode === 'legacy-outbox';
      const completeCache = mode === 'lf-state-and-outbox';
      const base = editState(Uint8Array.of(0, 0), (body) => body.insert(0, legacy ? 'base\r\nlast' : 'base'));
      const official = completeCache ? editState(base, (body) => body.insert(0, 'server:')) : base;
      const delta = appendDelta(base, legacy ? ' draft' : '\r\ndraft');
      const expected = legacy ? 'base\r\nlast draft' : 'base\r\ndraft';
      expect(textOf(delta)).toBe('');

      await first.close();
      const db = new DatabaseSync(f.databasePath);
      try {
        db.exec('BEGIN');
        db.prepare('INSERT INTO updates VALUES (?,1,?)').run(id, official);
        db.prepare('UPDATE documents SET sequence=1 WHERE id=?').run(id);
        db.prepare('INSERT INTO receipts VALUES (?,?,?,?,?,1,?)').run(
          id, 'aki', randomUUID(), randomUUID(), createHash('sha256').update(official).digest('hex'), Date.now());
        db.exec('COMMIT');
      } finally { db.close(); }
      const before = history(f.databasePath, id);
      const app = await f.start({ port: Number(new URL(first.origin).port) });
      await signIn(a, app.origin, app.logins[0]);
      await signIn(b, app.origin, app.logins[1]);
      const scope = `${id}:1`;
      const original = await seedDraft(b, scope, delta, completeCache ? base : undefined,
        mode === 'lf-already-quarantined' ? 'LINE_ENDING' : undefined);
      await openDocument(a, title);
      if (legacy) await expect(sequence(a)).toHaveText('1');
      else await expectDocument(a, textOf(official), 1);
      await openDocument(b, title);
      if (legacy) await expect(content(b)).toHaveCount(0);
      else {
        await expectDocument(b, textOf(official), 1);
        await expect(content(b)).toHaveAttribute('contenteditable', 'false');
      }
      await expect(saveStatus(b)).toHaveAttribute('data-server-save', 'blocked');
      const filename = `draft-${id}-1.txt`;
      expect(await downloadDraft(b, title)).toEqual({ text: expected, name: filename });
      expect(await loadDraftRecords(b, scope)).toEqual({
        blocked: legacy ? 'LEGACY_LINE_ENDING' : 'LINE_ENDING', base: [...official],
        cached: completeCache ? [...base] : [0, 0], pending: original,
      });

      await b.reload();
      // The local export also works before the user opens the document again.
      expect(await downloadDraft(b, title)).toEqual({ text: expected, name: filename });
      await probe(owner.context, app.origin, id, randomUUID(), async (_socket, next) => {
        const frame = await next(legacy ? 'LEGACY_STATE' : 'STATE');
        expect(frame.header.sequence).toBe(1);
        expect(textOf(frame.payload)).toBe(textOf(official));
      });
      expect(history(f.databasePath, id)).toEqual(before);
      expect(writes).toEqual([]);
      expect(broadcasts).toEqual([]);
    } finally { await f.close(); }
  });
}

test('review Medium: an old grant cannot use another document or a newer grant for draft recovery', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const owner = await f.profile('owner');
    const editor = await f.profile('editor');
    const { page: a } = owner;
    const { page: b } = editor;
    const requests: string[] = [];
    const sockets: string[] = [];
    const downloads: string[] = [];
    await signIn(a, app.origin, app.logins[0]);
    await signIn(b, app.origin, app.logins[1]);
    const oldTitle = '権限を失った文書';
    const id = await createDocument(a, oldTitle);
    await grant(a, 'レン', 'editor');
    await append(a, 'old base');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    let base!: Uint8Array;
    await probe(owner.context, app.origin, id, randomUUID(), async (_socket, next) => {
      base = (await next('STATE')).payload;
    });
    await grant(a, 'レン', '');
    await append(a, ' owner only');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await expect(b.getByRole('button', { name: 'ログアウト' })).toBeEnabled();
    const scope = `${id}:1`;
    const pending = await seedDraft(b, scope, appendDelta(base, '\r\nold draft'));

    const otherTitle = '別の文書';
    const otherId = await createDocument(a, otherTitle);
    await grant(a, 'レン', 'editor');
    await append(a, 'other base');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    let otherBase!: Uint8Array;
    await probe(owner.context, app.origin, otherId, randomUUID(), async (_socket, next) => {
      otherBase = (await next('STATE')).payload;
    });
    await seedDraft(b, `${otherId}:1`, appendDelta(otherBase, '\r\nother draft'));
    await b.reload();
    await openDocument(b, otherTitle);
    expect((await downloadDraft(b, otherTitle)).text).toBe('other base\r\nother draft');

    b.on('request', (request) => requests.push(request.url()));
    b.on('websocket', (socket) => sockets.push(socket.url()));
    b.on('download', (file) => downloads.push(file.suggestedFilename()));
    await draftExport(b, id).click();
    await expect(b.locator('.app-error')).toContainText('下書きは書き出せませんでした');
    expect(requests).toEqual([]);
    expect(sockets).toEqual([]);
    expect(downloads).toEqual([]);
    expect(await loadDraftRecords(b, scope)).toEqual({ blocked: 'ACCESS_DENIED', base: undefined, cached: [0, 0], pending });
    expect((await b.request.get(`${app.origin}/api/documents/${id}`)).status()).toBe(403);

    await openDocument(a, oldTitle);
    await grant(a, 'レン', 'editor');
    await b.reload();
    await openDocument(b, oldTitle);
    await expectDocument(b, 'old base owner only', 2);
    await draftExport(b, oldTitle).click();
    await expect(b.locator('.app-error')).toContainText('下書きは書き出せませんでした');
    expect(downloads).toEqual([]);
    expect(await loadDraftRecords(b, scope)).toEqual({ blocked: 'ACCESS_DENIED', base: undefined, cached: [0, 0], pending });

    await b.getByRole('button', { name: 'ログアウト' }).click();
    await expect(b.getByRole('button', { name: 'ログイン', exact: true })).toBeVisible();
    await signIn(b, app.origin, app.logins[2]);
    await expect(b.getByRole('button', { name: 'ログアウト' })).toBeEnabled();
    await expect(b.getByRole('region', { name: '同期を停止した下書き' })).toHaveCount(0);
  } finally { await f.close(); }
});

test('review Medium: export labels a partial draft and keeps its unresolved delta', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const { page } = await f.profile('editor');
    await signIn(page, app.origin, app.logins[1]);
    await expect(page.getByRole('button', { name: 'ログアウト' })).toBeEnabled();
    const id = randomUUID();
    const scope = `${id}:1`;
    const cached = editState(Uint8Array.of(0, 0), (body) => body.insert(0, 'local fragment\r\n'));
    const missing = editState(Uint8Array.of(0, 0), (body) => body.insert(0, 'missing predecessor'));
    const pending = await seedDraft(page, scope, appendDelta(missing, ' unresolved'), cached);
    await page.reload();
    expect(await downloadDraft(page, id)).toEqual({ text: 'local fragment\r\n', name: `draft-${id}-1-partial.txt` });
    await expect(page.locator('.app-error')).toContainText('下書きの一部を復元できませんでした');
    expect(await loadDraftRecords(page, scope)).toEqual({
      blocked: 'ACCESS_DENIED', base: undefined, cached: [...cached], pending,
    });
  } finally { await f.close(); }
});

test('review Medium: silent disconnect exposes reconnect and preserves the unacknowledged operation', async () => {
  test.setTimeout(30_000);
  const f = await browserFixture();
  try {
    const app = await f.start();
    const owner = await f.profile('owner');
    const editor = await f.profile('editor');
    const { page: a } = owner;
    const { page: b } = editor;
    const applied: number[] = [];
    await watchWire(a, ({ direction, header }) => {
      if (direction === 'received' && header.type === 'APPLIED') applied.push(Number(header.sequence));
    });
    const attempts: { connection: number; frame: Buffer }[] = [];
    let joins = 0;
    let oldAck: Buffer | undefined;
    let oldBrowser: WebSocketRoute | undefined;
    let releaseNewAck: (() => void) | undefined;

    await b.routeWebSocket('**/ws/**', (browser) => {
      const server = browser.connectToServer();
      const number = ++joins;
      let severed = false;
      browser.onMessage((frame) => {
        if (typeof frame !== 'string' && decodeFrame(frame).header.type === 'UPDATE') attempts.push({ connection: number, frame });
        if (!severed) server.send(frame);
      });
      server.onMessage((frame) => {
        if (typeof frame !== 'string' && decodeFrame(frame).header.type === 'ACK') {
          if (number === 1 && !severed) {
            oldAck = frame;
            oldBrowser = browser;
            severed = true;
            server.close();
            return;
          }
          if (number === 2 && !releaseNewAck) {
            releaseNewAck = () => browser.send(frame);
            return;
          }
        }
        if (!severed) browser.send(frame);
      });
      server.onClose((code, reason) => { if (!severed) browser.close({ code, reason }); });
      browser.onClose(() => { if (!severed) server.close(); });
    });

    await signIn(a, app.origin, app.logins[0]);
    await signIn(b, app.origin, app.logins[1]);
    const id = await createDocument(a, title);
    await grant(a, 'レン', 'editor');
    await openDocument(b, title);
    await expect(connection(b)).toHaveText('接続中');
    await append(b, 'レン');
    await expectDocument(a, 'レン', 1);
    await expect.poll(() => Boolean(oldAck)).toBe(true);
    await append(a, 'アキ');
    await expectDocument(a, 'レンアキ', 2);
    await content(b).focus();
    await b.keyboard.press('Control+Home');
    await b.keyboard.insertText('下書き');
    await expectDocument(b, '下書きレン', 1);
    const committed = history(f.databasePath, id);
    await expect(connection(b)).toHaveText('未接続', { timeout: 12_000 });
    await expect(b.getByRole('alert')).toContainText('サーバーから保存確認が届きません');
    await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
    await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');
    expect(attempts.filter(({ connection }) => connection === 1)).toHaveLength(3);

    // A response on the stopped connection must not clear the outbox.
    await oldBrowser!.send(oldAck!);
    await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
    await b.getByRole('button', { name: '接続し直す' }).click();
    await expect.poll(() => Boolean(releaseNewAck)).toBe(true);
    expect(joins).toBe(2);
    expect(attempts.find(({ connection }) => connection === 2)?.frame).toEqual(attempts[0].frame);
    await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
    releaseNewAck!();
    await expect(saveStatus(b)).toHaveText('サーバー保存済み');
    await expectDocument(a, '下書きレンアキ', 3);
    await expectDocument(b, '下書きレンアキ', 3);
    const final = history(f.databasePath, id);
    expect(final.receipts).toHaveLength(3);
    expect(final.receipts).toEqual(expect.arrayContaining(committed.receipts));
    expect(final.updates.slice(0, 2)).toEqual(committed.updates);
    expect(final.updates.map(({ sequence }) => sequence)).toEqual([1, 2, 3]);
    expect(applied).toEqual([1, 2, 3]);
  } finally { await f.close(); }
});
