import { on, once } from 'node:events';
import type { BrowserContext } from '@playwright/test';
import { WebSocket } from 'ws';
import { decodeFrame, encodeFrame, VERSION } from '../shared/protocol.ts';

export async function probe(context: BrowserContext, origin: string, id: string, clientId: string,
  check: (socket: WebSocket, next: (type: string) => Promise<ReturnType<typeof decodeFrame>>) => Promise<void>) {
  const cookie = (await context.cookies(origin)).map(({ name, value }) => `${name}=${value}`).join('; ');
  const socket = new WebSocket(`${origin.replace('http:', 'ws:')}/ws/${id}`, { origin, headers: { Cookie: cookie } });
  const messages = on(socket, 'message', { signal: AbortSignal.timeout(10_000) });
  try {
    await once(socket, 'open');
    socket.send(encodeFrame({ type: 'HELLO', version: VERSION, client_id: clientId, awareness_id: 77 }));
    await check(socket, async (type) => {
      for (;;) {
        const event = await messages.next();
        if (event.done) throw new Error(`Missing ${type}`);
        const frame = decodeFrame(event.value[0]);
        if (frame.header.type === type) return frame;
        if (frame.header.type !== 'AWARENESS') throw new Error(`Expected ${type}, received ${String(frame.header.type)}`);
      }
    });
  } finally {
    socket.terminate();
    await messages.return?.();
  }
}
