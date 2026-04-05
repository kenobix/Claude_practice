import { useState, useEffect } from 'react'
import type { ActiveTimer } from '../types'

export function useTimer(activeTimer: ActiveTimer | null): number {
  const [elapsed, setElapsed] = useState<number>(activeTimer?.elapsed_seconds ?? 0)

  useEffect(() => {
    if (!activeTimer) {
      setElapsed(0)
      return
    }

    setElapsed(activeTimer.elapsed_seconds)

    const interval = setInterval(() => {
      setElapsed((prev) => prev + 1)
    }, 1000)

    return () => clearInterval(interval)
  }, [activeTimer])

  return elapsed
}

export function formatElapsed(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':')
}

export function formatHHMM(seconds: number): string {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}`
}
