import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdir, stat, writeFile } from 'node:fs/promises';
import { arch, cpus, release } from 'node:os';
import { join, resolve } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import { setTimeout as pause } from 'node:timers/promises';
import { expect } from '@playwright/test';
import { append, browserFixture, connection, createDocument, grant, saveStatus, sequence, watchWire } from './browser.ts';
import { body, openDocument, signIn } from '../tests/browser-helpers.ts';

const seed = 261125;
const trials = 5;
const updates = 100;
const base = 'a'.repeat(1024);
const intervalMs = 110;
const blockLength = 16;
const title = '性能計測';
const out = process.argv[2];
if (!out || process.argv.length !== 3) throw new Error('Usage: npm run measure -- NEW_RESULTS_DIR');
const output = resolve(out);
await mkdir(output, { mode: 0o700 });

function blocks() {
  let state = seed;
  return Array.from({ length: updates + 10 }, (_, index) => {
    state ^= state << 13;
    state ^= state >>> 17;
    state ^= state << 5;
    return `${String(index).padStart(3, '0')}|${(state >>> 0).toString(16).padStart(8, '0')}|+;!`;
  });
}

async function footprint(path: string) {
  const db = new DatabaseSync(path, { readOnly: true });
  try {
    const value = (name: string) => Number(db.prepare(`PRAGMA ${name}`).get()?.[name]);
    async function size(suffix: string) {
      return stat(path + suffix).then(({ size }) => size, (e: NodeJS.ErrnoException) => {
        if (e.code !== 'ENOENT') throw e;
        return 0;
      });
    }
    const mainBytes = await size('');
    const walBytes = await size('-wal');
    const shmBytes = await size('-shm');
    return {
      pageSize: value('page_size'), pages: value('page_count'), freePages: value('freelist_count'),
      mainBytes, walBytes, shmBytes, mainPlusWalBytes: mainBytes + walBytes,
      logicalBytes: value('page_size') * value('page_count'),
    };
  } finally { db.close(); }
}

type Wire = Parameters<NonNullable<Parameters<typeof watchWire>[1]>>[0] & { profile: 'A' | 'B' };

async function trial(index: number) {
  const f = await browserFixture();
  try {
    const input = blocks();
    assert.ok(input.every((block) => block.length === blockLength));
    const app = await f.start();
    const [{ page: a, context }, { page: b }] = await Promise.all([f.profile('owner'), f.profile('editor')]);
    const frames: Wire[] = [];
    await watchWire(a, (frame) => frames.push({ profile: 'A', ...frame }));
    const tap = await watchWire(b, (frame) => frames.push({ profile: 'B', ...frame }));
    const cdp = await context.newCDPSession(a);
    const { product } = await cdp.send('Browser.getVersion');
    await Promise.all([signIn(a, app.origin, app.logins[0]), signIn(b, app.origin, app.logins[1])]);
    await createDocument(a, title);
    await grant(a, 'レン', 'editor');
    await openDocument(b, title);
    await append(a, base);
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await expect.poll(() => body(b)).toBe(base);
    const before = await footprint(f.databasePath);
    const startedAtUtc = new Date().toISOString();
    const zero = performance.now();
    const onlineStart = frames.length;

    for (const next of input.slice(0, updates)) {
      await append(a, next);
      await pause(intervalMs);
    }
    const expected = base + input.slice(0, updates).join('');
    await expect.poll(() => body(b)).toBe(expected);
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await expect(sequence(a)).toHaveText('101');
    await expect(sequence(b)).toHaveText('101');
    const onlineEnd = frames.length;
    const afterOnline = await footprint(f.databasePath);

    const online = frames.slice(onlineStart, onlineEnd);
    const operations = online.filter((frame) => frame.profile === 'A' && frame.direction === 'sent' && frame.header.type === 'UPDATE');
    assert.equal(operations.length, updates);
    const samples = operations.map(({ header, bytes, at }, offset) => {
      const seq = offset + 2;
      const arrived = online.find((frame) => frame.profile === 'B' && frame.direction === 'received' &&
        frame.header.type === 'APPLIED' && frame.header.sequence === seq);
      const ack = online.find((frame) => frame.profile === 'A' && frame.direction === 'received' &&
        frame.header.type === 'ACK' && frame.header.message_id === header.message_id && frame.header.sequence === seq);
      assert.ok(arrived && ack && arrived.at >= at && ack.at >= at);
      return {
        sequence: seq, sentMs: at - zero, deliveryMs: arrived.at - at, ackMs: ack.at - at,
        updateBytes: bytes, appliedBytes: arrived.bytes, ackBytes: ack.bytes,
      };
    });

    const disconnectedStart = frames.length;
    tap.cut();
    await expect(connection(b)).toHaveAttribute('data-connection', 'offline');
    for (let i = 0; i < 5; i += 1) {
      await append(a, input[updates + i]);
      await append(b, input[updates + 5 + i]);
      await pause(intervalMs);
    }
    await expect(sequence(a)).toHaveText('106');
    await expect(saveStatus(a)).toHaveText('サーバー保存済み');
    await expect(saveStatus(b)).toHaveText('サーバー保存待ち');
    await expect(b.locator('[data-local-save]')).toHaveText('端末内保存済み');

    const reconnectStart = frames.length;
    const attempt = performance.now();
    await b.getByRole('button', { name: '接続し直す' }).click();
    await expect(sequence(a)).toHaveText('111');
    await expect(sequence(b)).toHaveText('111');
    await expect(saveStatus(b)).toHaveText('サーバー保存済み');
    const [left, right] = await Promise.all([body(a), body(b)]);
    const visible = performance.now();
    assert.equal(left, right);
    assert.equal(left.length, base.length + (updates + 10) * blockLength);
    assert.ok(input.every((block) => left.includes(block)));
    const reconnected = frames.slice(reconnectStart);
    const hello = reconnected.find((frame) => frame.profile === 'B' && frame.direction === 'sent' && frame.header.type === 'HELLO');
    const state = reconnected.find((frame) => frame.profile === 'B' && frame.direction === 'received' && frame.header.type === 'STATE');
    const delivered = reconnected.find((frame) => frame.profile === 'A' && frame.direction === 'received' &&
      frame.header.type === 'APPLIED' && frame.header.sequence === 111);
    const lastAck = reconnected.find((frame) => frame.profile === 'B' && frame.direction === 'received' &&
      frame.header.type === 'ACK' && frame.header.sequence === 111);
    assert.ok(hello && state && delivered && lastAck);

    const database = { before, afterOnline, afterReconnect: await footprint(f.databasePath) };
    return {
      index, startedAtUtc, browser: product, samples,
      reconnect: {
        stateSequence: state.header.sequence, finalSequence: 111,
        helloMs: hello.at - attempt, stateMs: state.at - attempt, peerAppliedMs: delivered.at - attempt,
        lastAckMs: lastAck.at - attempt, visibleAndSavedMs: visible - attempt,
        bodyUtf16: left.length, bodySha256: createHash('sha256').update(left).digest('hex'),
      },
      database,
      frames: frames.slice(onlineStart).map(({ profile, direction, header, bytes, at }, i) => ({
        phase: onlineStart + i < disconnectedStart ? 'online' : onlineStart + i < reconnectStart ? 'disconnected' : 'reconnect',
        profile, direction, type: header.type, sequence: header.sequence, bytes, ms: at - zero,
      })),
    };
  } finally { await f.close(); }
}

function stats(values: number[]) {
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return { n: sorted.length, min: sorted[0], median: sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2,
    p95: sorted[Math.ceil(0.95 * sorted.length) - 1], max: sorted.at(-1) };
}

const runs: Awaited<ReturnType<typeof trial>>[] = [];
for (let index = 1; index <= trials; index += 1) {
  const result = await trial(index);
  runs.push(result);
  console.log(`Trial ${index}: ${result.samples.length} updates, final sequence ${result.reconnect.finalSequence}`);
}

const db = new DatabaseSync(':memory:');
const sqlite = db.prepare('SELECT sqlite_version() AS version').get()?.version;
db.close();
const metadata = {
  measuredAtUtc: runs[0].startedAtUtc, node: process.version, sqlite, kernel: release(), arch: arch(), cpu: cpus()[0]?.model,
  logicalCpuCount: cpus().length, browser: runs[0].browser, seed, trials, onlineUpdatesPerTrial: updates,
  initialBodyUtf16: base.length, updateBodyUtf16: blockLength, pacingWaitMs: intervalMs, disconnectedUpdatesPerProfile: 5,
};
const all = runs.flatMap(({ samples }) => samples);
const summary = {
  ...metadata,
  deliveryMs: stats(all.map(({ deliveryMs }) => deliveryMs)),
  ackMs: stats(all.map(({ ackMs }) => ackMs)),
  reconnectPeerAppliedMs: stats(runs.map(({ reconnect }) => reconnect.peerAppliedMs)),
  reconnectLastAckMs: stats(runs.map(({ reconnect }) => reconnect.lastAckMs)),
  reconnectVisibleAndSavedMs: stats(runs.map(({ reconnect }) => reconnect.visibleAndSavedMs)),
  onlineWireBytes: runs.map(({ frames }) => Object.fromEntries(['A', 'B'].map((profile) => [profile, Object.fromEntries(
    ['sent', 'received'].map((direction) => [direction, frames.filter((f) =>
      f.profile === profile && f.direction === direction && f.phase === 'online').reduce((sum, f) => sum + f.bytes, 0)]),
  )]))),
  reconnectWireBytes: runs.map(({ frames }) => Object.fromEntries(['A', 'B'].map((profile) => [profile, Object.fromEntries(
    ['sent', 'received'].map((direction) => [direction, frames.filter((f) =>
      f.profile === profile && f.direction === direction && f.phase === 'reconnect').reduce((sum, f) => sum + f.bytes, 0)]),
  )]))),
  onlineDatabaseGrowth: runs.map(({ database: { before, afterOnline } }) => ({
    logicalBytes: afterOnline.logicalBytes - before.logicalBytes,
    mainPlusWalBytes: afterOnline.mainPlusWalBytes - before.mainPlusWalBytes,
  })),
};
await writeFile(join(output, 'raw.json'), JSON.stringify({ metadata, runs }, null, 2) + '\n');
await writeFile(join(output, 'summary.json'), JSON.stringify(summary, null, 2) + '\n');
