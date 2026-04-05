export type Status = 'todo' | 'in_progress' | 'done'
export type Priority = 'high' | 'medium' | 'low'

export interface Project {
  id: number
  name: string
  description: string
  color: string
  status: string
  created_at: string
}

export interface Task {
  id: number
  title: string
  description: string
  status: Status
  priority: Priority
  project_id: number | null
  due_date: string | null
  created_at: string
  completed_at: string | null
}

export interface TimeLog {
  id: number
  task_id: number
  started_at: string
  stopped_at: string | null
  duration_seconds: number | null
  memo: string
}

export interface Note {
  id: number
  title: string
  body: string
  project_id: number | null
  task_id: number | null
  tags: string[]
  created_at: string
  updated_at: string
}

export interface ActiveTimer {
  task_id: number
  task_title: string
  started_at: string
  elapsed_seconds: number
}

export interface TodayStats {
  completed_count: number
  total_seconds_today: number
}

export interface Dashboard {
  today_tasks: Task[]
  active_timer: ActiveTimer | null
  today_stats: TodayStats
  recent_notes: Note[]
}
