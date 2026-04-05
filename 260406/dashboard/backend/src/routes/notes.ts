import { Hono } from 'hono';
import { eq, and, sql } from 'drizzle-orm';
import db from '../db/index.js';
import { notes } from '../db/schema.js';

const router = new Hono();

function parseNote(note: typeof notes.$inferSelect) {
  return {
    ...note,
    tags: JSON.parse(note.tags) as string[],
  };
}

// GET /api/notes?project_id=&task_id=&tag=
router.get('/', async (c) => {
  try {
    const projectIdParam = c.req.query('project_id');
    const taskIdParam = c.req.query('task_id');
    const tagParam = c.req.query('tag');

    const conditions = [];

    if (projectIdParam) {
      const projectId = Number(projectIdParam);
      if (!isNaN(projectId)) {
        conditions.push(eq(notes.project_id, projectId));
      }
    }

    if (taskIdParam) {
      const taskId = Number(taskIdParam);
      if (!isNaN(taskId)) {
        conditions.push(eq(notes.task_id, taskId));
      }
    }

    if (tagParam) {
      // Search for tag within JSON array string
      conditions.push(sql`${notes.tags} LIKE ${'%"' + tagParam + '"%'}`);
    }

    const result =
      conditions.length > 0
        ? await db.select().from(notes).where(and(...conditions)).all()
        : await db.select().from(notes).all();

    return c.json(result.map(parseNote));
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// POST /api/notes
router.post('/', async (c) => {
  try {
    const body = await c.req.json<{
      title: string;
      body: string;
      project_id?: number;
      task_id?: number;
      tags?: string[];
    }>();

    if (!body.title || typeof body.title !== 'string') {
      return c.json({ error: 'title is required' }, 400);
    }
    if (!body.body || typeof body.body !== 'string') {
      return c.json({ error: 'body is required' }, 400);
    }

    const now = new Date().toISOString();
    const [created] = await db
      .insert(notes)
      .values({
        title: body.title,
        body: body.body,
        project_id: body.project_id ?? null,
        task_id: body.task_id ?? null,
        tags: JSON.stringify(body.tags ?? []),
        created_at: now,
        updated_at: now,
      })
      .returning();

    return c.json(parseNote(created), 201);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// PUT /api/notes/:id
router.put('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(notes).where(eq(notes.id, id)).get();
    if (!existing) return c.json({ error: 'Note not found' }, 404);

    const body = await c.req.json<{
      title?: string;
      body?: string;
      project_id?: number;
      task_id?: number;
      tags?: string[];
    }>();

    const now = new Date().toISOString();
    const [updated] = await db
      .update(notes)
      .set({
        title: body.title ?? existing.title,
        body: body.body ?? existing.body,
        project_id: body.project_id !== undefined ? body.project_id : existing.project_id,
        task_id: body.task_id !== undefined ? body.task_id : existing.task_id,
        tags: body.tags !== undefined ? JSON.stringify(body.tags) : existing.tags,
        updated_at: now,
      })
      .where(eq(notes.id, id))
      .returning();

    return c.json(parseNote(updated));
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// DELETE /api/notes/:id
router.delete('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(notes).where(eq(notes.id, id)).get();
    if (!existing) return c.json({ error: 'Note not found' }, 404);

    await db.delete(notes).where(eq(notes.id, id));
    return c.json({ success: true });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

export default router;
