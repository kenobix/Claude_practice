import { access } from 'node:fs/promises';
import { startApplication } from './application.ts';

const port = Number(process.env.PORT ?? '4173');
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('PORT must be an integer from 0 to 65535');
await access(new URL('../dist/index.html', import.meta.url));
const app = await startApplication(port);
console.log(`共同ノート ${app.origin}`);
for (const { id, password } of app.logins) console.log(`${id} ${password}`);
process.once('SIGINT', () => { void app.close(); });
process.once('SIGTERM', () => { void app.close(); });
