import { useEffect, useState, useCallback } from 'react'
import { Link } from 'react-router-dom'
import { getDashboard } from '../api/dashboard'
import { stopTimer } from '../api/tasks'
import TimerWidget from '../components/TimerWidget'
import TaskCard from '../components/TaskCard'
import { formatHHMM } from '../hooks/useTimer'
import type { Dashboard } from '../types'

export default function DashboardPage() {
  const [data, setData] = useState<Dashboard | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const dash = await getDashboard()
      setData(dash)
      setError(null)
    } catch {
      setError('Failed to load dashboard')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleStopTimer = async () => {
    if (!data?.active_timer) return
    try {
      await stopTimer(data.active_timer.task_id)
      await load()
    } catch {
      alert('Failed to stop timer')
    }
  }

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  if (error) return <div className="text-red-500 p-4">{error}</div>
  if (!data) return null

  return (
    <div className="max-w-4xl mx-auto space-y-6 pb-20 lg:pb-0">
      <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>

      {/* Timer */}
      <TimerWidget activeTimer={data.active_timer} onStop={handleStopTimer} />

      {/* Stats */}
      <div className="grid grid-cols-2 gap-4">
        <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm text-center">
          <p className="text-3xl font-bold text-indigo-600">{data.today_stats.completed_count}</p>
          <p className="text-sm text-gray-500 mt-1">Completed Today</p>
        </div>
        <div className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm text-center">
          <p className="text-3xl font-bold text-indigo-600">{formatHHMM(data.today_stats.total_seconds_today)}</p>
          <p className="text-sm text-gray-500 mt-1">Time Today (HH:MM)</p>
        </div>
      </div>

      {/* Today's Tasks */}
      <section>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-lg font-semibold text-gray-800">Today's Tasks</h2>
          <Link to="/tasks" className="text-sm text-indigo-600 hover:underline">View all</Link>
        </div>
        {data.today_tasks.length === 0 ? (
          <p className="text-sm text-gray-400 bg-white rounded-xl border border-gray-200 p-6 text-center">
            No tasks for today
          </p>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            {data.today_tasks.map((task) => (
              <TaskCard key={task.id} task={task} />
            ))}
          </div>
        )}
      </section>
    </div>
  )
}
