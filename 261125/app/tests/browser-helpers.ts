import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';

export async function signIn(page: Page, origin: string, account: { id: string; password: string }) {
  await page.goto(origin);
  await page.getByLabel('アカウント').selectOption(account.id);
  await page.getByLabel('パスワード').fill(account.password);
  await page.getByRole('button', { name: 'ログイン', exact: true }).click();
  await expect(page.getByRole('button', { name: 'ログアウト' })).toBeVisible();
}

export function content(page: Page) { return page.locator('.cm-content'); }

export function body(page: Page) {
  return content(page).evaluate((element) => [...element.querySelectorAll('.cm-line')].map((line) => {
    const copy = line.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('.cm-ySelectionCaret, .cm-widgetBuffer').forEach((node) => node.remove());
    return copy.textContent;
  }).join('\n'));
}

export async function openDocument(page: Page, title: string) {
  await page.getByRole('button', { name: '一覧を更新' }).click();
  await page.getByRole('navigation', { name: '開ける文書' }).getByRole('button', { name: new RegExp(title) }).click();
}
