import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { chromium, expect } from '@playwright/test';
import type { BrowserContext, Page, WebSocketRoute } from '@playwright/test';
import { startApplication } from '../server/application.ts';
import { decodeFrame } from '../shared/protocol.ts';
import { content } from '../tests/browser-helpers.ts';

export async function browserFixture() {
  const dir = await mkdtemp(join(tmpdir(), 'collab-phase4-'));
  const contexts = new Set<BrowserContext>();
  const servers = new Set<Awaited<ReturnType<typeof startApplication>>>();
  return {
    dir,
    databasePath: join(dir, 'live.sqlite'),
    async start(options: Partial<Parameters<typeof startApplication>[0]> = {}) {
      const app = await startApplication({ databasePath: join(dir, 'live.sqlite'), ...options });
      servers.add(app);
      return app;
    },
    async profile(name: string, options: Parameters<typeof chromium.launchPersistentContext>[1] = {}) {
      const context = await chromium.launchPersistentContext(join(dir, name), options);
      contexts.add(context);
      return { context, page: context.pages()[0] };
    },
    async close() {
      await Promise.allSettled([...contexts].map((context) => context.close()));
      await Promise.allSettled([...servers].map((server) => server.close()));
      await rm(dir, { recursive: true, force: true });
    },
  };
}

export const connection = (page: Page) => page.locator('[data-connection]');
export const saveStatus = (page: Page) => page.locator('[data-server-save]');
export const sequence = (page: Page) => page.getByLabel('受理順番号');

export async function createDocument(page: Page, title: string) {
  await page.getByLabel('新しい文書').fill(title);
  await page.getByRole('button', { name: '作成', exact: true }).click();
  await expect(connection(page)).toHaveAttribute('data-connection', 'online');
  return (await page.locator('[data-document-id]').getAttribute('data-document-id'))!;
}

export async function grant(page: Page, user: string, role: string) {
  const select = page.getByLabel(user + 'の権限');
  await select.selectOption(role);
  await expect(select).toHaveValue(role);
}

export async function moveToEnd(page: Page) {
  await content(page).focus();
  await page.keyboard.press('Control+End');
}

export async function append(page: Page, value: string) {
  await moveToEnd(page);
  await page.keyboard.insertText(value);
}

type Frame = {
  direction: 'sent' | 'received';
  header: Record<string, unknown>;
  bytes: number;
  at: number;
};

export async function watchWire(page: Page, observe: (frame: Frame) => void = () => {}) {
  let wire: { browser: WebSocketRoute; server: WebSocketRoute } | undefined;
  await page.routeWebSocket('**/ws/**', (browser) => {
    const server = browser.connectToServer();
    wire = { browser, server };
    function route(direction: Frame['direction'], target: WebSocketRoute) {
      return (message: string | Buffer) => {
        if (typeof message !== 'string') {
          const at = performance.now();
          const { header } = decodeFrame(message);
          observe({ direction, header, bytes: message.length, at });
        }
        target.send(message);
      };
    }
    browser.onMessage(route('sent', server));
    server.onMessage(route('received', browser));
  });
  return {
    cut() {
      if (!wire) throw new Error('No active connection to cut');
      wire.server.close();
      wire.browser.close();
    },
  };
}
