import { createContext, useContext, useState, useEffect, useCallback, ReactNode } from 'react'
import { api } from '../lib/api'

const FALLBACK_PROJECTS = ['All', 'Arch', 'Chief Command', 'Butler', 'Archie'] as const

export interface ProjectContextValue {
  current: string
  available: string[]
  isLoading: boolean
  setContext: (project: string) => Promise<void>
}

export const ProjectContext = createContext<ProjectContextValue | null>(null)

export function useProjectContextValue(): ProjectContextValue {
  return useContext(ProjectContext) as ProjectContextValue
}

interface Props {
  children: ReactNode
}

export function ProjectContextProvider({ children }: Props) {
  const [current, setCurrent] = useState<string>('All')
  const [available, setAvailable] = useState<string[]>([...FALLBACK_PROJECTS])
  const [isLoading, setIsLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    api.context
      .get()
      .then((data) => {
        if (cancelled) return
        setCurrent(data.current)
        // Server returns available list; fall back to constant if empty
        if (data.available && data.available.length > 0) {
          setAvailable(data.available)
        }
      })
      .catch(() => {
        // Server unreachable or unauthenticated — keep fallback defaults
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const setContext = useCallback(async (project: string) => {
    // Optimistic update
    setCurrent(project)
    try {
      const data = await api.context.set(project)
      setCurrent(data.current)
    } catch {
      // Optimistic value stays — server state resets on restart anyway
    }
  }, [])

  return (
    <ProjectContext.Provider value={{ current, available, isLoading, setContext }}>
      {children}
    </ProjectContext.Provider>
  )
}
