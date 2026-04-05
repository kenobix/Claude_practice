import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { getProjects, updateProject } from '../api/projects'
import { getTasks, createTask, updateTask } from '../api/tasks'
import TaskCard from '../components/TaskCard'
import type { Project, Task, Priority, Status } from '../types'

export default function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()

  const [project, setProject] = useState<Project | null>(null)
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(true)
  const [editingName, setEditingName] = useState(false)
  const [nameValue, setNameValue] = useState('')
  const [showAddTask, setShowAddTask] = useState(false)
  const [newTaskTitle, setNewTaskTitle] = useState('')
  const [newTaskPriority, setNewTaskPriority] = useState<Priority>('medium')
  const [addingTask, setAddingTask] = useState(false)

  const load = useCallback(async () => {
    if (!id) return
    setLoading(true)
    try {
      const [projs, allTasks] = await Promise.all([getProjects(), getTasks({ project_id: Number(id) })])
      const found = projs.find((p) => p.id === Number(id))
      if (!found) {
        navigate('/projects')
        return
      }
      setProject(found)
      setNameValue(found.name)
      setTasks(allTasks)
    } finally {
      setLoading(false)
    }
  }, [id, navigate])

  useEffect(() => {
    load()
  }, [load])

  const handleNameSave = async () => {
    if (!project || !nameValue.trim()) return
    const updated = await updateProject(project.id, { name: nameValue.trim() })
    setProject(updated)
    setEditingName(false)
  }

  const handleStatusToggle = async (updated: Task) => {
    await updateTask(updated.id, { status: updated.status })
    setTasks((prev) => prev.map((t) => (t.id === updated.id ? updated : t)))
  }

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!project || !newTaskTitle.trim()) return
    setAddingTask(true)
    try {
      const created = await createTask({
        title: newTaskTitle.trim(),
        priority: newTaskPriority,
        project_id: project.id,
        status: 'todo' as Status,
      })
      setTasks((prev) => [created, ...prev])
      setNewTaskTitle('')
      setShowAddTask(false)
    } finally {
      setAddingTask(false)
    }
  }

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>
  if (!project) return null

  const completedCount = tasks.filter((t) => t.status === 'done').length
  const inProgressCount = tasks.filter((t) => t.status === 'in_progress').length

  return (
    <div className="max-w-4xl mx-auto pb-20 lg:pb-0">
      <button onClick={() => navigate('/projects')} className="text-gray-400 hover:text-gray-600 text-sm mb-4 block">
        ← Projects
      </button>

      {/* Project header */}
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-6 mb-6">
        <div className="flex items-center gap-4">
          <div
            className="w-5 h-5 rounded-full shrink-0"
            style={{ backgroundColor: project.color }}
          />
          {editingName ? (
            <div className="flex items-center gap-2 flex-1">
              <input
                type="text"
                value={nameValue}
                onChange={(e) => setNameValue(e.target.value)}
                className="text-xl font-bold text-gray-900 border-b-2 border-indigo-500 focus:outline-none bg-transparent flex-1"
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleNameSave()
                  if (e.key === 'Escape') setEditingName(false)
                }}
              />
              <button onClick={handleNameSave} className="text-sm text-indigo-600 font-semibold hover:text-indigo-800">Save</button>
              <button onClick={() => setEditingName(false)} className="text-sm text-gray-400 hover:text-gray-600">Cancel</button>
            </div>
          ) : (
            <h1
              className="text-xl font-bold text-gray-900 cursor-pointer hover:text-indigo-600 transition-colors"
              onClick={() => setEditingName(true)}
              title="Click to edit"
            >
              {project.name}
            </h1>
          )}
        </div>
        {project.description && (
          <p className="mt-2 text-sm text-gray-500 ml-9">{project.description}</p>
        )}

        {/* Stats */}
        <div className="mt-4 ml-9 flex gap-4 text-sm">
          <span className="text-gray-500">{tasks.length} tasks</span>
          <span className="text-green-600">{completedCount} done</span>
          <span className="text-blue-600">{inProgressCount} in progress</span>
        </div>
      </div>

      {/* Tasks */}
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-lg font-semibold text-gray-800">Tasks</h2>
        <button
          onClick={() => setShowAddTask(!showAddTask)}
          className="bg-indigo-600 text-white px-3 py-1.5 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors"
        >
          + Add Task
        </button>
      </div>

      {/* Add task form */}
      {showAddTask && (
        <form onSubmit={handleAddTask} className="bg-white rounded-xl border border-indigo-200 p-4 mb-4 flex gap-3">
          <input
            type="text"
            placeholder="Task title..."
            value={newTaskTitle}
            onChange={(e) => setNewTaskTitle(e.target.value)}
            className="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            autoFocus
            required
          />
          <select
            value={newTaskPriority}
            onChange={(e) => setNewTaskPriority(e.target.value as Priority)}
            className="border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
          >
            <option value="high">High</option>
            <option value="medium">Medium</option>
            <option value="low">Low</option>
          </select>
          <button
            type="submit"
            disabled={addingTask}
            className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50"
          >
            Add
          </button>
          <button
            type="button"
            onClick={() => setShowAddTask(false)}
            className="text-sm text-gray-400 px-2 hover:text-gray-600"
          >
            Cancel
          </button>
        </form>
      )}

      {tasks.length === 0 ? (
        <div className="text-center text-gray-400 py-12 bg-white rounded-xl border border-gray-200">
          No tasks in this project
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              project={project}
              onStatusToggle={handleStatusToggle}
            />
          ))}
        </div>
      )}
    </div>
  )
}
