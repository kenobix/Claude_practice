import { Hono } from 'hono';
import { eq } from 'drizzle-orm';
import db from '../db/index.js';
import { projects } from '../db/schema.js';

const router = new Hono();

// GET /api/projects
router.get('/', async (c) => {
  try {
    const all = await db.select().from(projects).all();
    return c.json(all);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// POST /api/projects
router.post('/', async (c) => {
  try {
    const body = await c.req.json<{
      name: string;
      description?: string;
      color?: string;
    }>();

    if (!body.name || typeof body.name !== 'string') {
      return c.json({ error: 'name is required' }, 400);
    }

    const now = new Date().toISOString();
    const [created] = await db
      .insert(projects)
      .values({
        name: body.name,
        description: body.description ?? null,
        color: body.color ?? '#4F46E5',
        status: 'active',
        created_at: now,
        updated_at: now,
      })
      .returning();

    return c.json(created, 201);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// PUT /api/projects/:id
router.put('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(projects).where(eq(projects.id, id)).get();
    if (!existing) return c.json({ error: 'Project not found' }, 404);

    const body = await c.req.json<{
      name?: string;
      description?: string;
      color?: string;
      status?: 'active' | 'archived';
    }>();

    const now = new Date().toISOString();
    const [updated] = await db
      .update(projects)
      .set({
        name: body.name ?? existing.name,
        description: body.description !== undefined ? body.description : existing.description,
        color: body.color ?? existing.color,
        status: body.status ?? existing.status,
        updated_at: now,
      })
      .where(eq(projects.id, id))
      .returning();

    return c.json(updated);
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

// DELETE /api/projects/:id
router.delete('/:id', async (c) => {
  try {
    const id = Number(c.req.param('id'));
    if (isNaN(id)) return c.json({ error: 'Invalid id' }, 400);

    const existing = await db.select().from(projects).where(eq(projects.id, id)).get();
    if (!existing) return c.json({ error: 'Project not found' }, 404);

    await db.delete(projects).where(eq(projects.id, id));
    return c.json({ success: true });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

export default router;
