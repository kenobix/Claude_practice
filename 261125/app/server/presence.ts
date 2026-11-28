import * as decoding from 'lib0/decoding';
import * as encoding from 'lib0/encoding';
import { isRecord, isUint, MAX_AWARENESS } from '../shared/protocol.ts';

type Point = { type: null; tname: string | null; item: { client: number; clock: number } | null; assoc: number };
type Cursor = { anchor: Point; head: Point };
type Presence = { user: { id: string; name: string; color: string; colorLight: string }; cursor?: Cursor };
export type PresenceEntry = { id: number; clock: number; state: Presence | null };

function item(value: unknown) {
  if (!isRecord(value) || !isUint(value.client) || !isUint(value.clock)) throw new Error('Invalid cursor item');
  return { client: value.client, clock: value.clock };
}

function point(value: unknown): Point {
  if (!isRecord(value) || (value.assoc !== 0 && value.assoc !== -1 && value.assoc !== 1)) {
    throw new Error('Invalid cursor point');
  }
  if (value.type != null || (value.tname != null && value.tname !== 'body') || (value.tname == null && value.item == null)) {
    throw new Error('Invalid cursor target');
  }
  return {
    type: null,
    tname: value.tname === 'body' ? 'body' : null,
    item: value.item == null ? null : item(value.item),
    assoc: value.assoc,
  };
}

export function readCursor(bytes: Uint8Array, expectedId: number): Cursor | undefined {
  if (bytes.length > MAX_AWARENESS) throw new Error('Awareness too large');
  const decoder = decoding.createDecoder(bytes);
  const count = decoding.readVarUint(decoder);
  const id = decoding.readVarUint(decoder);
  if (count !== 1 || id !== expectedId) throw new Error('Invalid awareness ID');
  decoding.readVarUint(decoder); // The server maintains the clock it advertises.
  const state: unknown = JSON.parse(decoding.readVarString(decoder));
  if (decoding.hasContent(decoder) || (state !== null && !isRecord(state))) throw new Error('Invalid awareness state');
  if (!isRecord(state) || state.cursor == null) return undefined;
  if (!isRecord(state.cursor)) throw new Error('Invalid cursor');
  return { anchor: point(state.cursor.anchor), head: point(state.cursor.head) };
}

export function encodePresence(entries: PresenceEntry[]) {
  const encoder = encoding.createEncoder();
  encoding.writeVarUint(encoder, entries.length);
  for (const entry of entries) {
    encoding.writeVarUint(encoder, entry.id);
    encoding.writeVarUint(encoder, entry.clock);
    encoding.writeVarString(encoder, JSON.stringify(entry.state));
  }
  return encoding.toUint8Array(encoder);
}
