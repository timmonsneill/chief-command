import { createContext, useState, useEffect, useCallback, useRef, ReactNode } from 'react'
import { api } from '../lib/api'

const FALLBACK_PROJECTS = ['All', 'Arch', 'Chief Command', 'Butler', 'Archie'] as const

export interface ProjectContextValue {
  current: string
  available: string[]
  isLoading: boolean
  setContext: (project: string) => Promise<void>
}

export const ProjectContext = createContext<ProjectContextValue | null>(null)

interface Props {
  children: ReactNode
}

export function ProjectContextProvider({ children }: Props) {
  const [current, setCurrent] = useState<string>('All')
  const [available, setAvailable] = useState<string[]>([...FALLBACK_PROJECTS])
  const [isLoading, setIsLoading] = useState(true)
  const previousRef = useRef<string>('All')

  useEffect(() => {
    let cancelled = false
    api.context
      .get()
      .then((data) => {
        if (cancelled) return
        setCurrent(data.current)
        previousRef.current = data.current
        if (data.available && data.available.length > 0) {
          setAvailable(data.available)
        }
      })
      .catch(() => {
        // Server unreachable — keep fallback defaults
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  const setContext = useCallback(async (project: string) => {
    const prev = previousRef.current
    setCurrent(project)
    previousRef.current = project
    try {
      const data = await api.context.set(project)
      setCurrent(data.current)
      previousRef.current = data.current
    } catch {
      // Roll back optimistic update on failure so UI matches server state
      setCurrent(prev)
      previousRef.current = prev
    }
  }, [])

  return (
    <ProjectContext.Provider value={{ current, available, isLoading, setContext }}>
      {children}
    </ProjectContext.Provider>
  )
}
