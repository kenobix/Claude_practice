import { execFile } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { copyFile, mkdir, readFile, stat } from 'node:fs/promises';
import { join } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { expect, test } from '@playwright/test';
import * as Y from 'yjs';
import { decodeFrame, encodeFrame } from '../shared/protocol.ts';
import { append, browserFixture, createDocument, grant, moveToEnd, saveStatus, sequence } from '../tools/browser.ts';
import { probe } from '../tools/probe.ts';
import { body, content, openDocument, signIn } from './browser-helpers.ts';

const title = 'Phase 4 検証';
const backupCommand = fileURLToPath(new URL('../ops/backup.ts', import.meta.url));
const runBackup = (source: string, target: string) => promisify(execFile)(process.execPath, [backupCommand, source, target]);

test('P4-1: Chromium composition events commit Japanese text, emoji, newline and a keyboard paste', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const [owner, reader] = await Promise.all([f.profile('owner'), f.profile('reader')]);
    const [a, b] = [owner.page, reader.page];
    await Promise.all([signIn(a, app.origin, app.logins[0]), signIn(b, app.origin, app.logins[1])]);
    await createDocument(a, title);
    await grant(a, 'レン', 'viewer');
    await openDocument(b, title);
    await moveToEnd(a);

    const events = await a.evaluateHandle(() => {
      const observed: { type: string; data: string }[] = [];
      for (const type of ['compositionstart', 'compositionupdate', 'compositionend', 'paste']) {
        document.addEventListener(type, (event) => {
          observed.push({ type, data: event instanceof CompositionEvent ? event.data : '' });
        });
      }
      return observed;
    });
    const cdp = await owner.context.newCDPSession(a);
    await cdp.send('Input.imeSetComposition', { text: 'にほん', selectionStart: 3, selectionEnd: 3 });
    await cdp.send('Input.imeSetComposition', { text: '日本', selectionStart: 2, selectionEnd: 2 });
    await cdp.send('Input.insertText', { text: '日本' });
    await expect.poll(() => body(b)).toBe('日本');

    await a.keyboard.insertText('🙂');
    await a.keyboard.press('Enter');
    await owner.context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: app.origin });
    await a.evaluate(() => navigator.clipboard.writeText('貼り付け\n二行目'));
    await a.keyboard.press('Control+v');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    const expected = '日本🙂\n貼り付け\n二行目';
    await expect.poll(() => body(a)).toBe(expected);
    await expect.poll(() => body(b)).toBe(expected);
    expect(await events.jsonValue()).toEqual(expect.arrayContaining([
      { type: 'compositionstart', data: '' },
      { type: 'compositionupdate', data: 'にほん' },
      { type: 'compositionupdate', data: '日本' },
      { type: 'compositionend', data: '日本' },
      { type: 'paste', data: '' },
    ]));
    expect(await sequence(a).textContent()).toBe(await sequence(b).textContent());
  } finally { await f.close(); }
});

test('P4-1: individual Undo preserves another writer’s edit', async () => {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const [{ page: a }, { page: b }] = await Promise.all([f.profile('owner'), f.profile('editor')]);
    await Promise.all([signIn(a, app.origin, app.logins[0]), signIn(b, app.origin, app.logins[1])]);
    await createDocument(a, title);
    await grant(a, 'レン', 'editor');
    await openDocument(b, title);
    await append(a, '自分');
    await expect.poll(() => body(b)).toBe('自分');
    await append(b, '相手');
    await expect.poll(() => body(a)).toBe('自分相手');
    await moveToEnd(a);
    await a.keyboard.press('Control+z');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await expect.poll(() => body(b)).toBe('相手');
    await expect.poll(() => body(a)).toBe('相手');
    await expect(sequence(a)).toHaveText('3');
    await expect(sequence(b)).toHaveText('3');
  } finally { await f.close(); }
});

function databaseRows(filename: string) {
  const db = new DatabaseSync(filename, { readOnly: true });
  try {
    expect(db.prepare('PRAGMA integrity_check').get()?.integrity_check).toBe('ok');
    expect(db.prepare('PRAGMA foreign_key_check').all()).toEqual([]);
    return {
      documents: db.prepare('SELECT * FROM documents ORDER BY id').all(),
      grants: db.prepare('SELECT * FROM grants ORDER BY document_id, user_id').all(),
      snapshots: db.prepare('SELECT document_id, sequence, hex(state) AS state, created_at FROM snapshots ORDER BY document_id').all(),
      updates: db.prepare('SELECT document_id, sequence, hex(payload) AS payload FROM updates ORDER BY document_id, sequence').all(),
      receipts: db.prepare('SELECT * FROM receipts ORDER BY document_id, sequence, message_id').all(),
    };
  } finally { db.close(); }
}

test('P4-2: a live WAL backup restores a common point, receipts, grants and a deletion in an isolated app', async () => {
  const f = await browserFixture();
  try {
    const original = await f.start({ snapshotEvery: 2 });
    const { page: a } = await f.profile('original-owner');
    let previous: ReturnType<typeof decodeFrame> | undefined;
    a.on('websocket', (ws) => ws.on('framesent', ({ payload }) => {
      if (typeof payload === 'string' || previous) return;
      const frame = decodeFrame(payload);
      if (frame.header.type === 'UPDATE') previous = frame;
    }));
    await signIn(a, original.origin, original.logins[0]);
    const removed = await createDocument(a, '削除済みの試験文書');
    await grant(a, 'レン', 'viewer');
    a.once('dialog', (dialog) => dialog.accept());
    await a.getByRole('button', { name: '文書を削除' }).click();
    await expect(a.getByRole('alert')).toContainText('文書が削除されました');

    const id = await createDocument(a, title);
    await grant(a, 'レン', 'editor');
    await grant(a, 'レン', 'viewer');
    for (const [index, part] of ['会', '議', '録'].entries()) {
      await append(a, part);
      await expect(sequence(a)).toHaveText(String(index + 1));
      await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    }
    expect((await stat(f.databasePath + '-wal')).size).toBeGreaterThan(32);
    const atBackup = databaseRows(f.databasePath);
    expect(atBackup.documents).toEqual(expect.arrayContaining([
      expect.objectContaining({ id, status: 'active', sequence: 3, acl_revision: 2 }),
      expect.objectContaining({ id: removed, status: 'deleted' }),
    ]));
    expect(atBackup.grants).toContainEqual(expect.objectContaining({ document_id: id, user_id: 'ren', role: 'viewer', revision: 2 }));
    expect(atBackup.snapshots.map(({ sequence }) => sequence)).toEqual([2]);
    expect(atBackup.updates.map(({ sequence }) => sequence)).toEqual([3]);
    expect(atBackup.receipts.map(({ sequence }) => sequence)).toEqual([1, 2, 3]);

    const backup = join(f.dir, 'point-in-time.sqlite');
    await runBackup(f.databasePath, backup);
    expect(databaseRows(backup)).toEqual(atBackup);
    expect(databaseRows(f.databasePath)).toEqual(atBackup);
    const backupBytes = await readFile(backup);
    await expect(runBackup(f.databasePath, backup)).rejects.toThrow(/EEXIST/);
    await expect(runBackup(f.databasePath, f.databasePath)).rejects.toThrow(/EEXIST/);
    expect(await readFile(backup)).toEqual(backupBytes);
    expect(databaseRows(f.databasePath)).toEqual(atBackup);

    await append(a, '後日');
    await expect(sequence(a)).toHaveText('4');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await grant(a, 'レン', 'editor');
    const later = databaseRows(f.databasePath);
    expect(later.grants).toContainEqual(expect.objectContaining({ document_id: id, user_id: 'ren', role: 'editor', revision: 3 }));

    const dir = join(f.dir, 'restore');
    await mkdir(dir);
    const restoredPath = join(dir, 'restored.sqlite');
    await copyFile(backup, restoredPath);
    const restored = await f.start({ databasePath: restoredPath });
    const [viewer, owner] = await Promise.all([f.profile('restore-viewer'), f.profile('restore-owner')]);
    await Promise.all([
      signIn(viewer.page, restored.origin, restored.logins[1]),
      signIn(owner.page, restored.origin, restored.logins[0]),
    ]);
    await openDocument(viewer.page, title);
    await expect(content(viewer.page)).toHaveAttribute('contenteditable', 'false');
    await expect.poll(() => body(viewer.page)).toBe('会議録');
    await expect(sequence(viewer.page)).toHaveText('3');
    await expect(viewer.page.getByRole('button', { name: /削除済みの試験文書/ })).toHaveCount(0);
    const response = await viewer.page.request.get(`${restored.origin}/api/documents/${removed}`);
    expect(response.status()).toBe(410);
    expect(await response.json()).toEqual({ error: 'DOCUMENT_DELETED' });

    const viewerClient = randomUUID();
    await probe(viewer.context, restored.origin, id, viewerClient, async (socket, next) => {
      const { header, payload } = await next('STATE');
      expect(header).toMatchObject({ role: 'viewer', grant_revision: 2, sequence: 3 });
      const draft = new Y.Doc();
      try {
        Y.applyUpdate(draft, payload);
        draft.getText('body').insert(0, '拒否');
        for (const [revision, code] of [[3, 'GRANT_CHANGED'], [2, 'WRITE_FORBIDDEN']] as const) {
          const operation = randomUUID();
          socket.send(encodeFrame({ type: 'UPDATE', client_id: viewerClient, message_id: operation, grant_revision: revision },
            Y.encodeStateAsUpdate(draft)));
          expect((await next('ERROR')).header).toMatchObject({ code, message_id: operation });
        }
      } finally { draft.destroy(); }
    });

    const accepted = previous!;
    await probe(owner.context, restored.origin, id, String(accepted.header.client_id), async (socket, next) => {
      await next('STATE');
      socket.send(encodeFrame(accepted.header as Parameters<typeof encodeFrame>[0], accepted.payload));
      expect((await next('ACK')).header).toMatchObject({ sequence: 1, message_id: accepted.header.message_id });
    });
    expect(databaseRows(restoredPath)).toEqual(atBackup);
    expect(databaseRows(f.databasePath)).toEqual(later);
    expect(await readFile(backup)).toEqual(backupBytes);
    await expect.poll(() => body(viewer.page)).toBe('会議録');
  } finally { await f.close(); }
});
