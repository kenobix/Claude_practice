import { useEffect, useState, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { getNotes, deleteNote } from '../api/notes'
import type { Note } from '../types'

export default function NotesPage() {
  const [notes, setNotes] = useState<Note[]>([])
  const [loading, setLoading] = useState(true)
  const [search, setSearch] = useState('')
  const navigate = useNavigate()

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setNotes(await getNotes())
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    load()
  }, [load])

  const handleDelete = async (e: React.MouseEvent, id: number) => {
    e.stopPropagation()
    if (!confirm('Delete this note?')) return
    await deleteNote(id)
    setNotes((prev) => prev.filter((n) => n.id !== id))
  }

  const filtered = search.trim()
    ? notes.filter((n) => n.title.toLowerCase().includes(search.toLowerCase()))
    : notes

  return (
    <div className="max-w-4xl mx-auto pb-20 lg:pb-0">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-2xl font-bold text-gray-900">Notes</h1>
        <button
          onClick={() => navigate('/notes/new')}
          className="bg-indigo-600 text-white px-4 py-2 rounded-lg text-sm font-semibold hover:bg-indigo-700 transition-colors"
        >
          + New Note
        </button>
      </div>

      {/* Search */}
      <div className="mb-5">
        <input
          type="text"
          placeholder="Search notes by title..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="w-full border border-gray-300 rounded-lg px-4 py-2.5 text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400 bg-white"
        />
      </div>

      {loading ? (
        <div className="text-center text-gray-400 py-16">Loading...</div>
      ) : filtered.length === 0 ? (
        <div className="text-center text-gray-400 py-16 bg-white rounded-xl border border-gray-200">
          {search ? 'No notes match your search' : 'No notes yet. Create one!'}
        </div>
      ) : (
        <div className="space-y-3">
          {filtered.map((note) => (
            <div
              key={note.id}
              className="bg-white rounded-xl border border-gray-200 p-4 shadow-sm hover:shadow-md transition-shadow cursor-pointer group"
              onClick={() => navigate(`/notes/${note.id}`)}
            >
              <div className="flex items-start justify-between">
                <div className="flex-1 min-w-0">
                  <h3 className="font-semibold text-gray-900 truncate">{note.title || 'Untitled'}</h3>
                  {note.body && (
                    <p className="text-sm text-gray-500 mt-1 line-clamp-2">
                      {note.body.substring(0, 100)}
                    </p>
                  )}
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {note.tags.map((tag) => (
                      <span key={tag} className="text-xs bg-indigo-50 text-indigo-600 px-2 py-0.5 rounded-full font-medium">
                        {tag}
                      </span>
                    ))}
                    <span className="text-xs text-gray-400">
                      {new Date(note.updated_at).toLocaleDateString()}
                    </span>
                  </div>
                </div>
                <button
                  onClick={(e) => handleDelete(e, note.id)}
                  className="ml-4 text-gray-300 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all text-sm shrink-0"
                >
                  ✕
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
