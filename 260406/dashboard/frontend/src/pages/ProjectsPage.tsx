import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getProjects, createProject, deleteProject } from '../api/projects'
import type { Project } from '../types'

const PRESET_COLORS = ['#6366f1', '#8b5cf6', '#ec4899', '#10b981', '#f59e0b', '#3b82f6']

interface ProjectForm {
  name: string
  description: string
  color: string
}

const defaultForm: ProjectForm = {
  name: '',
  description: '',
  color: '#6366f1',
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [showModal, setShowModal] = useState(false)
  const [form, setForm] = useState<ProjectForm>(defaultForm)
  const [saving, setSaving] = useState(false)
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setProjects(await getProjects())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!form.name.trim()) return
    setSaving(true)
    try {
      const created = await createProject({
        name: form.name.trim(),
        description: form.description.trim(),
        color: form.color,
        status: 'active',
      })
      setProjects((prev) => [...prev, created])
      setForm(defaultForm)
      setShowModal(false)
    } catch {
      alert('Failed to create project')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    if (!confirm('Delete this project?')) return
    await deleteProject(id)
    setProjects((prev) => prev.filter((p) => p.id !== id))
  }

  return (
    <div className="max-w-4xl mx-auto pb-20 lg:pb-0">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Projects</h1>
        <button
          onClick={() => setShowModal(true)}
          className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors"
        >
          + New Project
        </button>
      </div>

      {loading ? (
        <div className="text-center text-gray-400 py-16">Loading...</div>
      ) : projects.length === 0 ? (
        <div className="text-center text-gray-400 py-16 bg-white rounded-xl border border-gray-200">
          No projects yet. Create one!
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {projects.map((project) => (
            <div
              key={project.id}
              className="bg-white rounded-xl border border-gray-200 p-5 shadow-sm hover:shadow-md transition-shadow cursor-pointer group"
              onClick={() => navigate(`/projects/${project.id}`)}
            >
              <div className="flex items-start justify-between">
                <div className="flex items-center gap-3">
                  <div
                    className="w-4 h-4 rounded-full shrink-0 mt-0.5"
                    style={{ backgroundColor: project.color }}
                  />
                  <h3 className="font-semibold text-gray-900">{project.name}</h3>
                </div>
                <button
                  onClick={(e) => handleDelete(e, project.id)}
                  className="text-gray-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all text-sm"
                >
                  ✕
                </button>
              </div>
              {project.description && (
                <p className="mt-2 text-sm text-gray-500 line-clamp-2 ml-7">{project.description}</p>
              )}
              <div className="mt-3 ml-7">
                <span
                  className="text-xs px-2 py-0.5 rounded-full"
                  style={{ backgroundColor: project.color + '22', color: project.color }}
                >
                  {project.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* New Project Modal */}
      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black bg-opacity-50 p-4">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-md">
            <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200">
              <h2 className="text-lg font-semibold text-gray-900">New Project</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-gray-600 text-xl">✕</button>
            </div>
            <form onSubmit={handleCreate} className="p-6 space-y-4">
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Name *</label>
                <input
                  type="text"
                  value={form.name}
                  onChange={(e) => setForm({ ...form, name: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
                  required
                  autoFocus
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-1">Description</label>
                <textarea
                  value={form.description}
                  onChange={(e) => setForm({ ...form, description: e.target.value })}
                  className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 resize-none"
                  rows={3}
                />
              </div>
              <div>
                <label className="block text-sm font-medium text-gray-700 mb-2">Color</label>
                <div className="flex gap-2">
                  {PRESET_COLORS.map((color) => (
                    <button
                      key={color}
                      type="button"
                      onClick={() => setForm({ ...form, color })}
                      className="w-8 h-8 rounded-full transition-transform hover:scale-110 focus:outline-none"
                      style={{
                        backgroundColor: color,
                        boxShadow: form.color === color ? `0 0 0 3px white, 0 0 0 5px ${color}` : 'none',
                      }}
                    />
                  ))}
                </div>
              </div>
              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => setShowModal(false)}
                  className="px-4 py-2 text-sm text-gray-600 hover:text-gray-900"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="bg-indigo-600 text-white px-5 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                >
                  {saving ? 'Creating...' : 'Create'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  )
}
