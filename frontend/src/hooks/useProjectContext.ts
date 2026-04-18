import { useContext } from 'react'
import { ProjectContext, ProjectContextValue } from '../contexts/ProjectContextProvider'

export type { ProjectContextValue }

export function useProjectContext(): ProjectContextValue {
  const ctx = useContext(ProjectContext)
  if (!ctx) {
    throw new Error('useProjectContext must be used inside <ProjectContextProvider>')
  }
  return ctx
}
