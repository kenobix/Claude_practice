import { sqliteTable, integer, text } from 'drizzle-orm/sqlite-core';

export const projects = sqliteTable('projects', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  name: text('name').notNull(),
  description: text('description'),
  color: text('color').notNull().default('#4F46E5'),
  status: text('status', { enum: ['active', 'archived'] }).notNull().default('active'),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
});

export const tasks = sqliteTable('tasks', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  project_id: integer('project_id').references(() => projects.id),
  title: text('title').notNull(),
  description: text('description'),
  status: text('status', { enum: ['todo', 'in_progress', 'done'] }).notNull().default('todo'),
  priority: text('priority', { enum: ['high', 'medium', 'low'] }).notNull().default('medium'),
  due_date: text('due_date'),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
  completed_at: text('completed_at'),
});

export const time_logs = sqliteTable('time_logs', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  task_id: integer('task_id').notNull().references(() => tasks.id),
  started_at: text('started_at').notNull(),
  stopped_at: text('stopped_at'),
  duration_seconds: integer('duration_seconds'),
  memo: text('memo'),
});

export const notes = sqliteTable('notes', {
  id: integer('id').primaryKey({ autoIncrement: true }),
  title: text('title').notNull(),
  body: text('body').notNull(),
  project_id: integer('project_id').references(() => projects.id),
  task_id: integer('task_id').references(() => tasks.id),
  tags: text('tags').notNull().default('[]'),
  created_at: text('created_at').notNull(),
  updated_at: text('updated_at').notNull(),
});

export type Project = typeof projects.$inferSelect;
export type NewProject = typeof projects.$inferInsert;
export type Task = typeof tasks.$inferSelect;
export type NewTask = typeof tasks.$inferInsert;
export type TimeLog = typeof time_logs.$inferSelect;
export type NewTimeLog = typeof time_logs.$inferInsert;
export type Note = typeof notes.$inferSelect;
export type NewNote = typeof notes.$inferInsert;
