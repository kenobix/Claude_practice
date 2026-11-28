import { createHash, randomBytes, timingSafeEqual } from 'node:crypto';
import type { IncomingMessage } from 'node:http';
import type { User } from '../shared/protocol.ts';

const DEMO_USERS: User[] = [
  { id: 'aki', name: 'アキ' },
  { id: 'ren', name: 'レン' },
  { id: 'sora', name: 'ソラ' },
];
const SESSION_SECONDS = 60 * 60;
type Session = { user: User; expires: number };

function digest(password: string) {
  return createHash('sha256').update(password).digest();
}

export function createAccounts(now = Date.now) {
  const logins = DEMO_USERS.map((user) => ({ ...user, password: randomBytes(12).toString('base64url') }));
  const proofs = new Map(logins.map(({ id, password }) => [id, digest(password)]));
  const sessions = new Map<string, Session>();

  return {
    users: DEMO_USERS,
    logins,
    authenticate(id: unknown, password: unknown) {
      if (typeof id !== 'string' || typeof password !== 'string') return undefined;
      const proof = proofs.get(id);
      if (!proof || !timingSafeEqual(proof, digest(password))) return undefined;
      return DEMO_USERS.find((user) => user.id === id);
    },
    start(user: User) {
      const token = randomBytes(32).toString('hex');
      sessions.set(token, { user, expires: now() + SESSION_SECONDS * 1000 });
      return sessionCookie(token, SESSION_SECONDS);
    },
    get(token: string) {
      const session = sessions.get(token);
      if (session && session.expires <= now()) {
        sessions.delete(token);
        return undefined;
      }
      return session;
    },
    end(token: string) {
      sessions.delete(token);
      return sessionCookie('', 0);
    },
    prune() {
      const time = now();
      for (const [token, session] of sessions) if (session.expires <= time) sessions.delete(token);
    },
  };
}

function sessionCookie(token: string, seconds: number) {
  return `collab_session=${token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=${seconds}`;
}

export function sessionToken(request: IncomingMessage) {
  return /(?:^|;\s*)collab_session=([a-f0-9]{64})(?:;|$)/.exec(request.headers.cookie ?? '')?.[1] ?? '';
}
