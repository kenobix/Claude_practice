import { chmod, link, mkdtemp, rm } from 'node:fs/promises';
import { dirname, join, resolve } from 'node:path';
import { backup, DatabaseSync } from 'node:sqlite';

const [source, target, ...extra] = process.argv.slice(2);
if (!source || !target || extra.length) throw new Error('Usage: npm run backup -- SOURCE.sqlite NEW_BACKUP.sqlite');

const destination = resolve(target);
const input = new DatabaseSync(resolve(source), { readOnly: true });
try {
  // Validate that this is an application database before creating a backup.
  for (const table of ['documents', 'grants', 'snapshots', 'updates', 'receipts']) {
    input.prepare(`SELECT 1 FROM ${table} LIMIT 1`).get();
  }

  const directory = await mkdtemp(join(dirname(destination), '.collab-backup-'));
  try {
    const copy = join(directory, 'copy.sqlite');
    await backup(input, copy);
    const check = new DatabaseSync(copy);
    try {
      if (check.prepare('PRAGMA journal_mode=DELETE').get()?.journal_mode !== 'delete' ||
          check.prepare('PRAGMA integrity_check').get()?.integrity_check !== 'ok' ||
          check.prepare('PRAGMA foreign_key_check').get()) throw new Error('SQLite backup verification failed');
    } finally {
      check.close();
    }

    await chmod(copy, 0o600);
    // Publish a verified file without replacing a previous backup or the source.
    await link(copy, destination);
    console.log(destination);
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
} finally {
  input.close();
}
