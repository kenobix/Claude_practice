import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect, test } from '@playwright/test';
import type { BrowserContext, Page, WebSocketRoute } from '@playwright/test';
import * as Y from 'yjs';
import { startApplication } from '../server/application.ts';
import { decodeFrame } from '../shared/protocol.ts';
import { body, content, openDocument, signIn } from './browser-helpers.ts';

const title = '復帰試験';

async function fixture() {
  const dir = await mkdtemp(join(tmpdir(), 'collab-browser-recovery-'));
  const databasePath = join(dir, 'collab.sqlite');
  const contexts = new Set<BrowserContext>();
  const servers = new Set<Awaited<ReturnType<typeof startApplication>>>();
  return {
    async start(port = 0) {
      const app = await startApplication({ databasePath, port });
      servers.add(app);
      return app;
    },
    async profile(id: string) {
      const context = await chromium.launchPersistentContext(join(dir, id));
      contexts.add(context);
      return { context, page: context.pages()[0] };
    },
    async close() {
      await Promise.allSettled([...contexts].map((context) => context.close()));
      await Promise.allSettled([...servers].map((app) => app.close()));
      await rm(dir, { recursive: true, force: true });
    },
  };
}

async function watch(page: Page) {
  let wire: { browser: WebSocketRoute; server: WebSocketRoute } | undefined;
  const updates: { id: string; bytes: Buffer }[] = [];
  const acks = new Map<string, Buffer>();
  let holdAck = false;
  await page.routeWebSocket('**/ws/**', (browser) => {
    const server = browser.connectToServer();
    wire = { browser, server };
    browser.onMessage((message) => {
      if (typeof message !== 'string') {
        const { header, payload } = decodeFrame(message);
        if (header.type === 'UPDATE') updates.push({ id: String(header.message_id), bytes: Buffer.from(payload) });
      }
      server.send(message);
    });
    server.onMessage((message) => {
      if (holdAck && typeof message !== 'string') {
        const { header } = decodeFrame(message);
        if (header.type === 'ACK') { acks.set(String(header.message_id), message); return; }
      }
      browser.send(message);
    });
  });
  return {
    updates, acks,
    holdAcks() { holdAck = true; },
    release(id: string) { wire!.browser.send(acks.get(id)!); acks.delete(id); },
    cut() {
      wire!.server.close();
      wire!.browser.close();
    },
  };
}

async function create(page: Page) {
  await page.getByLabel('新しい文書').fill(title);
  await page.getByRole('button', { name: '作成', exact: true }).click();
  await expect(page.locator('[data-connection]')).toHaveText('接続中');
}

async function append(page: Page, value: string) {
  await content(page).focus();
  await page.keyboard.press('End');
  await page.keyboard.insertText(value);
}

function saveStatus(page: Page) { return page.locator('[data-server-save]'); }

async function cacheContents(page: Page, user: string) {
  return page.evaluate(async (name) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(name);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const tx = db.transaction(['states', 'outbox']);
      function read(store: string) {
        return new Promise<Record<string, unknown>[]>((resolve, reject) => {
          const request = tx.objectStore(store).getAll();
          request.onsuccess = () => resolve(request.result);
          request.onerror = () => reject(request.error);
        });
      }
      const [states, outbox] = await Promise.all([read('states'), read('outbox')]);
      return { states: states.length, ids: outbox.map((item) => item.messageId as string) };
    } finally { db.close(); }
  }, `collab-${user}`);
}

async function keepPartial(page: Page, mode: 'state-only' | 'outbox-only' | 'missing-predecessor') {
  await page.evaluate(async (mode) => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open('collab-aki');
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    try {
      const tx = db.transaction(['states', 'outbox'], 'readwrite', { durability: 'strict' });
      if (mode === 'state-only') tx.objectStore('outbox').clear();
      else if (mode === 'outbox-only') tx.objectStore('states').clear();
      else {
        const first = tx.objectStore('outbox').openCursor();
        first.onsuccess = () => first.result!.delete();
      }
      await new Promise<void>((resolve, reject) => {
        tx.oncomplete = () => resolve();
        tx.onabort = () => reject(tx.error);
      });
    } finally { db.close(); }
  }, mode);
}

test('P2-2: losing an ACK retries the original bytes and leaves the later edit pending', async () => {
  const f = await fixture();
  try {
    const app = await f.start();
    const { page } = await f.profile('aki');
    const tap = await watch(page);
    await signIn(page, app.origin, app.logins[0]);
    await create(page);
    await expect(saveStatus(page)).toHaveAttribute('data-server-save', 'idle');
    tap.holdAcks();
    await append(page, 'A');
    await append(page, 'B');
    await expect.poll(async () => (await cacheContents(page, 'aki')).ids.length).toBe(2);
    const [first, second] = (await cacheContents(page, 'aki')).ids;
    await expect.poll(() => tap.updates.filter(({ id }) => id === first).length, { timeout: 8_000 }).toBeGreaterThanOrEqual(2);
    expect(tap.updates[0].bytes).toEqual(tap.updates[1].bytes);
    await expect(page.getByLabel('受理順番号')).toHaveText('1');
    await expect(page.locator('[data-local-save]')).toHaveText('端末内保存済み');
    await expect(saveStatus(page)).toHaveText('サーバー保存待ち');
    tap.release(first);
    await expect.poll(async () => (await cacheContents(page, 'aki')).ids).toEqual([second]);
    await expect.poll(() => tap.acks.has(second)).toBe(true);
    await expect(saveStatus(page)).toHaveText('サーバー保存待ち');
    tap.release(second);
    await expect(saveStatus(page)).toHaveText('サーバー保存済み');
    await expect(page.getByLabel('受理順番号')).toHaveText('2');
    expect(await body(page)).toBe('AB');
    expect((await cacheContents(page, 'aki')).ids).toEqual([]);
  } finally { await f.close(); }
});

test('P2-3: isolated profiles edit across disconnect, browser restart, and server restart', async () => {
  const f = await fixture();
  try {
    const app = await f.start();
    const a = await f.profile('aki');
    let b = await f.profile('ren');
    const tap = await watch(b.page);
    await signIn(a.page, app.origin, app.logins[0]);
    await signIn(b.page, app.origin, app.logins[1]);
    await create(a.page);
    await a.page.getByLabel('レンの権限').selectOption('editor');
    await expect(a.page.getByLabel('レンの権限')).toHaveValue('editor');
    await openDocument(b.page, title);
    await append(a.page, 'abcd');
    await expect(saveStatus(a.page)).toHaveText('サーバー保存済み');
    await expect.poll(() => body(b.page)).toBe('abcd');

    tap.cut();
    await expect(b.page.locator('[data-connection]')).toHaveText('未接続');
    await expect(content(b.page)).toHaveAttribute('contenteditable', 'true');
    await append(a.page, 'X');
    await expect(saveStatus(a.page)).toHaveText('サーバー保存済み');
    await content(b.page).focus();
    await b.page.keyboard.press('Home');
    await b.page.keyboard.press('ArrowRight');
    await b.page.keyboard.press('Delete');
    await expect.poll(async () => (await cacheContents(b.page, 'ren')).ids.length).toBe(1);
    await expect(saveStatus(b.page)).toHaveText('サーバー保存待ち');
    expect(await body(b.page)).toBe('acd');
    const [original] = (await cacheContents(b.page, 'ren')).ids;

    await b.context.close();
    b = await f.profile('ren');
    const recovered = await watch(b.page);
    await b.page.goto(app.origin);
    await openDocument(b.page, title);
    await expect(saveStatus(b.page)).toHaveText('サーバー保存済み');
    expect(recovered.updates.map(({ id }) => id)).toEqual([original]);
    await expect.poll(() => body(a.page)).toBe('acdX');
    expect(await body(b.page)).toBe('acdX');
    await expect(a.page.getByLabel('受理順番号')).toHaveText('3');
    await expect(b.page.getByLabel('受理順番号')).toHaveText('3');

    await app.close();
    await expect(b.page.locator('[data-connection]')).toHaveText('未接続');
    await append(b.page, 'Z');
    await expect.poll(async () => (await cacheContents(b.page, 'ren')).ids.length).toBe(1);
    const restarted = await f.start(Number(new URL(app.origin).port));
    await signIn(b.page, restarted.origin, restarted.logins[1]);
    await openDocument(b.page, title);
    await expect(saveStatus(b.page)).toHaveText('サーバー保存済み');
    await expect(b.page.getByLabel('受理順番号')).toHaveText('4');
    expect(await body(b.page)).toBe('acdXZ');
  } finally { await f.close(); }
});

test('P2-3: an already applied state-only edit waits for the new no-op ACK', async () => {
  const f = await fixture();
  try {
    const app = await f.start();
    let profile = await f.profile('aki');
    const first = await watch(profile.page);
    await signIn(profile.page, app.origin, app.logins[0]);
    await create(profile.page);
    first.holdAcks();
    await append(profile.page, 'A');
    await expect(profile.page.getByLabel('受理順番号')).toHaveText('1');
    await expect.poll(() => first.acks.size).toBe(1);
    const original = first.updates[0].id;
    first.cut();
    await expect(profile.page.locator('[data-connection]')).toHaveText('未接続');
    await keepPartial(profile.page, 'state-only');
    await profile.context.close();

    profile = await f.profile('aki');
    const second = await watch(profile.page);
    second.holdAcks();
    await profile.page.goto(app.origin);
    await openDocument(profile.page, title);
    await expect.poll(() => second.acks.size).toBe(1);
    await expect(saveStatus(profile.page)).toHaveText('サーバー保存待ち');
    const [replacement] = second.acks.keys();
    expect(replacement).not.toBe(original);
    second.release(replacement);
    await expect(saveStatus(profile.page)).toHaveText('サーバー保存済み');
    await expect(profile.page.getByLabel('受理順番号')).toHaveText('1');
    expect(await body(profile.page)).toBe('A');
  } finally { await f.close(); }
});

for (const mode of ['state-only', 'outbox-only', 'missing-predecessor'] as const) {
  test(`P2-3: recover from IndexedDB ${mode}`, async () => {
    const f = await fixture();
    try {
      const app = await f.start();
      let profile = await f.profile('aki');
      const tap = await watch(profile.page);
      await signIn(profile.page, app.origin, app.logins[0]);
      await create(profile.page);
      await append(profile.page, 'abc');
      await expect(saveStatus(profile.page)).toHaveText('サーバー保存済み');
      tap.cut();
      await expect(profile.page.locator('[data-connection]')).toHaveText('未接続');

      if (mode === 'state-only') {
        await content(profile.page).focus();
        await profile.page.keyboard.press('Home');
        await profile.page.keyboard.press('ArrowRight');
        await profile.page.keyboard.press('Delete');
      } else {
        await append(profile.page, 'X');
        await append(profile.page, 'Y');
      }
      const expectedCount = mode === 'state-only' ? 1 : 2;
      await expect.poll(async () => (await cacheContents(profile.page, 'aki')).ids.length).toBe(expectedCount);
      const original = (await cacheContents(profile.page, 'aki')).ids;
      await keepPartial(profile.page, mode);
      expect(await cacheContents(profile.page, 'aki')).toEqual({
        states: mode === 'outbox-only' ? 0 : 1,
        ids: mode === 'state-only' ? [] : mode === 'outbox-only' ? original : original.slice(1),
      });
      await profile.context.close();
      profile = await f.profile('aki');
      const recovered = await watch(profile.page);
      await profile.page.goto(app.origin);
      await openDocument(profile.page, title);
      await expect(saveStatus(profile.page)).toHaveText('サーバー保存済み');
      expect((await cacheContents(profile.page, 'aki')).ids).toEqual([]);
      if (mode === 'state-only') {
        expect(await body(profile.page)).toBe('ac');
        expect(recovered.updates).toHaveLength(1);
        expect(recovered.updates[0].id).not.toBe(original[0]);
        const decoded = Y.decodeUpdate(recovered.updates[0].bytes);
        expect(decoded.structs).toHaveLength(0);
        expect(decoded.ds.clients.size).toBeGreaterThan(0);
      } else {
        expect(await body(profile.page)).toBe('abcXY');
        expect(recovered.updates).toHaveLength(2);
        expect(recovered.updates[1].id).toBe(original[1]);
        if (mode === 'outbox-only') expect(recovered.updates[0].id).toBe(original[0]);
        else expect(recovered.updates[0].id).not.toBe(original[0]);
      }
      await expect(profile.page.getByLabel('受理順番号')).toHaveText(mode === 'outbox-only' ? '3' : '2');
    } finally { await f.close(); }
  });
}
