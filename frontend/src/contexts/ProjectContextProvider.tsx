import { createContext, useState, useEffect, useCallback, useRef, ReactNode } from 'react'
import { api, WsEvent } from '../lib/api'
import { useWebSocket } from '../hooks/useWebSocket'

// Scope is always a concrete single project — no "All" mode (owner design
// decision — the project switcher is the only way to change focus).
// Archie is the AI brain layer inside Arch (same project, not a separate
// scope). Personal Assist is the third canonical scope.
const FALLBACK_PROJECTS = ['Chief Command', 'Arch', 'Personal Assist'] as const
const DEFAULT_PROJECT = 'Chief Command'

// Dissolved-scope migration: "Archie" used to be its own scope. It isn't
// anymore — it's the brain layer inside Arch. Any stale persisted value that
// still reads "Archie" must be remapped on read so the picker shows the
// canonical name. Mirror of backend `_migrate_dissolved_scope` in
// backend/app/websockets.py.
function migrateDissolvedScope(value: string | undefined | null): string | null {
  if (!value) return null
  if (value === 'Archie') return 'Arch'
  return value
}

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
  const [current, setCurrent] = useState<string>(DEFAULT_PROJECT)
  const [available, setAvailable] = useState<string[]>([...FALLBACK_PROJECTS])
  const [isLoading, setIsLoading] = useState(true)
  const previousRef = useRef<string>(DEFAULT_PROJECT)
  const availableRef = useRef<string[]>([...FALLBACK_PROJECTS])

  useEffect(() => {
    availableRef.current = available
  }, [available])

  // Hydrate once on mount via HTTP.
  useEffect(() => {
    let cancelled = false
    api.context
      .get()
      .then((data) => {
        if (cancelled) return
        const migrated = migrateDissolvedScope(data.current) ?? DEFAULT_PROJECT
        setCurrent(migrated)
        previousRef.current = migrated
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

  // Live push: subscribe to the voice WS for `context_switched` frames. The
  // backend emits this whenever the owner says "switch to X" (or a bare name)
  // during a voice turn. Without this listener the server switched scope but
  // the UI pill stayed stale — classic one-way state. The optimistic
  // setContext path below still works; this is a second, server-authoritative
  // input that keeps the pill in sync no matter who moved scope first.
  //
  // We attach via useWebSocket to match the pattern already in Layout /
  // VoicePage / TerminalPage — not inventing a new bus. This creates a
  // subscription-only WS connection (no audio, no text turns) — the backend's
  // `ensure_session` guard means it does NOT create a usage session row.
  const onMessage = useCallback((raw: string) => {
    let parsed: WsEvent
    try {
      parsed = JSON.parse(raw) as WsEvent
    } catch {
      return
    }
    if (parsed.type !== 'context_switched') return
    const migrated = migrateDissolvedScope(parsed.project) ?? DEFAULT_PROJECT
    // Defense-in-depth: ignore a frame that names a project the client
    // doesn't know about. Protects against a bad server frame flipping the
    // pill to an arbitrary string.
    if (!availableRef.current.includes(migrated)) return
    setCurrent(migrated)
    previousRef.current = migrated
  }, [])

  useWebSocket({
    path: '/ws/voice',
    autoConnect: true,
    onMessage,
  })

  const setContext = useCallback(async (project: string) => {
    const prev = previousRef.current
    const migrated = migrateDissolvedScope(project) ?? DEFAULT_PROJECT
    setCurrent(migrated)
    previousRef.current = migrated
    try {
      const data = await api.context.set(migrated)
      const confirmed = migrateDissolvedScope(data.current) ?? DEFAULT_PROJECT
      setCurrent(confirmed)
      previousRef.current = confirmed
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
