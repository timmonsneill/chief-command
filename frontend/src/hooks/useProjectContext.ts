import { useState, useEffect, useCallback } from 'react'
import { api } from '../lib/api'

const AVAILABLE_PROJECTS = ['All', 'Arch', 'Chief Command', 'Butler', 'Archie'] as const

interface ProjectContextValue {
  current: string
  available: string[]
  setContext: (project: string) => Promise<void>
  isLoading: boolean
}

export function useProjectContext(): ProjectContextValue {
  const [current, setCurrent] = useState<string>('All')
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    api.context
      .get()
      .then((data) => {
        setCurrent(data.current)
      })
      .catch(() => {
        // Server unreachable or unauthenticated — fall back to default
        setCurrent('All')
      })
      .finally(() => {
        setIsLoading(false)
      })
  }, [])

  const setContext = useCallback(async (project: string) => {
    // Optimistic update
    setCurrent(project)
    try {
      const data = await api.context.set(project)
      setCurrent(data.current)
    } catch {
      // Revert on failure is not attempted — the optimistic value is acceptable
      // since server state resets on restart anyway
    }
  }, [])

  return {
    current,
    available: [...AVAILABLE_PROJECTS],
    setContext,
    isLoading,
  }
}
