import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect, test } from '@playwright/test';
import type { BrowserContext, Page, WebSocketRoute } from '@playwright/test';
import { startApplication } from '../server/application.ts';
import { decodeFrame } from '../shared/protocol.ts';
import { body, content, openDocument, signIn } from './browser-helpers.ts';

async function editAtMiddle(page: Page, value: string, ...keys: string[]) {
  await content(page).focus();
  for (const key of ['Home', 'ArrowRight', 'ArrowRight']) await page.keyboard.press(key);
  await page.keyboard.insertText(value);
  for (const key of keys) await page.keyboard.press(key);
}

test('P1-1/P1-3: isolated profiles share concurrent insertions and deletions without echo', async () => {
  const profiles = await mkdtemp(join(tmpdir(), 'collab-profiles-'));
  const app = await startApplication({ databasePath: join(profiles, 'collab.sqlite') });
  const contexts: BrowserContext[] = [];
  try {
    const viewport = { width: 1320, height: 900 };
    for (const { id } of app.logins) contexts.push(await chromium.launchPersistentContext(join(profiles, id), { viewport }));
    const pages = contexts.map((ctx) => ctx.pages()[0]);
    const errors: string[] = [];
    const sends = [0, 0, 0];
    const held: { socket: WebSocketRoute; frame: Buffer }[] = [];
    let hold = false;
    for (const [index, page] of pages.entries()) {
      page.on('pageerror', (error) => errors.push(error.message));
      await page.routeWebSocket('**/ws/**', (socket) => {
        const remote = socket.connectToServer();
        socket.onMessage((message) => {
          if (typeof message !== 'string' && decodeFrame(message).header.type === 'UPDATE') sends[index] += 1;
          remote.send(message);
        });
        remote.onMessage((message) => {
          if (hold && typeof message !== 'string' && decodeFrame(message).header.type === 'APPLIED') held.push({ socket, frame: message });
          else socket.send(message);
        });
      });
      await signIn(page, app.origin, app.logins[index]);
    }
    const [a, b, c] = pages;
    const sessions = await Promise.all(contexts.map(async (ctx) => (await ctx.cookies(app.origin)).find(({ name }) => name === 'collab_session')));
    expect(sessions.map((session) => session?.httpOnly)).toEqual([true, true, true]);
    expect(new Set(sessions.map((session) => session?.value)).size).toBe(3);

    const title = '勉強会メモ';
    await a.getByLabel('新しい文書').fill(title);
    await a.getByRole('button', { name: '作成', exact: true }).click();
    await expect(a.locator('[data-connection]')).toHaveText('接続中');
    await a.getByLabel('レンの権限').selectOption('editor');
    await expect(a.getByLabel('レンの権限')).toHaveValue('editor');
    await a.getByLabel('ソラの権限').selectOption('viewer');
    await expect(a.getByLabel('ソラの権限')).toHaveValue('viewer');
    await Promise.all([openDocument(b, title), openDocument(c, title)]);
    for (const page of pages) {
      await expect(page.locator('[data-connection]')).toHaveText('接続中');
      await expect(page.getByRole('region', { name: '接続中の利用者' }).getByRole('listitem')).toHaveCount(3);
      await expect(page.locator('[data-server-save]')).toHaveAttribute('data-server-save', 'idle');
    }

    await content(a).click();
    await a.keyboard.insertText('abcd');
    for (const page of pages) {
      await expect.poll(() => body(page)).toBe('abcd');
      await expect(page.getByLabel('受理順番号')).toHaveText('1');
    }
    expect(sends).toEqual([1, 0, 0]);

    hold = true;
    await Promise.all([editAtMiddle(a, 'X', 'ArrowLeft', 'Backspace'), editAtMiddle(b, 'Y', 'Delete')]);
    await expect.poll(() => held.length).toBe(12);
    expect(await Promise.all(pages.map(body))).toEqual(['aXcd', 'abYd', 'abcd']);
    hold = false;
    for (const { socket, frame } of held) socket.send(frame);
    for (const page of pages) await expect(page.getByLabel('受理順番号')).toHaveText('5');
    const final = await body(a);
    expect(final).toMatch(/^a(?:XY|YX)d$/);
    expect(await Promise.all(pages.map(body))).toEqual([final, final, final]);
    expect(sends).toEqual([3, 2, 0]);

    await expect(content(c)).toHaveAttribute('contenteditable', 'false');
    await content(c).click();
    await c.keyboard.insertText('不可');
    expect(await body(c)).toBe(final);
    expect(sends).toEqual([3, 2, 0]);

    const duplicate = await contexts[2].newPage();
    await duplicate.goto(app.origin);
    await openDocument(duplicate, title);
    await expect(duplicate.locator('[data-connection]')).toHaveText('未接続');
    await expect(content(duplicate)).toHaveCount(0);
    await c.close();
    await expect(a.getByRole('region', { name: '接続中の利用者' }).getByRole('listitem')).toHaveCount(2);
    await duplicate.getByRole('button', { name: '接続し直す' }).click();
    await expect(duplicate.getByLabel('受理順番号')).toHaveText('5');
    expect(await body(duplicate)).toBe(final);
    expect(errors).toEqual([]);
    console.log(`Three profiles: body=${final}; sequence=5; UPDATE sends=${sends.join(',')}; slot reused`);

    if (process.env.PHASE1_SCREENSHOT) await a.screenshot({ path: process.env.PHASE1_SCREENSHOT });
    await a.setViewportSize({ width: 390, height: 844 });
    await expect(a.getByLabel('本文')).toBeVisible();
    expect(await a.locator('body').evaluate((element) => element.scrollWidth)).toBeLessThanOrEqual(390);
  } finally {
    await Promise.allSettled(contexts.map((context) => context.close()));
    await app.close();
    await rm(profiles, { recursive: true, force: true });
  }
});
