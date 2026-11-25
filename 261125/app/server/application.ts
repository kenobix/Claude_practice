import { STATUS_CODES, createServer } from 'node:http';
import type { IncomingMessage, ServerResponse } from 'node:http';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { isRecord } from '../shared/protocol.ts';
import { createAccounts, sessionToken } from './accounts.ts';
import { createRooms } from './rooms.ts';
import type { StorageOptions } from './storage.ts';

const HOST = '127.0.0.1';
const DIST = fileURLToPath(new URL('../dist/', import.meta.url));
const MAX_JSON = 4096;

class HttpError extends Error {
  readonly status: number;

  constructor(status: number, code: string) {
    super(code);
    this.status = status;
  }
}

function json(response: ServerResponse, status: number, body: unknown) {
  response.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
  response.end(JSON.stringify(body));
}

async function readJson(request: IncomingMessage) {
  if (request.headers['content-type']?.split(';')[0] !== 'application/json') throw new HttpError(415, 'INVALID_REQUEST');
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of request.iterator({ destroyOnReturn: false })) {
    total += chunk.length;
    if (total > MAX_JSON) {
      request.resume();
      throw new HttpError(413, 'INVALID_REQUEST');
    }
    chunks.push(chunk);
  }
  let body: unknown;
  try {
    body = JSON.parse(Buffer.concat(chunks).toString('utf8'));
  } catch {
    throw new HttpError(400, 'INVALID_REQUEST');
  }
  if (!isRecord(body)) throw new HttpError(400, 'INVALID_REQUEST');
  return body;
}

async function staticPage(path: string, response: ServerResponse, origin: string) {
  const resource = path === '/' ? 'index.html' : /^\/assets\/[a-zA-Z0-9][a-zA-Z0-9_.-]*\.(?:js|css)$/.test(path) ? path.slice(1) : null;
  if (!resource) throw new HttpError(404, 'NOT_FOUND');
  const bytes = await readFile(resolve(DIST, resource)).catch((error: NodeJS.ErrnoException) => {
    if (error.code === 'ENOENT') throw new HttpError(404, 'NOT_FOUND');
    throw error;
  });
  const type = resource.endsWith('.js') ? 'text/javascript' : resource.endsWith('.css') ? 'text/css' : 'text/html';
  response.writeHead(200, {
    'Content-Type': `${type}; charset=utf-8`,
    'Cache-Control': resource === 'index.html' ? 'no-store' : 'public, max-age=31536000, immutable',
    'Content-Security-Policy': `default-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ${origin.replace('http:', 'ws:')}; base-uri 'none'; frame-ancestors 'none'`,
  });
  response.end(bytes);
}

export async function startApplication({ port = 0, ...storage }: StorageOptions & { port?: number }) {
  const accounts = createAccounts();
  const rooms = createRooms((token) => Boolean(accounts.get(token)), accounts.prune, storage);
  let origin = '';
  let host = '';

  async function route(request: IncomingMessage, response: ServerResponse) {
    response.setHeader('X-Content-Type-Options', 'nosniff');
    if (request.headers.host !== host) throw new HttpError(403, 'ORIGIN_DENIED');
    const url = new URL(request.url ?? '/', origin);
    if (url.origin !== origin) throw new HttpError(403, 'ORIGIN_DENIED');
    const path = url.pathname;
    const method = request.method;
    if (!path.startsWith('/api/')) {
      if (method !== 'GET') throw new HttpError(404, 'NOT_FOUND');
      return staticPage(path, response, origin);
    }
    if (method !== 'GET' && request.headers.origin !== origin) throw new HttpError(403, 'ORIGIN_DENIED');
    const token = sessionToken(request);
    const session = accounts.get(token);

    if (path === '/api/session') {
      if (method === 'GET') return json(response, 200, { user: session?.user ?? null, accounts: accounts.users });
      if (method === 'POST') {
        const { userId, password } = await readJson(request);
        const user = accounts.authenticate(userId, password);
        if (!user) throw new HttpError(401, 'LOGIN_FAILED');
        accounts.end(token);
        rooms.closeSession(token);
        response.setHeader('Set-Cookie', accounts.start(user));
        return json(response, 200, { user });
      }
      if (method === 'DELETE') {
        response.setHeader('Set-Cookie', accounts.end(token));
        rooms.closeSession(token);
        return json(response, 200, { user: null });
      }
    }

    if (!session) throw new HttpError(401, 'SESSION_EXPIRED');
    if (!rooms.available) throw new HttpError(503, 'STORAGE_RECOVERY');
    if (path === '/api/documents') {
      if (method === 'GET') return json(response, 200, { documents: rooms.list(session.user.id) });
      if (method === 'POST') {
        const { title } = await readJson(request);
        if (accounts.get(token) !== session) throw new HttpError(401, 'SESSION_EXPIRED');
        if (typeof title !== 'string' || title.trim().length === 0 || title.trim().length > 80) {
          throw new HttpError(400, 'INVALID_TITLE');
        }
        return json(response, 201, { document: rooms.create(session.user, title.trim()) });
      }
    }

    const access = /^\/api\/documents\/([0-9a-f-]+)\/access\/([a-z]+)$/.exec(path);
    if (access && method === 'PUT') {
      const [, documentId, target] = access;
      const { role } = await readJson(request);
      if (accounts.get(token) !== session) throw new HttpError(401, 'SESSION_EXPIRED');
      if (!accounts.users.some((user) => user.id === target) || (role !== 'editor' && role !== 'viewer' && role !== null)) {
        throw new HttpError(400, 'INVALID_REQUEST');
      }
      const document = rooms.setAccess(documentId, session.user.id, target, role);
      if (!document) throw new HttpError(403, 'ACCESS_DENIED');
      return json(response, 200, { document });
    }
    throw new HttpError(404, 'NOT_FOUND');
  }

  const server = createServer({ requestTimeout: 15_000, headersTimeout: 10_000 }, (request, response) => {
    void route(request, response).catch((error: unknown) => {
      request.resume();
      if (error instanceof HttpError) json(response, error.status, { error: error.message });
      else json(response, 500, { error: 'SERVER_ERROR' });
    });
  });
  server.on('upgrade', (request, socket, head) => {
    const path = /^\/ws\/([0-9a-f-]+)$/.exec(request.url ?? '');
    const token = sessionToken(request);
    const session = accounts.get(token);
    let status: number | undefined;
    if (!path) status = 404;
    else if (request.headers.host !== host || request.headers.origin !== origin) status = 403;
    else if (!session) status = 401;
    else status = rooms.upgrade(request, socket, head, path[1], token, session.user);
    if (status) socket.end(`HTTP/1.1 ${status} ${STATUS_CODES[status]}\r\nConnection: close\r\nContent-Length: 0\r\n\r\n`);
  });

  try {
    await new Promise<void>((done, fail) => {
      server.once('error', fail);
      server.listen(port, HOST, () => {
        server.off('error', fail);
        const address = server.address();
        if (!address || typeof address === 'string') return fail(new Error('Missing TCP address'));
        host = `${HOST}:${address.port}`;
        origin = `http://${host}`;
        done();
      });
    });
  } catch (error) {
    rooms.close();
    throw error;
  }
  return {
    origin,
    logins: accounts.logins,
    close() {
      rooms.close();
      return new Promise<void>((done) => server.close(() => done()));
    },
  };
}
