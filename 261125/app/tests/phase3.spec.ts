import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect, test as base } from '@playwright/test';
import type { BrowserContext, Page, WebSocketRoute } from '@playwright/test';
import { startApplication } from '../server/application.ts';
import { decodeFrame } from '../shared/protocol.ts';
import { body, content, openDocument, signIn } from './browser-helpers.ts';

type Options = Partial<Parameters<typeof startApplication>[0]>;

const test = base.extend<{ fixture: {
  start: (options?: Options) => ReturnType<typeof startApplication>;
  profile: (name: string) => Promise<BrowserContext>;
} }>({
  fixture: async ({}, use) => {
    const dir = await mkdtemp(join(tmpdir(), 'collab-browser-grants-'));
    const contexts: BrowserContext[] = [];
    const servers: Awaited<ReturnType<typeof startApplication>>[] = [];
    try {
      await use({
        async start(options = {}) {
          const app = await startApplication({ databasePath: join(dir, 'collab.sqlite'), ...options });
          servers.push(app);
          return app;
        },
        async profile(name) {
          const context = await chromium.launchPersistentContext(join(dir, name));
          contexts.push(context);
          return context;
        },
      });
    } finally {
      await Promise.allSettled(contexts.map((context) => context.close()));
      await Promise.allSettled(servers.map((server) => server.close()));
      await rm(dir, { recursive: true, force: true });
    }
  },
});

const title = '権限と下書き';

function connection(page: Page) { return page.locator('[data-connection]'); }
function saveStatus(page: Page) { return page.locator('[data-server-save]'); }
function drafts(page: Page) { return page.getByRole('region', { name: '同期を停止した下書き' }); }

async function create(page: Page) {
  await page.getByLabel('新しい文書').fill(title);
  await page.getByRole('button', { name: '作成', exact: true }).click();
  await expect(connection(page)).toHaveAttribute('data-connection', 'online');
}

async function append(page: Page, value: string) {
  await content(page).focus();
  await page.keyboard.press('End');
  await page.keyboard.insertText(value);
}

async function setRole(page: Page, name: string, role: string) {
  const grant = page.getByLabel(name + 'の権限');
  await grant.selectOption(role);
  await expect(grant).toHaveValue(role);
}

async function wiretap(page: Page) {
  let wire: { browser: WebSocketRoute; server: WebSocketRoute } | undefined;
  let hold = false;
  const updates: { id: string; revision: number }[] = [];
  const errors: string[] = [];
  await page.routeWebSocket('**/ws/**', (browser) => {
    const server = browser.connectToServer();
    wire = { browser, server };
    browser.onMessage((message) => {
      if (typeof message !== 'string') {
        const { header } = decodeFrame(message);
        if (header.type === 'UPDATE') {
          updates.push({ id: String(header.message_id), revision: Number(header.grant_revision) });
          if (hold) return;
        }
      }
      server.send(message);
    });
    server.onMessage((message) => {
      if (typeof message !== 'string') {
        const { header } = decodeFrame(message);
        if (header.type === 'ERROR') errors.push(String(header.code));
      }
      browser.send(message);
    });
  });
  return {
    updates, errors,
    holdUpdates() { hold = true; },
    releaseUpdates() { hold = false; },
    cut() { wire!.server.close(); wire!.browser.close(); },
  };
}

async function exportText(page: Page) {
  const result = page.waitForEvent('download');
  await drafts(page).getByRole('button', { name: 'テキストを書き出す' }).click();
  const download = await result;
  expect(download.suggestedFilename()).toMatch(/^draft-[0-9a-f-]+-\d+\.txt$/);
  return readFile(await download.path(), 'utf8');
}

test('P3-1: online downgrade shows the official body and an isolated export before regrant', async ({ fixture }) => {
  const app = await fixture.start();
  const a = (await fixture.profile('owner')).pages()[0];
  const b = (await fixture.profile('editor')).pages()[0];
  const c = (await fixture.profile('reader')).pages()[0];
  const tap = await wiretap(b);
  await Promise.all([a, b, c].map((page, index) => signIn(page, app.origin, app.logins[index])));
  await create(a);
  await setRole(a, 'レン', 'editor');
  await setRole(a, 'ソラ', 'viewer');
  await openDocument(b, title);
  await openDocument(c, title);
  await append(a, '正式');
  await expect.poll(() => body(b)).toBe('正式');
  await append(b, '受理');
  await expect(saveStatus(b)).toHaveText('サーバー保存済み');
  await expect.poll(() => body(c)).toBe('正式受理');

  tap.holdUpdates();
  await append(b, '下書き');
  await expect.poll(() => tap.updates.length).toBe(2);
  await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
  await setRole(a, 'レン', 'viewer');
  await expect(content(b)).toHaveAttribute('contenteditable', 'false');
  await expect.poll(() => body(b)).toBe('正式受理');
  await expect(b.getByLabel('受理順番号')).toHaveText('2');
  await expect(drafts(b)).toContainText('付与版 1');
  expect(await exportText(b)).toBe('正式受理下書き');
  expect(tap.errors).toContain('GRANT_CHANGED');

  await append(a, '追加');
  await expect.poll(() => body(b)).toBe('正式受理追加');
  await expect.poll(() => body(c)).toBe('正式受理追加');
  tap.releaseUpdates();
  const previous = tap.updates.map(({ id }) => id);
  await setRole(a, 'レン', 'editor');
  await expect(content(b)).toHaveAttribute('contenteditable', 'true');
  await expect(b.getByLabel('受理順番号')).toHaveText('3');
  await append(b, '再付与');
  await expect(saveStatus(b)).toHaveText('サーバー保存済み');
  await expect.poll(() => body(c)).toBe('正式受理追加再付与');
  await expect(a.getByLabel('受理順番号')).toHaveText('4');
  expect(tap.updates.slice(0, 2).map(({ id }) => id)).toEqual(previous);
  expect(tap.updates.map(({ revision }) => revision)).toEqual([1, 1, 3]);
});

test('P3-1: revocation during a disconnect survives profile restart without uploading the queued edit', async ({ fixture }) => {
  const app = await fixture.start();
  const a = (await fixture.profile('owner')).pages()[0];
  let offline = await fixture.profile('offline');
  let b = offline.pages()[0];
  const tap = await wiretap(b);
  await Promise.all([signIn(a, app.origin, app.logins[0]), signIn(b, app.origin, app.logins[1])]);
  await create(a);
  await setRole(a, 'レン', 'editor');
  await openDocument(b, title);
  await append(a, '共有');
  await expect.poll(() => body(b)).toBe('共有');
  tap.cut();
  await expect(connection(b)).toHaveAttribute('data-connection', 'offline');
  await append(b, '切断中');
  await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');
  await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
  expect(tap.updates).toEqual([]);
  await setRole(a, 'レン', 'viewer');
  await b.getByRole('button', { name: '接続し直す' }).click();
  await expect(content(b)).toHaveAttribute('contenteditable', 'false');
  await expect.poll(() => body(b)).toBe('共有');
  await expect(b.getByLabel('受理順番号')).toHaveText('1');
  expect(await exportText(b)).toBe('共有切断中');
  expect(tap.updates).toEqual([]);
  await setRole(a, 'レン', '');
  await expect(b.getByRole('alert')).toHaveText('文書を開く権限がありません。');
  await expect(saveStatus(b)).toHaveText('サーバーへの保存を停止');
  await expect(content(b)).toHaveCount(0);
  expect(await exportText(b)).toBe('共有切断中');
  await offline.close();

  offline = await fixture.profile('offline');
  b = offline.pages()[0];
  const after = await wiretap(b);
  await b.goto(app.origin);
  await expect(b.getByRole('navigation', { name: '開ける文書' }).getByRole('button')).toHaveCount(0);
  expect(await exportText(b)).toBe('共有切断中');
  await setRole(a, 'レン', 'editor');
  await openDocument(b, title);
  await expect(content(b)).toHaveAttribute('contenteditable', 'true');
  await expect.poll(() => body(b)).toBe('共有');
  await expect(b.getByLabel('受理順番号')).toHaveText('1');
  expect(after.updates).toEqual([]);
});

test('P3-2: confirmed deletion closes a live connection and a stale profile exports after join denial', async ({ fixture }) => {
  const app = await fixture.start();
  const a = (await fixture.profile('owner')).pages()[0];
  const b = (await fixture.profile('offline')).pages()[0];
  const c = (await fixture.profile('reader')).pages()[0];
  const tap = await wiretap(b);
  await Promise.all([a, b, c].map((page, index) => signIn(page, app.origin, app.logins[index])));
  await create(a);
  await setRole(a, 'レン', 'editor');
  await setRole(a, 'ソラ', 'viewer');
  await openDocument(b, title);
  await openDocument(c, title);
  await append(a, '削除前');
  await expect.poll(() => body(b)).toBe('削除前');
  tap.cut();
  await expect(connection(b)).toHaveAttribute('data-connection', 'offline');
  await append(b, '端末の続き');
  await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
  await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');
  const previous = await a.locator('[data-document-id]').getAttribute('data-document-id');

  a.once('dialog', (dialog) => dialog.accept());
  await a.getByRole('button', { name: '文書を削除' }).click();
  await expect(c.getByRole('alert')).toContainText('文書が削除されました');
  await expect(content(c)).toHaveCount(0);
  const cold = await b.context().newPage();
  await cold.goto(app.origin);
  await expect(cold.getByRole('navigation', { name: '開ける文書' }).getByRole('button')).toHaveCount(0);
  expect(await exportText(cold)).toBe('削除前端末の続き');
  await b.getByRole('button', { name: '接続し直す' }).click();
  await expect(b.getByRole('alert')).toContainText('文書が削除されました');
  await expect(content(b)).toHaveCount(0);
  expect(await exportText(b)).toBe('削除前端末の続き');
  expect(tap.updates).toEqual([]);
  await c.getByRole('button', { name: '一覧を更新' }).click();
  await expect(c.getByRole('navigation', { name: '開ける文書' }).getByRole('button')).toHaveCount(0);
  await create(a);
  expect(await a.locator('[data-document-id]').getAttribute('data-document-id')).not.toBe(previous);
});

test('P3-2: logout drains queued IndexedDB writes before account switching and closes other tabs', async ({ fixture }) => {
  const app = await fixture.start();
  const context = await fixture.profile('shared');
  const a = context.pages()[0];
  const otherTab = await context.newPage();
  await signIn(a, app.origin, app.logins[0]);
  await create(a);
  await append(a, '旧アカウント');
  await expect(saveStatus(a)).toHaveText('サーバー保存済み');
  await otherTab.goto(app.origin);
  await openDocument(otherTab, title);
  await expect.poll(() => body(otherTab)).toBe('旧アカウント');

  // Hold a real transaction in the other tab so that the next local save queues behind it.
  await otherTab.evaluate(async () => {
    type Gate = Window & { releaseCache?: () => void };
    const request = indexedDB.open('collab-aki');
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise<void>((resolve) => {
      let held = true;
      (window as Gate).releaseCache = () => { held = false; db.close(); };
      db.onversionchange = () => db.close();
      const tx = db.transaction(['states', 'outbox'], 'readwrite');
      const store = tx.objectStore('states');
      const wait = () => { if (held) store.get('gate').onsuccess = wait; };
      store.get('gate').onsuccess = () => { wait(); resolve(); };
    });
  });
  await append(a, '保留中');
  await expect(a.locator('[data-local-save]')).toHaveAttribute('data-local-save', 'writing');
  const ended = a.waitForResponse((response) => response.url().endsWith('/api/session') && response.request().method() === 'DELETE');
  await a.getByRole('button', { name: 'ログアウト' }).click();
  expect((await ended).status()).toBe(200);
  await expect(a.getByRole('button', { name: 'ログイン', exact: true })).toHaveCount(0);
  await otherTab.evaluate(() => (window as Window & { releaseCache?: () => void }).releaseCache!());
  await expect(a.getByRole('button', { name: 'ログイン', exact: true })).toBeEnabled();
  await expect(otherTab.getByRole('button', { name: 'ログイン', exact: true })).toBeVisible();
  const names = () => a.evaluate(async () => (await indexedDB.databases()).map((db) => db.name));
  expect(await names()).not.toContain('collab-aki');
  await signIn(a, app.origin, app.logins[1]);
  await expect(a.getByRole('navigation', { name: '開ける文書' }).getByRole('button')).toHaveCount(0);
  await expect(drafts(a)).toHaveCount(0);
  await create(a);
  expect(await body(a)).toBe('');
  await expect(a.getByLabel('受理順番号')).toHaveText('0');
  expect(await names()).toEqual(['collab-ren']);
});

test('P3-3: a real RATE_LIMIT keeps the queued edit and retries the same operation after refill', async ({ fixture }) => {
  let now = 0;
  const app = await fixture.start({ updateNow: () => now });
  const a = (await fixture.profile('owner')).pages()[0];
  const b = (await fixture.profile('observer')).pages()[0];
  const tap = await wiretap(a);
  await Promise.all([signIn(a, app.origin, app.logins[0]), signIn(b, app.origin, app.logins[1])]);
  await create(a);
  await setRole(a, 'レン', 'viewer');
  await openDocument(b, title);
  for (let index = 0; index < 20; index += 1) {
    await append(a, 'X');
    await expect(a.getByLabel('受理順番号')).toHaveText(String(index + 1));
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
  }
  await expect.poll(() => body(b)).toBe('X'.repeat(20));
  await append(a, 'Y');
  await expect(a.getByText('更新が短時間に集中したため、少し待ってから自動で再送します。')).toBeVisible();
  await expect(saveStatus(a)).toHaveText('サーバー保存待ち');
  await expect(connection(a)).toHaveAttribute('data-connection', 'online');
  await expect(drafts(a)).toHaveCount(0);
  await expect(b.getByLabel('受理順番号')).toHaveText('20');
  const retry = tap.updates[20].id;
  expect(tap.errors).toContain('RATE_LIMIT');
  now = 100;
  await expect(saveStatus(a)).toHaveText('サーバー保存済み');
  await expect.poll(() => body(b)).toBe('X'.repeat(20) + 'Y');
  await expect(b.getByLabel('受理順番号')).toHaveText('21');
  expect(tap.updates.length).toBeGreaterThan(21);
  expect(new Set(tap.updates.slice(20).map(({ id }) => id))).toEqual(new Set([retry]));
});

test('P3-3: an expired session retains a pending edit for the same account after reauthentication', async ({ fixture }) => {
  let now = 0;
  const app = await fixture.start({ sessionNow: () => now });
  const a = (await fixture.profile('owner')).pages()[0];
  const b = (await fixture.profile('observer')).pages()[0];
  const tap = await wiretap(a);
  await signIn(a, app.origin, app.logins[0]);
  await create(a);
  now = 1;
  await signIn(b, app.origin, app.logins[1]);
  await setRole(a, 'レン', 'viewer');
  await openDocument(b, title);
  await append(a, '公式');
  await expect(saveStatus(a)).toHaveText('サーバー保存済み');
  await expect.poll(() => body(b)).toBe('公式');

  tap.holdUpdates();
  await append(a, '続き');
  await expect.poll(() => tap.updates.length).toBe(2);
  now = 3_600_000;
  tap.releaseUpdates();
  await expect(a.getByRole('alert')).toContainText('セッションの有効期限が切れました');
  await expect(saveStatus(a)).toHaveText('サーバー保存待ち');
  await expect(b.getByLabel('受理順番号')).toHaveText('1');
  await a.getByRole('button', { name: 'ログインし直す' }).click();
  await expect(a.getByRole('button', { name: 'ログイン', exact: true })).toBeEnabled();
  await signIn(a, app.origin, app.logins[0]);
  await openDocument(a, title);
  await expect(saveStatus(a)).toHaveText('サーバー保存済み');
  await expect.poll(() => body(b)).toBe('公式続き');
  await expect(b.getByLabel('受理順番号')).toHaveText('2');
  expect(tap.updates.length).toBeGreaterThanOrEqual(3);
  expect(tap.updates.slice(1)).toEqual(tap.updates.slice(1).map(() => tap.updates[1]));
});
