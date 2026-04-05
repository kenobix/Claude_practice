import Database from 'better-sqlite3';
import { drizzle } from 'drizzle-orm/better-sqlite3';
import { migrate } from 'drizzle-orm/better-sqlite3/migrator';
import { mkdirSync } from 'fs';
import { dirname } from 'path';
import * as schema from './schema.js';

const dbPath = process.env.DB_PATH || './data/dashboard.db';

// Ensure the data directory exists
mkdirSync(dirname(dbPath), { recursive: true });

const sqlite = new Database(dbPath);

// Enable WAL mode for better concurrent read performance
sqlite.pragma('journal_mode = WAL');
sqlite.pragma('foreign_keys = ON');

export const db = drizzle(sqlite, { schema });

// Run migrations on startup
try {
  migrate(db, { migrationsFolder: './drizzle' });
  console.log('Database migrations applied successfully.');
} catch (err) {
  console.warn('Migration warning (may be safe to ignore if tables already exist):', err);
}

export default db;
