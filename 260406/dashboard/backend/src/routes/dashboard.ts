import { Hono } from 'hono';
import { eq, or, isNull, sql, desc } from 'drizzle-orm';
import db from '../db/index.js';
import { tasks, time_logs, notes } from '../db/schema.js';

const router = new Hono();

// GET /api/dashboard
router.get('/', async (c) => {
  try {
    const today = new Date().toISOString().split('T')[0]; // YYYY-MM-DD

    // today_tasks: tasks with due_date=today OR status=in_progress
    const todayTasks = await db
      .select()
      .from(tasks)
      .where(or(eq(tasks.due_date, today), eq(tasks.status, 'in_progress')))
      .all();

    // active_timer: time_log with stopped_at IS NULL
    const activeLog = await db
      .select()
      .from(time_logs)
      .where(isNull(time_logs.stopped_at))
      .get();

    let activeTimer: {
      task_id: number;
      task_title: string;
      started_at: string;
      elapsed_seconds: number;
    } | null = null;

    if (activeLog) {
      const timerTask = await db
        .select()
        .from(tasks)
        .where(eq(tasks.id, activeLog.task_id))
        .get();

      const startedMs = new Date(activeLog.started_at).getTime();
      const nowMs = Date.now();
      const elapsedSeconds = Math.floor((nowMs - startedMs) / 1000);

      activeTimer = {
        task_id: activeLog.task_id,
        task_title: timerTask?.title ?? '(Unknown Task)',
        started_at: activeLog.started_at,
        elapsed_seconds: elapsedSeconds,
      };
    }

    // today_stats: tasks completed today + total seconds logged today
    const completedTodayResult = await db
      .select()
      .from(tasks)
      .where(sql`${tasks.completed_at} LIKE ${today + '%'}`)
      .all();

    const completedCount = completedTodayResult.length;

    // Sum duration_seconds for time logs started today
    const todayLogsResult = await db
      .select()
      .from(time_logs)
      .where(sql`${time_logs.started_at} LIKE ${today + '%'}`)
      .all();

    const totalSecondsToday = todayLogsResult.reduce(
      (sum, log) => sum + (log.duration_seconds ?? 0),
      0
    );

    // recent_notes: last 5 notes ordered by updated_at desc
    const recentNotes = await db
      .select()
      .from(notes)
      .orderBy(desc(notes.updated_at))
      .limit(5)
      .all();

    const recentNotesParsed = recentNotes.map((note) => ({
      ...note,
      tags: JSON.parse(note.tags) as string[],
    }));

    return c.json({
      today_tasks: todayTasks,
      active_timer: activeTimer,
      today_stats: {
        completed_count: completedCount,
        total_seconds_today: totalSecondsToday,
      },
      recent_notes: recentNotesParsed,
    });
  } catch (err) {
    const message = err instanceof Error ? err.message : 'Unknown error';
    return c.json({ error: message }, 500);
  }
});

export default router;
