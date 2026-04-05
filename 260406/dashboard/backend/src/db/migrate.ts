import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { mkdirSync } from 'fs';
import { dirname } from 'path';

const dbPath = process.env.DB_PATH || './data/dashboard.db';

mkdirSync(dirname(dbPath), { recursive: true });

const sqlite = new Database(dbPath);
sqlite.pragma('journal_mode = WAL');
sqlite.pragma('foreign_keys = ON');

const db = drizzle(sqlite);

console.log('Running migrations...');
migrate(db, { migrationsFolder: './drizzle' });
console.log('Migrations complete.');

sqlite.close();
