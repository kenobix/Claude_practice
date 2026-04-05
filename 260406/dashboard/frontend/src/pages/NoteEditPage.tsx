import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import { getNotes, createNote, updateNote, deleteNote } from '../api/notes'
import { getProjects } from '../api/projects'
import { getTasks } from '../api/tasks'
import type { Note, Project, Task } from '../types'

export default function NoteEditPage() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const isNew = !id || id === 'new'

  const [projects, setProjects] = useState<Project[]>([])
  const [tasks, setTasks] = useState<Task[]>([])
  const [loading, setLoading] = useState(!isNew)
  const [saving, setSaving] = useState(false)

  const [form, setForm] = useState({
    title: '',
    body: '',
    project_id: '',
    task_id: '',
    tags: '',
  })

  const load = useCallback(async () => {
    try {
      const [projs, allTasks] = await Promise.all([getProjects(), getTasks()])
      setProjects(projs)
      setTasks(allTasks)

      if (!isNew && id) {
        const notes = await getNotes()
        const found = notes.find((n) => n.id === Number(id))
        if (found) {
          setForm({
            title: found.title,
            body: found.body,
            project_id: found.project_id ? String(found.project_id) : '',
            task_id: found.task_id ? String(found.task_id) : '',
            tags: found.tags.join(', '),
          })
        } else {
          navigate('/notes')
        }
      }
    } finally {
      setLoading(false)
    }
  }, [id, isNew, navigate])

  useEffect(() => {
    load()
  }, [load])

  const parseTags = (raw: string): string[] =>
    raw
      .split(',')
      .map((t) => t.trim())
      .filter(Boolean)

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    setSaving(true)
    try {
      const payload: Partial<Note> = {
        title: form.title.trim(),
        body: form.body,
        project_id: form.project_id ? Number(form.project_id) : null,
        task_id: form.task_id ? Number(form.task_id) : null,
        tags: parseTags(form.tags),
      }

      if (isNew) {
        const created = await createNote(payload)
        navigate(`/notes/${created.id}`, { replace: true })
      } else {
        await updateNote(Number(id), payload)
      }
    } catch {
      alert('Failed to save note')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async () => {
    if (!id || !confirm('Delete this note?')) return
    await deleteNote(Number(id))
    navigate('/notes')
  }

  if (loading) return <div className="flex items-center justify-center h-64 text-gray-400">Loading...</div>

  return (
    <div className="max-w-7xl mx-auto pb-20 lg:pb-0">
      <div className="flex items-center justify-between mb-4">
        <button onClick={() => navigate('/notes')} className="text-gray-400 hover:text-gray-600 text-sm">
          ← Notes
        </button>
        <div className="flex items-center gap-3">
          {!isNew && (
            <button
              onClick={handleDelete}
              className="text-sm text-red-500 hover:text-red-700 font-medium"
            >
              Delete
            </button>
          )}
          <button
            onClick={handleSave}
            disabled={saving}
            className="bg-indigo-600 text-white px-5 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : isNew ? 'Create' : 'Save'}
          </button>
        </div>
      </div>

      {/* Title & meta */}
      <div className="bg-white rounded-2xl border border-gray-200 shadow-sm p-4 mb-4">
        <input
          type="text"
          placeholder="Note title..."
          value={form.title}
          onChange={(e) => setForm({ ...form, title: e.target.value })}
          className="w-full text-xl font-bold text-gray-900 border-0 focus:outline-none bg-transparent placeholder-gray-300 mb-3"
        />
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div>
            <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Project</label>
            <select
              value={form.project_id}
              onChange={(e) => setForm({ ...form, project_id: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <option value="">None</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Task</label>
            <select
              value={form.task_id}
              onChange={(e) => setForm({ ...form, task_id: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            >
              <option value="">None</option>
              {tasks.map((t) => (
                <option key={t.id} value={t.id}>{t.title}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-semibold text-gray-400 uppercase tracking-wide mb-1">Tags (comma-separated)</label>
            <input
              type="text"
              placeholder="react, backend, idea"
              value={form.tags}
              onChange={(e) => setForm({ ...form, tags: e.target.value })}
              className="w-full border border-gray-200 rounded-lg px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
            />
          </div>
        </div>
      </div>

      {/* Editor + Preview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Editor */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col">
          <div className="px-4 py-2.5 border-b border-gray-100">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Markdown</span>
          </div>
          <textarea
            value={form.body}
            onChange={(e) => setForm({ ...form, body: e.target.value })}
            placeholder="Write your note in Markdown..."
            className="flex-1 p-4 text-sm text-gray-800 font-mono resize-none focus:outline-none rounded-b-2xl min-h-96"
          />
        </div>

        {/* Preview */}
        <div className="bg-white rounded-2xl border border-gray-200 shadow-sm flex flex-col">
          <div className="px-4 py-2.5 border-b border-gray-100">
            <span className="text-xs font-semibold text-gray-500 uppercase tracking-wide">Preview</span>
          </div>
          <div className="flex-1 p-4 overflow-y-auto prose prose-sm max-w-none min-h-96">
            {form.body ? (
              <ReactMarkdown>{form.body}</ReactMarkdown>
            ) : (
              <p className="text-gray-300 italic">Preview will appear here...</p>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
