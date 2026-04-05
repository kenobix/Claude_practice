import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getTasks, updateTask, deleteTask, startTimer, stopTimer, getTimeLogs } from '../api/tasks'
import { getDashboard } from '../api/dashboard'
import { getProjects } from '../api/projects'
import { formatElapsed } from '../hooks/useTimer'
import type { Task, TimeLog, Project, ActiveTimer, Status, Priority } from '../types'

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [task, setTask] = useState<Task | null>(null)
  const [timeLogs, setTimeLogs] = useState<TimeLog[]>([])
  const [projects, setProjects] = useState<Project[]>([])
  const [activeTimer, setActiveTimer] = useState<ActiveTimer | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const [form, setForm] = useState({
    title: '',
    description: '',
    status: 'todo' as Status,
    priority: 'medium' as Priority,
    due_date: '',
    project_id: '',
  })

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [allTasks, logs, projs, dash] = await Promise.all([
        getTasks(),
        getTimeLogs(Number(id)),
        getProjects(),
        getDashboard(),
      ])
      const found = allTasks.find((t) => t.id === Number(id))
      if (!found) {
        navigate('/tasks')
        return
      }
      setTask(found)
      setForm({
        title: found.title,
        description: found.description || '',
        status: found.status,
        priority: found.priority,
        due_date: found.due_date ? found.due_date.substring(0, 10) : '',
        project_id: found.project_id ? String(found.project_id) : '',
      })
      setTimeLogs(logs)
      setProjects(projs)
      setActiveTimer(dash.active_timer)
    } finally {
      setLoading(false)
    }
  }, [id, navigate])

  useEffect(() => {
    load()
  }, [load])

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!task) return
    setSaving(true)
    try {
      const updated = await updateTask(task.id, {
        title: form.title.trim(),
        description: form.description.trim(),
        status: form.status,
        priority: form.priority,
        due_date: form.due_date || null,
        project_id: form.project_id ? Number(form.project_id) : null,
      })
      setTask(updated)
    } catch {
      alert('Failed to save task')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!task || !confirm('Delete this task?')) return
    await deleteTask(task.id)
    navigate('/tasks')
  }

  const handleStartTimer = async () => {
    if (!task) return
    try {
      await startTimer(task.id)
      await load()
    } catch {
      alert('Failed to start timer')
    }
  }

  const handleStopTimer = async () => {
    if (!task) return
    try {
      await stopTimer(task.id)
      await load()
    } catch {
      alert('Failed to stop timer')
    }
  }

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  if (!task) return null

  const isActiveTask = activeTimer?.task_id === task.id

  return (
    <div className="max-w-2xl mx-auto pb-20 lg:pb-0">
      <div className="flex items-center gap-2 mb-6">
        <button onClick={() => navigate('/tasks')} className="text-gray-400 hover:text-gray-600 text-sm">
          ← Tasks
        </button>
      </div>

      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
        <form onSubmit={handleSave} className="space-y-5">
          {/* Title */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Title</label>
            <input
              type="text"
              value={form.title}
              onChange={(e) => setForm({ ...form, title: e.target.value })}
              className="w-full text-xl font-bold text-gray-900 border-0 border-b-2 border-gray-200 focus:border-indigo-500 focus:outline-none pb-1 bg-transparent"
              required
            />
          </div>

          {/* Status / Priority / Due */}
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Status</label>
              <select
                value={form.status}
                onChange={(e) => setForm({ ...form, status: e.target.value as Status })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="todo">Todo</option>
                <option value="in_progress">In Progress</option>
                <option value="done">Done</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Priority</label>
              <select
                value={form.priority}
                onChange={(e) => setForm({ ...form, priority: e.target.value as Priority })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              >
                <option value="high">High</option>
                <option value="medium">Medium</option>
                <option value="low">Low</option>
              </select>
            </div>
            <div>
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Due Date</label>
              <input
                type="date"
                value={form.due_date}
                onChange={(e) => setForm({ ...form, due_date: e.target.value })}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
          </div>

          {/* Project */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Project</label>
            <select
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value })}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <option value="">None</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Description</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={4}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
              placeholder="Task description..."
            />
          </div>

          {/* Actions */}
          <div className="flex items-center justify-between pt-2">
            <button
              type="button"
              onClick={handleDelete}
              className="text-sm text-red-500 hover:text-red-700 font-medium"
            >
              Delete Task
            </button>
            <button
              type="submit"
              disabled={saving}
              className="bg-indigo-600 text-white px-5 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {saving ? 'Saving...' : 'Save Changes'}
            </button>
          </div>
        </form>
      </div>

      {/* Timer Section */}
      <div className="mt-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Timer</h2>
        {isActiveTask ? (
          <div className="flex items-center gap-4">
            <span className="text-sm text-indigo-600 font-medium animate-pulse">Timer running...</span>
            <button
              onClick={handleStopTimer}
              className="bg-red-500 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-red-600 transition-colors"
            >
              Stop Timer
            </button>
          </div>
        ) : activeTimer ? (
          <p className="text-sm text-gray-400">Another task has an active timer. Stop it first.</p>
        ) : (
          <button
            onClick={handleStartTimer}
            className="bg-green-500 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-green-600 transition-colors"
          >
            Start Timer
          </button>
        )}
      </div>

      {/* Time Logs */}
      <div className="mt-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
        <h2 className="text-base font-semibold text-gray-800 mb-4">Time Logs</h2>
        {timeLogs.length === 0 ? (
          <p className="text-sm text-gray-400">No time logs yet</p>
        ) : (
          <div className="space-y-2">
            {timeLogs.map((log) => (
              <div key={log.id} className="flex items-center justify-between text-sm border border-gray-100 rounded-lg px-4 py-2">
                <div>
                  <span className="text-gray-700 font-medium">
                    {new Date(log.started_at).toLocaleDateString()} {new Date(log.started_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                  </span>
                  {log.memo && <span className="text-gray-400 ml-2">— {log.memo}</span>}
                </div>
                <span className="font-mono text-indigo-600 font-semibold">
                  {log.duration_seconds != null
                    ? formatElapsed(log.duration_seconds)
                    : log.stopped_at
                    ? formatElapsed(Math.floor((new Date(log.stopped_at).getTime() - new Date(log.started_at).getTime()) / 1000))
                    : 'Running...'}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
