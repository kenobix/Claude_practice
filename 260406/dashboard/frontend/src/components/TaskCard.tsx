import { useNavigate } from 'react-router-dom'
import type { Task } from '../types'

interface TaskCardProps {
  task: Task
  onStatusToggle?: (task: Task) => void
}

const priorityStyles: Record<string, string> = {
  high: 'bg-red-100 text-red-700',
  medium: 'bg-yellow-100 text-yellow-700',
  low: 'bg-gray-100 text-gray-600',
}

const statusStyles: Record<string, string> = {
  todo: 'bg-gray-100 text-gray-600',
  in_progress: 'bg-blue-100 text-blue-700',
  done: 'bg-green-100 text-green-700',
}

const statusLabels: Record<string, string> = {
  todo: 'Todo',
  in_progress: 'In Progress',
  done: 'Done',
}

export default function TaskCard({ task, onStatusToggle }: TaskCardProps) {
  const navigate = useNavigate()
  const isDone = task.status === 'done'

  return (
    <div
      className={`bg-white rounded-xl border p-4 shadow-sm hover:shadow-md transition-shadow cursor-pointer ${
        isDone ? 'border-green-200 opacity-70' : 'border-gray-200'
      }`}
      onClick={() => navigate(`/tasks/${task.id}`)}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className={`text-sm font-semibold flex-1 line-clamp-2 ${isDone ? 'line-through text-gray-400' : 'text-gray-900'}`}>
          {task.title}
        </h3>
        {onStatusToggle && !isDone && (
          <button
            className="shrink-0 w-7 h-7 rounded-full border-2 border-gray-300 hover:border-green-500 hover:bg-green-50 transition-colors flex items-center justify-center"
            onClick={(e) => {
              e.stopPropagation()
              onStatusToggle({ ...task, status: 'done' })
            }}
            title="Mark as done"
          >
            <span className="text-xs text-gray-400 hover:text-green-600">✓</span>
          </button>
        )}
        {isDone && (
          <span className="shrink-0 w-7 h-7 rounded-full bg-green-100 border-2 border-green-400 flex items-center justify-center">
            <span className="text-xs text-green-600">✓</span>
          </span>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5 items-center">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${priorityStyles[task.priority]}`}>
          {task.priority}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusStyles[task.status]}`}>
          {statusLabels[task.status]}
        </span>
        {task.label && (
          <span className="text-xs px-2 py-0.5 rounded-full font-medium bg-indigo-50 text-indigo-600 border border-indigo-200">
            {task.label}
          </span>
        )}
      </div>

      {task.due_date && (
        <p className="mt-2 text-xs text-gray-400">
          Due: {new Date(task.due_date).toLocaleDateString()}
        </p>
      )}
    </div>
  )
}
