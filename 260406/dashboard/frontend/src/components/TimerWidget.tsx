import type { ActiveTimer } from '../types'
import { useTimer, formatElapsed } from '../hooks/useTimer'

interface TimerWidgetProps {
  activeTimer: ActiveTimer | null
  onStop: () => void
}

export default function TimerWidget({ activeTimer, onStop }: TimerWidgetProps) {
  const elapsed = useTimer(activeTimer)

  if (!activeTimer) {
    return (
      <div className="bg-gray-100 rounded-xl p-4 text-center text-sm text-gray-400">
        No active timer
      </div>
    )
  }

  return (
    <div className="bg-indigo-600 text-white rounded-xl p-4 flex items-center justify-between shadow-md">
      <div>
        <p className="text-xs font-medium text-indigo-200 uppercase tracking-wide mb-1">Active Timer</p>
        <p className="text-base font-semibold line-clamp-1">{activeTimer.task_title}</p>
        <p className="text-3xl font-mono font-bold mt-1 tabular-nums">{formatElapsed(elapsed)}</p>
      </div>
      <button
        onClick={onStop}
        className="ml-4 shrink-0 bg-white text-indigo-700 font-semibold px-4 py-2 rounded-lg hover:bg-indigo-50 transition-colors text-sm"
      >
        Stop
      </button>
    </div>
  )
}
