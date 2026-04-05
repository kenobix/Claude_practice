import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getTasks, updateTask, deleteTask, startTimer, stopTimer, getTimeLogs } from '../api/tasks'
import { getDashboard } from '../api/dashboard'
import { useTimer, formatElapsed } from '../hooks/useTimer'
import type { Task, TimeLog, ActiveTimer, Status, Priority } from '../types'

export default function TaskDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [task, setTask] = useState<Task | null>(null)
  const [timeLogs, setTimeLogs] = useState<TimeLog[]>([])
  const [activeTimer, setActiveTimer] = useState<ActiveTimer | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)

  const [form, setForm] = useState({
    title: '',
    description: '',
    label: '',
    status: 'todo' as Status,
    priority: 'medium' as Priority,
    due_date: '',
  })

  const elapsed = useTimer(activeTimer)

  const load = useCallback(async () => {
    if (!id) return
    try {
      const [allTasks, logs, dash] = await Promise.all([
        getTasks(),
        getTimeLogs(Number(id)),
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
        label: found.label || '',
        status: found.status,
        priority: found.priority,
        due_date: found.due_date ? found.due_date.substring(0, 10) : '',
      })
      setTimeLogs(logs)
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
        label: form.label.trim() || null,
        status: form.status,
        priority: form.priority,
        due_date: form.due_date || null,
      })
      setTask(updated)
    } catch {
      alert('Failed to save task')
    } finally {
      setSaving(false)
    }
  }

  const handleMarkDone = async () => {
    if (!task) return
    try {
      const updated = await updateTask(task.id, { status: 'done' })
      setTask(updated)
      setForm((f) => ({ ...f, status: 'done' }))
    } catch {
      alert('Failed to update task')
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
  const isDone = task.status === 'done'

  // Total logged seconds for this task
  const totalSeconds = timeLogs.reduce((sum, log) => {
    if (log.duration_seconds != null) return sum + log.duration_seconds
    if (log.stopped_at) return sum + Math.floor((new Date(log.stopped_at).getTime() - new Date(log.started_at).getTime()) / 1000)
    return sum
  }, 0)

  return (
    <div className="max-w-2xl mx-auto pb-20 lg:pb-0">
      <div className="flex items-center gap-2 mb-6">
        <button onClick={() => navigate('/tasks')} className="text-gray-400 hover:text-gray-600 text-sm">
          ← Tasks
        </button>
      </div>

      {/* Timer bar — shown when this task's timer is running */}
      {isActiveTask && (
        <div className="mb-4 bg-indigo-600 text-white rounded-2xl p-4 flex items-center justify-between shadow-md">
          <div>
            <p className="text-xs font-medium text-indigo-200 uppercase tracking-wide mb-1">Focus Timer</p>
            <p className="text-4xl font-mono font-bold tabular-nums">{formatElapsed(elapsed)}</p>
          </div>
          <button
            onClick={handleStopTimer}
            className="bg-white text-indigo-700 font-semibold px-5 py-2 rounded-lg hover:bg-indigo-50 transition-colors text-sm"
          >
            Stop
          </button>
        </div>
      )}

      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
        <form onSubmit={handleSave} className="space-y-5">
          {/* Title + Done button */}
          <div className="flex items-start gap-3">
            <div className="flex-1">
              <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Title</label>
              <input
                type="text"
                value={form.title}
                onChange={(e) => setForm({ ...form, title: e.target.value })}
                className={`w-full text-xl font-bold border-0 border-b-2 border-gray-200 focus:border-indigo-500 focus:outline-none pb-1 bg-transparent ${isDone ? 'line-through text-gray-400' : 'text-gray-900'}`}
                required
              />
            </div>
            {!isDone && (
              <button
                type="button"
                onClick={handleMarkDone}
                className="mt-6 shrink-0 bg-green-500 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-green-600 transition-colors"
              >
                ✓ Done
              </button>
            )}
            {isDone && (
              <span className="mt-6 shrink-0 text-sm text-green-600 font-semibold">Completed</span>
            )}
          </div>

          {/* Label */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Label</label>
            <input
              type="text"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              placeholder="e.g. Work, Personal, Side Project..."
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
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

          {/* Memo */}
          <div>
            <label className="block text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">Memo</label>
            <textarea
              value={form.description}
              onChange={(e) => setForm({ ...form, description: e.target.value })}
              rows={4}
              className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
              placeholder="Notes about this task..."
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
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-base font-semibold text-gray-800">Focus Timer</h2>
          {totalSeconds > 0 && (
            <span className="text-sm text-gray-500">Total: <span className="font-mono font-semibold text-indigo-600">{formatElapsed(totalSeconds)}</span></span>
          )}
        </div>
        {isActiveTask ? (
          <p className="text-sm text-indigo-500 animate-pulse">Timer running — see counter above</p>
        ) : activeTimer ? (
          <p className="text-sm text-gray-400">Another task has an active timer. Stop it first.</p>
        ) : (
          <button
            onClick={handleStartTimer}
            className="bg-green-500 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-green-600 transition-colors"
          >
            ▶ Start Timer
          </button>
        )}
      </div>

      {/* Time Logs */}
      {timeLogs.length > 0 && (
        <div className="mt-6 bg-white rounded-2xl border border-gray-200 shadow-sm p-6">
          <h2 className="text-base font-semibold text-gray-800 mb-4">Time Logs</h2>
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
        </div>
      )}
    </div>
  )
}
