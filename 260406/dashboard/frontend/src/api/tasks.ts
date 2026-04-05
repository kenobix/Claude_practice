import client from './client'
import type { Task, TimeLog } from '../types'

export interface TaskFilters {
  status?: string
  priority?: string
  project_id?: number
}

export async function getTasks(filters?: TaskFilters): Promise<Task[]> {
  const params: Record<string, string | number> = {}
  if (filters?.status) params.status = filters.status
  if (filters?.priority) params.priority = filters.priority
  if (filters?.project_id) params.project_id = filters.project_id
  const res = await client.get<Task[]>('/api/tasks', { params })
  return res.data
}

export async function createTask(data: Partial<Task>): Promise<Task> {
  const res = await client.post<Task>('/api/tasks', data)
  return res.data
}

export async function updateTask(id: number, data: Partial<Task>): Promise<Task> {
  const res = await client.put<Task>(`/api/tasks/${id}`, data)
  return res.data
}

export async function deleteTask(id: number): Promise<void> {
  await client.delete(`/api/tasks/${id}`)
}

export async function startTimer(taskId: number): Promise<void> {
  await client.post(`/api/tasks/${taskId}/timer/start`)
}

export async function stopTimer(taskId: number): Promise<void> {
  await client.post(`/api/tasks/${taskId}/timer/stop`)
}

export async function getTimeLogs(taskId: number): Promise<TimeLog[]> {
  const res = await client.get<TimeLog[]>(`/api/tasks/${taskId}/timelogs`)
  return res.data
}
