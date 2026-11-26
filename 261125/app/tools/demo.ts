import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir, readFile, writeFile } from 'node:fs/promises';
import { join, resolve } from 'node:path';
import { createInterface } from 'node:readline';
import { setTimeout as pause } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { promisify } from 'node:util';
import { expect } from '@playwright/test';
import type { Page } from '@playwright/test';
import { encodeFrame } from '../shared/protocol.ts';
import { append, browserFixture, connection, createDocument, grant, saveStatus, sequence, watchWire } from './browser.ts';
import { body, content, openDocument, signIn } from '../tests/browser-helpers.ts';
import { probe } from './probe.ts';

const out = process.argv[2];
if (!out || process.argv.length !== 3) throw new Error('Usage: npm run demo -- NEW_VIDEO_DIR');
const output = resolve(out);
await mkdir(output);
const exec = promisify(execFile);
const seed = 261125;
const title = '勉強会の記録';
const viewport = { width: 900, height: 640 };
const primary = '共同編集｜アキ（左）・レン（右）';
const clips: { start: number; caption: string }[] = [];

function time(seconds: number) {
  const s = Math.round(seconds * 100);
  return `${Math.floor(s / 360000)}:${String(Math.floor(s / 6000) % 60).padStart(2, '0')}:${String(Math.floor(s / 100) % 60).padStart(2, '0')}.${String(s % 100).padStart(2, '0')}`;
}

async function render(dir: string, name: string, videos: string[], duration: number, heading: string) {
  function line(from: number, to: number, style: string, value: string) {
    return `Dialogue: 0,${time(from)},${time(to)},${style},,0,0,0,,${value.replaceAll('\n', '\\N')}`;
  }
  const sideBySide = videos.length === 2;
  const width = sideBySide ? 1800 : 900;
  const captions = [
    '[Script Info]', 'ScriptType: v4.00+', `PlayResX: ${width}`, 'PlayResY: 812', 'WrapStyle: 0',
    '[V4+ Styles]',
    'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding',
    `Style: Title,Noto Sans CJK JP,${sideBySide ? 35 : 29},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,0,0,8,30,30,12,1`,
    `Style: Caption,Noto Sans CJK JP,${sideBySide ? 32 : 23},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,36,36,36,1`,
    '[Events]', 'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text',
    line(0, duration, 'Title', heading),
    ...clips.map(({ start, caption }, index) => line(start, clips[index + 1]?.start ?? duration, 'Caption', caption)),
  ].join('\n');
  await writeFile(join(dir, 'captions.ass'), captions);
  const layout = sideBySide ? '[0:v][1:v]hstack=inputs=2,' : '[0:v]';
  const parameters = [
    '-hide_banner', '-loglevel', 'error', '-filter_complex_threads', '2',
    ...videos.flatMap((path) => ['-i', path]), '-filter_complex',
    `${layout}pad=${width}:812:0:64:color=0x13294a,tpad=stop_mode=clone:stop_duration=${duration},ass=captions.ass`,
    '-t', String(duration), '-an', '-c:v', 'libx264', '-crf', '25', '-preset', 'fast',
    '-threads', '2', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', join(output, name),
  ];
  await exec('ffmpeg', parameters, { cwd: dir });
}

async function saved(page: Page, expectedSequence: number) {
  await expect(sequence(page)).toHaveText(String(expectedSequence));
  await expect(saveStatus(page)).toHaveText('サーバー保存済み');
}

function director() {
  const started = performance.now();
  return async (second: number, caption: string) => {
    await pause(Math.max(0, second * 1_000 - (performance.now() - started)));
    clips.push({ start: (performance.now() - started) / 1_000, caption });
  };
}

async function collaboration() {
  const f = await browserFixture();
  try {
    const app = await f.start();
    const options = { viewport, recordVideo: { dir: f.dir, size: viewport } };
    const [owner, editor] = await Promise.all([f.profile('owner', options), f.profile('editor', options)]);
    await Promise.all([signIn(owner.page, app.origin, app.logins[0]), signIn(editor.page, app.origin, app.logins[1])]);
    const id = await createDocument(owner.page, title);
    await grant(owner.page, 'レン', 'editor');
    const [a, b] = await Promise.all([owner.context.newPage(), editor.context.newPage()]);
    const scene = director();
    const tap = await watchWire(b);
    await Promise.all([a.goto(app.origin), b.goto(app.origin)]);
    await Promise.all([openDocument(a, title), openDocument(b, title)]);
    await expect(connection(a)).toHaveAttribute('data-connection', 'online');
    await expect(connection(b)).toHaveAttribute('data-connection', 'online');

    await scene(3, '01   共同編集\nアキとレンが別々のブラウザプロファイルから同じ文書を開きます。');
    await owner.page.close();
    await append(a, '議題：共同ノート');
    await saved(a, 1);
    await expect.poll(() => body(b)).toBe('議題：共同ノート');
    await append(b, '\n確認：次回の日程');
    await saved(b, 2);
    await expect.poll(() => body(a)).toBe('議題：共同ノート\n確認：次回の日程');

    await scene(32, '02   レンの通信を切断\n左は接続中のままです。右は端末内で編集できます。');
    tap.cut();
    await expect(connection(b)).toHaveAttribute('data-connection', 'offline');

    await scene(52, '03   どちらも編集を続ける\nレンの追記は端末に保存され、「サーバー保存待ち」になります。');
    await append(a, '\nアキ：資料を準備');
    await saved(a, 3);
    await append(b, '\nレン：候補日を確認');
    await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
    await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');

    await scene(77, '04   接続し直して合流\n両画面の本文と受理順番号がそろい、レンに保存確認が届きます。');
    await b.getByRole('button', { name: '接続し直す' }).click();
    await saved(b, 4);
    await expect(sequence(a)).toHaveText('4');
    const shared = await body(a);
    assert.equal(await body(b), shared);

    await scene(105, '05   レンの編集中に権限を変更\n再び右だけ切断し、下書きを保存してから閲覧者に変更します。');
    tap.cut();
    await expect(connection(b)).toHaveAttribute('data-connection', 'offline');
    await append(b, '\nレン：端末だけの下書き');
    await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');
    await grant(a, 'レン', 'viewer');

    const old = await b.evaluate(async (documentId) => {
      const db = await new Promise<IDBDatabase>((resolve, reject) => {
        const request = indexedDB.open('collab-ren');
        request.onsuccess = () => resolve(request.result);
        request.onerror = () => reject(request.error);
      });
      try {
        const tx = db.transaction(['outbox', 'device']);
        const device = tx.objectStore('device').get('clientId');
        const request = tx.objectStore('outbox').index('scope').getAll(documentId + ':1');
        const entry = await new Promise<{ bytes: Uint8Array; messageId: string }>((resolve, reject) => {
          request.onsuccess = () => resolve(request.result[0]);
          request.onerror = () => reject(request.error);
        });
        return { client: device.result as string, id: entry.messageId, bytes: Array.from(entry.bytes) };
      } finally { db.close(); }
    }, id);

    await scene(132, '06   新しい権限で閲覧\nレンが接続し直すと、右には正式本文と書き出し用の下書きが表示されます。');
    await b.getByRole('button', { name: '接続し直す' }).click();
    await expect(content(b)).toHaveAttribute('contenteditable', 'false');
    await expect.poll(() => body(b)).toBe(shared);
    let error: unknown;
    await probe(editor.context, app.origin, id, old.client, async (socket, next) => {
      await next('STATE');
      socket.send(encodeFrame({ type: 'UPDATE', client_id: old.client, grant_revision: 1, message_id: old.id }, Uint8Array.from(old.bytes)));
      error = (await next('ERROR')).header.code;
      assert.equal(error, 'GRANT_CHANGED');
    });
    await expect(sequence(b)).toHaveText('4');

    await scene(151, '07   旧権限での再送を拒否\n試験用の接続で旧付与版を送ると GRANT_CHANGED。順番号は4のままです。');
    const pending = b.waitForEvent('download');
    await b.getByRole('button', { name: 'テキストを書き出す' }).click();
    const downloaded = await pending;
    const file = join(f.dir, 'draft.txt');
    await downloaded.saveAs(file);
    const fileText = await readFile(file, 'utf8');
    assert.equal(fileText, shared + '\nレン：端末だけの下書き');

    await scene(165, '08   書き出したファイルを開く\nレンの追記が手元のテキストに残りました。左には正式本文が表示されています。');
    await b.goto(`data:text/plain;charset=utf-8,${encodeURIComponent(fileText)}`);
    await expect(b.getByText('レン：端末だけの下書き', { exact: false })).toBeVisible();
    await scene(180, '共同編集の確認を終了しました。');
    const paths = [a.video()!, b.video()!];
    await Promise.all([owner.context.close(), editor.context.close()]);
    await render(f.dir, 'collaboration.mp4', await Promise.all(paths.map((part) => part.path())), 180, primary);
    return { seed, finalSequence: 4, oldGrantError: error, exportedHasLocalText: true, captions: clips.slice(0, -1) };
  } finally { clips.length = 0; await f.close(); }
}

async function boot(database: string, port = 0) {
  const child = spawn(process.execPath, [fileURLToPath(new URL('../server/index.ts', import.meta.url))], {
    env: { ...process.env, COLLAB_DATABASE: database, PORT: String(port) }, stdio: ['ignore', 'pipe', 'inherit'],
  });
  const exited = once(child, 'exit');
  const reader = createInterface({ input: child.stdout });
  try {
    let origin = '';
    const logins = [];
    for await (const line of reader) {
      if (line.startsWith('共同ノート ')) origin = line.split(' ')[1];
      if (/^(aki|ren|sora) /.test(line)) {
        const [id, password] = line.split(' ');
        logins.push({ id, password });
      }
      if (origin && logins.length === 3) return {
        origin, logins, async close() { child.kill('SIGTERM'); await exited; },
      };
    }
    throw new Error('Application process ended during startup');
  } catch (error) {
    child.kill('SIGTERM');
    await exited;
    throw error;
  } finally { reader.close(); child.stdout.resume(); }
}

async function restart() {
  const f = await browserFixture();
  let app: Awaited<ReturnType<typeof boot>> | undefined;
  try {
    app = await boot(f.databasePath);
    const { context, page: initial } = await f.profile('owner', { viewport, recordVideo: { dir: f.dir, size: viewport } });
    await signIn(initial, app.origin, app.logins[0]);
    const a = await context.newPage();
    const scene = director();
    await a.goto(app.origin);
    await scene(2, '保存を確認\n受理順番号1で「サーバー保存済み」になります。');
    await createDocument(a, '再起動の確認');
    await append(a, 'SQLiteに保存した会議メモ');
    await saved(a, 1);

    await scene(12, 'サーバープロセスを停止\n編集画面には未接続と表示されます。');
    const port = Number(new URL(app.origin).port);
    await app.close();
    await expect(connection(a)).toHaveAttribute('data-connection', 'offline');
    await scene(21, '同じDBで起動\n新しくログインし、保存済みの文書を開きます。');
    app = await boot(f.databasePath, port);
    // The original recorded page keeps the browser view. Its password field is masked.
    await signIn(a, app.origin, app.logins[0]);
    await openDocument(a, '再起動の確認');
    await expect.poll(() => body(a)).toBe('SQLiteに保存した会議メモ');
    await expect(sequence(a)).toHaveText('1');
    await scene(29, '再起動直後に本文を取得\nSQLiteに保存した会議メモと受理順番号1を確認できました。');
    await scene(41, '再起動の確認を終了しました。');
    const path = a.video()!;
    await context.close();
    await render(f.dir, 'restart.mp4', [await path.path()], 41, 'サーバー再起動後の文書復元');
    return { sequence: 1, restoredText: 'SQLiteに保存した会議メモ', captions: clips.slice(0, -1) };
  } finally { await app?.close(); await f.close(); clips.length = 0; }
}

const recording = { recordedAtUtc: new Date().toISOString(), collaboration: await collaboration(), restart: await restart() };
await writeFile(join(output, 'evidence.json'), JSON.stringify(recording, null, 2) + '\n');
