export const VERSION = 2;
export const MAX_BODY = 100_000;
export const MAX_PAYLOAD = 8 * 1024 * 1024;
const MAX_HEADER = 1024;
export const MAX_FRAME = MAX_PAYLOAD + MAX_HEADER;
export const MAX_AWARENESS = 4096;
export const MAX_CONNECTIONS = 3;

export type Role = 'owner' | 'editor' | 'viewer';
export type Access = Exclude<Role, 'owner'> | null;
export type User = { id: string; name: string };
export type DocumentSummary = {
  id: string;
  title: string;
  role: Role;
  access?: Record<string, Access>;
};

export type Message =
  | { type: 'HELLO'; version: number; client_id: string; awareness_id: number }
  | { type: 'STATE'; version: number; role: Role; acl_revision: number; grant_revision: number; sequence: number }
  | { type: 'UPDATE'; client_id: string; message_id: string; grant_revision: number }
  | { type: 'APPLIED'; sequence: number }
  | { type: 'ACK'; client_id: string; message_id: string; sequence: number }
  | { type: 'AWARENESS' }
  | { type: 'ERROR'; code: string; message_id?: string };

const encoder = new TextEncoder();
const decoder = new TextDecoder('utf-8', { fatal: true });

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isRole(value: unknown): value is Role {
  return value === 'owner' || value === 'editor' || value === 'viewer';
}

export function isUint(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
}

export function isId(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value);
}

export function encodeFrame(header: Message, payload: Uint8Array = new Uint8Array()): Uint8Array<ArrayBuffer> {
  const json = encoder.encode(JSON.stringify(header));
  if (json.length + 2 > MAX_HEADER || payload.length > MAX_PAYLOAD) throw new Error('Frame too large');
  const frame = new Uint8Array(2 + json.length + payload.length);
  new DataView(frame.buffer).setUint16(0, json.length);
  frame.set(json, 2);
  frame.set(payload, 2 + json.length);
  return frame;
}

export function decodeFrame(frame: Uint8Array) {
  if (frame.length < 3 || frame.length > MAX_FRAME) throw new Error('Invalid frame length');
  const end = 2 + new DataView(frame.buffer, frame.byteOffset, frame.byteLength).getUint16(0);
  if (end > MAX_HEADER || end > frame.length) throw new Error('Invalid header length');
  const header: unknown = JSON.parse(decoder.decode(frame.subarray(2, end)));
  if (!isRecord(header)) throw new Error('Invalid frame header');
  return { header, payload: frame.subarray(end) };
}
