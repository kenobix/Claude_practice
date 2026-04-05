import { Hono } from 'hono';
import { cors } from 'hono/cors';
import { serve } from '@hono/node-server';

// Initialize DB connection and run migrations on startup
import './db/index.js';

import projectsRouter from './routes/projects.js';
import tasksRouter from './routes/tasks.js';
import timelogsRouter from './routes/timelogs.js';
import notesRouter from './routes/notes.js';
import dashboardRouter from './routes/dashboard.js';

const app = new Hono();

// CORS middleware - allow all origins for local development
app.use(
  '*',
  cors({
    origin: '*',
    allowMethods: ['GET', 'POST', 'PUT', 'DELETE', 'OPTIONS'],
    allowHeaders: ['Content-Type', 'Authorization'],
  })
);

// Health check
app.get('/health', (c) => {
  return c.json({ status: 'ok', timestamp: new Date().toISOString() });
});

// API routes
app.route('/api/projects', projectsRouter);
app.route('/api/tasks', tasksRouter);
app.route('/api/timelogs', timelogsRouter);
app.route('/api/notes', notesRouter);
app.route('/api/dashboard', dashboardRouter);

// 404 fallback
app.notFound((c) => {
  return c.json({ error: 'Not found' }, 404);
});

// Global error handler
app.onError((err, c) => {
  console.error('Unhandled error:', err);
  return c.json({ error: err.message || 'Internal server error' }, 500);
});

const PORT = Number(process.env.PORT) || 3001;

serve(
  {
    fetch: app.fetch,
    port: PORT,
  },
  (info) => {
    console.log(`Dashboard backend running on http://localhost:${info.port}`);
    console.log(`Health check: http://localhost:${info.port}/health`);
  }
);

export default app;
