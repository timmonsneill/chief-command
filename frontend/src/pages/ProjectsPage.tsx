import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderKanban, RefreshCw, ChevronRight } from 'lucide-react'
import { api, type Project } from '../lib/api'

const STATUS_CONFIG: Record<string, { dot: string; label: string }> = {
  active: { dot: 'bg-status-online', label: 'Active' },
  paused: { dot: 'bg-status-working', label: 'Paused' },
  done: { dot: 'bg-ink/30', label: 'Done' },
}

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'No activity'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 2) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const fetchProjects = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await api.projects.list()
      setProjects(data)
    } catch {
      setError('Failed to load projects')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchProjects()
  }, [fetchProjects])

  if (loading && projects.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-ink/30 text-sm">Loading projects...</div>
      </div>
    )
  }

  if (error && projects.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-ink/40 text-sm">{error}</p>
        <button
          onClick={fetchProjects}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-ink/60 text-sm active:text-ink transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center gap-2">
          <FolderKanban size={18} className="text-chief" />
          <h1 className="font-display text-lg font-semibold text-ink">Projects</h1>
          <span className="text-xs text-ink/30 ml-auto">
            {projects.length} project{projects.length !== 1 ? 's' : ''}
          </span>
          <button
            onClick={fetchProjects}
            className="w-7 h-7 flex items-center justify-center rounded-lg text-ink/30 active:text-ink/60 transition-colors"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>
      </div>

      <div className="px-4 py-3 space-y-2">
        {projects.map((project) => {
          const cfg = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.active
          const pct = project.todo_percent ?? 0
          const openTodos = (project.todo_total ?? 0) - (project.todo_done ?? 0)

          return (
            <button
              key={project.slug}
              onClick={() => navigate(`/projects/${project.slug}`)}
              className="w-full text-left p-4 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
                    <h3 className="text-sm font-medium text-ink truncate">
                      {project.name}
                    </h3>
                    <span className="text-[10px] text-ink/30 shrink-0">
                      {cfg.label}
                    </span>
                  </div>
                  {project.description && (
                    <p className="text-xs text-ink/40 line-clamp-2 ml-4">
                      {project.description}
                    </p>
                  )}
                </div>
                <ChevronRight size={16} className="text-ink/20 shrink-0 mt-0.5 ml-2" />
              </div>

              <div className="flex items-center gap-3 mt-3 ml-4">
                <div className="flex-1 h-1.5 bg-surface-border rounded-full overflow-hidden">
                  <div
                    className="h-full bg-chief rounded-full transition-all"
                    style={{ width: `${pct}%` }}
                  />
                </div>
                <span className="text-[10px] text-ink/30 shrink-0 w-8 text-right">
                  {pct}%
                </span>
                {openTodos > 0 && (
                  <span className="text-[10px] text-ink/30 shrink-0">
                    {openTodos} open
                  </span>
                )}
                <span className="text-[10px] text-ink/20 shrink-0">
                  {formatRelativeTime(project.last_activity)}
                </span>
              </div>
            </button>
          )
        })}

        {projects.length === 0 && !loading && (
          <div className="text-center py-12 text-ink/30 text-sm">
            No projects configured
          </div>
        )}
      </div>
    </div>
  )
}
