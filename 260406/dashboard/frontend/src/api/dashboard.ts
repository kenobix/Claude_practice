import client from './client'
import type { Dashboard } from '../types'

export async function getDashboard(): Promise<Dashboard> {
  const res = await client.get<Dashboard>('/api/dashboard')
  return res.data
}
