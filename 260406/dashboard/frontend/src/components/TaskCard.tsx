import { useNavigate } from 'react-router-dom'
import type { Task, Project } from '../types'

interface TaskCardProps {
  task: Task
  project?: Project | null
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

function nextStatus(current: string): string {
  if (current === 'todo') return 'in_progress'
  if (current === 'in_progress') return 'done'
  return 'todo'
}

export default function TaskCard({ task, project, onStatusToggle }: TaskCardProps) {
  const navigate = useNavigate()

  return (
    <div
      className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition-shadow cursor-pointer"
      onClick={() => navigate(`/tasks/${task.id}`)}
    >
      <div className="flex items-start justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-900 flex-1 line-clamp-2">{task.title}</h3>
        {onStatusToggle && (
          <button
            className="shrink-0 text-xs px-2 py-1 rounded-full border border-gray-300 text-gray-500 hover:bg-gray-100 transition-colors"
            onClick={(e) => {
              e.stopPropagation()
              onStatusToggle({ ...task, status: nextStatus(task.status) as Task['status'] })
            }}
            title="Toggle status"
          >
            →
          </button>
        )}
      </div>

      <div className="mt-2 flex flex-wrap gap-1.5 items-center">
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${priorityStyles[task.priority]}`}>
          {task.priority}
        </span>
        <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${statusStyles[task.status]}`}>
          {statusLabels[task.status]}
        </span>
        {project && (
          <span
            className="text-xs px-2 py-0.5 rounded-full font-medium"
            style={{
              backgroundColor: project.color + '22',
              color: project.color,
            }}
          >
            {project.name}
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
