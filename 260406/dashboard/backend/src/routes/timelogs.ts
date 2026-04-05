import { Hono } from 'hono';
import { eq, and, sql } from 'drizzle-orm';
import db from '../db/index.js';
import { time_logs } from '../db/schema.js';

const router = new Hono();

// GET /api/timelogs?task_id=&date=YYYY-MM-DD
router.get('/', async (c) => {
  try {
    const taskIdParam = c.req.query('task_id');
    const dateParam = c.req.query('date');

    const conditions = [];

    if (taskIdParam) {
      const taskId = Number(taskIdParam);
      if (!isNaN(taskId)) {
        conditions.push(eq(time_logs.task_id, taskId));
      }
    }

    if (dateParam) {
      conditions.push(sql`${time_logs.started_at} LIKE ${dateParam + '%'}`);
    }

    const result =
      conditions.length > 0
        ? await db.select().from(time_logs).where(and(...conditions)).all()
        : await db.select().from(time_logs).all();

    return c.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

export default router;
