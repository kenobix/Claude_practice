import client from './client'
import type { Project } from '../types'

export async function getProjects(): Promise<Project[]> {
  const res = await client.get<Project[]>('/api/projects')
  return res.data
}

export async function createProject(data: Partial<Project>): Promise<Project> {
  const res = await client.post<Project>('/api/projects', data)
  return res.data
}

export async function updateProject(id: number, data: Partial<Project>): Promise<Project> {
  const res = await client.put<Project>(`/api/projects/${id}`, data)
  return res.data
}

export async function deleteProject(id: number): Promise<void> {
  await client.delete(`/api/projects/${id}`)
}
