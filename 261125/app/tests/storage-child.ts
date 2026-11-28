import { existsSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { startApplication } from '../server/application.ts';
import type { Checkpoint } from '../server/storage.ts';

const [databasePath, port, directory, point, mode, interval] = process.argv.slice(2);
let used = false;
function checkpoint(at: Checkpoint) {
  if (at !== point || used) return;
  used = true;
  writeFileSync(join(directory, 'reached'), at);
  if (mode === 'throw') throw new Error('Injected failure after SQLite committed');
  const wait = new Int32Array(new SharedArrayBuffer(4));
  const deadline = Date.now() + 12_000;
  while (!existsSync(join(directory, 'resume'))) {
    if (Date.now() >= deadline) throw new Error(`Checkpoint timed out: ${at}`);
    Atomics.wait(wait, 0, 0, 10);
  }
}

const app = await startApplication({ databasePath, port: Number(port), checkpoint, snapshotEvery: Number(interval) });
process.send?.({ origin: app.origin, logins: app.logins });
process.on('disconnect', () => { void app.close(); });
