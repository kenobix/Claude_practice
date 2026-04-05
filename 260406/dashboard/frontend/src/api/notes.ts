import client from './client'
import type { Note } from '../types'

export interface NoteFilters {
  project_id?: number
  task_id?: number
}

export async function getNotes(filters?: NoteFilters): Promise<Note[]> {
  const params: Record<string, number> = {}
  if (filters?.project_id) params.project_id = filters.project_id
  if (filters?.task_id) params.task_id = filters.task_id
  const res = await client.get<Note[]>('/api/notes', { params })
  return res.data
}

export async function createNote(data: Partial<Note>): Promise<Note> {
  const res = await client.post<Note>('/api/notes', data)
  return res.data
}

export async function updateNote(id: number, data: Partial<Note>): Promise<Note> {
  const res = await client.put<Note>(`/api/notes/${id}`, data)
  return res.data
}

export async function deleteNote(id: number): Promise<void> {
  await client.delete(`/api/notes/${id}`)
}
