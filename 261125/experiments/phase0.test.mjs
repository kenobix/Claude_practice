import assert from 'node:assert/strict';
import { Buffer } from 'node:buffer';
import { test } from 'node:test';
import * as Y from 'yjs';

import { increment, merge, total } from './g-counter.mjs';

function createReplica(t, id) {
  const doc = new Y.Doc();
  // Fixed client IDs make the order of concurrent insertions reproducible.
  doc.clientID = id;
  t.after(() => doc.destroy());
  return { doc, text: doc.getText('body') };
}

function captureUpdate(replica, edit) {
  let update;
  replica.doc.once('update', (bytes) => { update = bytes; });
  replica.doc.transact(() => edit(replica.text));
  assert.ok(update, 'the edit emitted one Yjs update');
  return update;
}

test('G-Counter merges by replica and ignores duplicate and older states', () => {
  const a1 = increment(new Map(), 'A');
  const a2 = increment(a1, 'A');
  const b1 = increment(new Map(), 'B');
  const c1 = increment(new Map(), 'C');
  const expected = new Map([['A', 2], ['B', 1], ['C', 1]]);

  const merged = merge(merge(merge(a2, b1), a1), c1);
  assert.deepEqual(merged, expected);
  assert.deepEqual(merge(c1, merge(b1, a2)), expected);
  assert.deepEqual(merge(merged, merged), expected);
  assert.equal(total(merged), 4);
  assert.equal(total(a1), 1);
});

test('three Y.Text replicas converge after reverse, duplicate and delayed delivery', (t) => {
  const initial = createReplica(t, 1);
  initial.text.insert(0, 'abcd');
  const snapshot = Y.encodeStateAsUpdate(initial.doc);
  const replicas = { A: createReplica(t, 10), B: createReplica(t, 20), C: createReplica(t, 30) };
  for (const replica of Object.values(replicas)) Y.applyUpdate(replica.doc, snapshot);

  const updates = {
    A1: captureUpdate(replicas.A, (text) => {
      text.insert(2, 'A');
      text.delete(1, 1);
    }),
    A2: captureUpdate(replicas.A, (text) => text.insert(2, '!')),
    B1: captureUpdate(replicas.B, (text) => {
      text.insert(2, 'B');
      text.delete(3, 1);
    }),
    C1: captureUpdate(replicas.C, (text) => {
      text.insert(2, 'C');
      text.delete(1, 1);
    }),
  };
  const received = { A: ['A1', 'A2'], B: ['B1'], C: ['C1'] };
  function deliver(name, ...ids) {
    for (const id of ids) {
      Y.applyUpdate(replicas[name].doc, updates[id]);
      received[name].push(id);
    }
  }
  function observe() {
    return Object.fromEntries(Object.entries(replicas).map(([name, { text }]) => [name, text.toString()]));
  }

  deliver('A', 'B1', 'C1', 'B1');
  deliver('B', 'A2', 'C1', 'A2');
  deliver('C', 'B1', 'A2', 'B1');
  const before = observe();
  assert.deepEqual(before, { A: 'aA!BCd', B: 'aBCd', C: 'aBCd' });
  for (const { doc } of [replicas.B, replicas.C]) {
    assert.equal(Y.decodeStateVector(Y.encodeStateVector(doc)).get(10), undefined);
    assert.ok(doc.store.pendingStructs, 'A2 awaits A1');
  }
  t.diagnostic(`before release: ${JSON.stringify({ received, text: before })}`);

  deliver('B', 'A1');
  deliver('C', 'A1');
  const after = { A: 'aA!BCd', B: 'aA!BCd', C: 'aA!BCd' };
  const expectedVector = new Map([[1, 4], [10, 2], [20, 1], [30, 1]]);
  assert.deepEqual(observe(), after);
  for (const { doc } of Object.values(replicas)) {
    assert.deepEqual(Y.decodeStateVector(Y.encodeStateVector(doc)), expectedVector);
    assert.equal(doc.store.pendingStructs, null);
    assert.equal(doc.store.pendingDs, null);
  }
  t.diagnostic(`after release: ${JSON.stringify({ received, text: observe() })}`);

  let appliedUpdates = 0;
  for (const { doc } of Object.values(replicas)) {
    doc.on('update', () => { appliedUpdates += 1; });
    for (const resolved of Object.values(replicas)) {
      if (doc === resolved.doc) continue;
      Y.applyUpdate(doc, Y.encodeStateAsUpdate(resolved.doc, Y.encodeStateVector(doc)));
    }
  }
  assert.deepEqual(observe(), after);
  assert.equal(appliedUpdates, 0);
});

test('equal state vectors can miss a deletion and a Yjs update repairs it', (t) => {
  const writer = createReplica(t, 1);
  const reader = createReplica(t, 2);
  writer.text.insert(0, 'abc');
  const initial = Y.encodeStateAsUpdate(writer.doc);
  Y.applyUpdate(reader.doc, initial);

  const deleteOnly = captureUpdate(writer, (text) => text.delete(1, 1));
  const readerVector = Y.encodeStateVector(reader.doc);
  assert.deepEqual(Y.encodeStateVector(writer.doc), readerVector);
  assert.equal(writer.text.toString(), 'ac');
  assert.equal(reader.text.toString(), 'abc');
  Y.applyUpdate(reader.doc, Y.encodeStateAsUpdate(writer.doc, readerVector));
  assert.equal(reader.text.toString(), 'ac');

  const lateReader = createReplica(t, 3);
  Y.applyUpdate(lateReader.doc, deleteOnly);
  assert.equal(lateReader.text.toString(), '');
  assert.ok(lateReader.doc.store.pendingDs, 'the delete awaits the inserted item');
  Y.applyUpdate(lateReader.doc, initial);
  assert.equal(lateReader.text.toString(), 'ac');
  assert.equal(lateReader.doc.store.pendingDs, null);
  t.diagnostic('equal vectors: abc -> ac; delete delivered before insert: ac');
});

test('UTF-16 units, UTF-8 bytes and encoded Yjs state have different limits', (t) => {
  const samples = [
    { label: 'ASCII', glyph: 'a', count: 100_000, utf8Bytes: 100_000 },
    { label: 'CJK', glyph: '漢', count: 100_000, utf8Bytes: 300_000 },
    { label: 'emoji', glyph: '😀', count: 50_000, utf8Bytes: 200_000 },
  ];
  const maxPayload = 8 * 1024 * 1024;
  for (const [index, { label, glyph, count, utf8Bytes }] of samples.entries()) {
    const replica = createReplica(t, index + 1);
    const input = glyph.repeat(count);
    const update = captureUpdate(replica, (text) => text.insert(0, input));
    const state = Y.encodeStateAsUpdate(replica.doc);
    assert.equal(replica.text.length, 100_000);
    assert.equal(Buffer.byteLength(input), utf8Bytes);
    assert.ok((input + 'a').length > 100_000);
    assert.ok(update.byteLength <= maxPayload);
    assert.ok(state.byteLength <= maxPayload);

    const restored = createReplica(t, index + 4);
    Y.applyUpdate(restored.doc, state);
    assert.equal(restored.text.toString(), input);
    t.diagnostic(`${label}: units=${replica.text.length} utf8=${utf8Bytes} update=${update.byteLength} state=${state.byteLength}`);
  }

  const boundaryText = '漢'.repeat(2_796_197) + 'a';
  for (const [index, suffix] of ['', 'a'].entries()) {
    const replica = createReplica(t, index + 7);
    const input = boundaryText + suffix;
    const update = captureUpdate(replica, (text) => text.insert(0, input));
    const state = Y.encodeStateAsUpdate(replica.doc);
    assert.equal(update.byteLength, maxPayload + index);
    assert.equal(state.byteLength, maxPayload + index);
    t.diagnostic(`binary boundary: units=${replica.text.length} utf8=${Buffer.byteLength(input)} update=${update.byteLength} state=${state.byteLength}`);
  }

  const replica = createReplica(t, 9);
  replica.text.insert(0, boundaryText);
  replica.text.delete(0, replica.text.length);
  const blank = createReplica(t, 10);
  const deletedStateBytes = Y.encodeStateAsUpdate(replica.doc).byteLength;
  const blankStateBytes = Y.encodeStateAsUpdate(blank.doc).byteLength;
  assert.equal(replica.text.length, 0);
  assert.ok(deletedStateBytes > blankStateBytes);
  t.diagnostic(`empty text: deleted state=${deletedStateBytes} blank state=${blankStateBytes}`);
});
