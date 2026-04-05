import { Hono } from 'hono';
import { eq, and, isNull } from 'drizzle-orm';
import db from '../db/index.js';
import { tasks, time_logs } from '../db/schema.js';

const router = new Hono();

// GET /api/tasks?project_id=&status=&priority=
router.get('/', async (c) => {
  try {
    const projectIdParam = c.req.query('project_id');
    const statusParam = c.req.query('status');
    const priorityParam = c.req.query('priority');

    const conditions = [];

    if (projectIdParam) {
      const projectId = Number(projectIdParam);
      if (!isNaN(projectId)) {
        conditions.push(eq(tasks.project_id, projectId));
      }
    }

    if (statusParam) {
      conditions.push(eq(tasks.status, statusParam as 'todo' | 'in_progress' | 'done'));
    }

    if (priorityParam) {
      conditions.push(eq(tasks.priority, priorityParam as 'high' | 'medium' | 'low'));
    }

    const result =
      conditions.length > 0
        ? await db.select().from(tasks).where(and(...conditions)).all()
        : await db.select().from(tasks).all();

    return c.json(result);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// POST /api/tasks
router.post('/', async (c) => {
  try {
    const body = await c.req.json<{
      title: string;
      description?: string;
      project_id?: number;
      priority?: 'high' | 'medium' | 'low';
      due_date?: string;
    }>();

    if (!body.title || typeof body.title !== 'string') {
      return c.json({ error: 'title is required' }, 400);
    }

    const now = new Date().toISOString();
    const [created] = await db
      .insert(tasks)
      .values({
        title: body.title,
        description: body.description ?? null,
        project_id: body.project_id ?? null,
        priority: body.priority ?? 'medium',
        due_date: body.due_date ?? null,
        status: 'todo',
        created_at: now,
        updated_at: now,
        completed_at: null,
      })
      .returning();

    return c.json(created, 201);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// PUT /api/tasks/:id
router.put('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(tasks).where(eq(tasks.id, id)).get();
    if (!existing) return c.json({ error: 'Task not found' }, 404);

    const body = await c.req.json<{
      title?: string;
      description?: string;
      status?: 'todo' | 'in_progress' | 'done';
      priority?: string;
      due_date?: string;
    }>();

    const now = new Date().toISOString();
    const newStatus = body.status ?? existing.status;
    const completedAt =
      newStatus === 'done' && existing.status !== 'done'
        ? now
        : newStatus !== 'done'
        ? null
        : existing.completed_at;

    const [updated] = await db
      .update(tasks)
      .set({
        title: body.title ?? existing.title,
        description: body.description !== undefined ? body.description : existing.description,
        status: newStatus,
        priority: (body.priority as 'high' | 'medium' | 'low') ?? existing.priority,
        due_date: body.due_date !== undefined ? body.due_date : existing.due_date,
        completed_at: completedAt,
        updated_at: now,
      })
      .where(eq(tasks.id, id))
      .returning();

    return c.json(updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// DELETE /api/tasks/:id
router.delete('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(tasks).where(eq(tasks.id, id)).get();
    if (!existing) return c.json({ error: 'Task not found' }, 404);

    // Remove associated time logs first to respect FK constraint
    await db.delete(time_logs).where(eq(time_logs.task_id, id));
    await db.delete(tasks).where(eq(tasks.id, id));

    return c.json({ success: true });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// POST /api/tasks/:id/timer/start
router.post('/:id/timer/start', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const task = await db.select().from(tasks).where(eq(tasks.id, id)).get();
    if (!task) return c.json({ error: 'Task not found' }, 404);

    // Enforce global single active timer constraint
    const activeTimer = await db
      .select()
      .from(time_logs)
      .where(isNull(time_logs.stopped_at))
      .get();

    if (activeTimer) {
      return c.json(
        {
          error: 'Another timer is already active. Stop it before starting a new one.',
          active_task_id: activeTimer.task_id,
          active_log_id: activeTimer.id,
        },
        400
      );
    }

    const now = new Date().toISOString();
    const [log] = await db
      .insert(time_logs)
      .values({
        task_id: id,
        started_at: now,
        stopped_at: null,
        duration_seconds: null,
        memo: null,
      })
      .returning();

    // Automatically advance task to in_progress if it was todo
    if (task.status === 'todo') {
      await db
        .update(tasks)
        .set({ status: 'in_progress', updated_at: now })
        .where(eq(tasks.id, id));
    }

    return c.json({ message: 'Timer started', log }, 201);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// POST /api/tasks/:id/timer/stop
router.post('/:id/timer/stop', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const task = await db.select().from(tasks).where(eq(tasks.id, id)).get();
    if (!task) return c.json({ error: 'Task not found' }, 404);

    const body = await c.req.json<{ memo?: string }>().catch(() => ({}));

    // Find the open time log for this specific task
    const openLog = await db
      .select()
      .from(time_logs)
      .where(and(eq(time_logs.task_id, id), isNull(time_logs.stopped_at)))
      .get();

    if (!openLog) {
      return c.json({ error: 'No active timer found for this task' }, 400);
    }

    const now = new Date().toISOString();
    const startedMs = new Date(openLog.started_at).getTime();
    const stoppedMs = new Date(now).getTime();
    const durationSeconds = Math.floor((stoppedMs - startedMs) / 1000);

    const [updated] = await db
      .update(time_logs)
      .set({
        stopped_at: now,
        duration_seconds: durationSeconds,
        memo: (body as { memo?: string }).memo ?? null,
      })
      .where(eq(time_logs.id, openLog.id))
      .returning();

    return c.json({
      message: 'Timer stopped',
      log: updated,
      duration_seconds: durationSeconds,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

export default router;
