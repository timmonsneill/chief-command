import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { FolderKanban, RefreshCw, ChevronRight } from 'lucide-react'
import { api, type Project } from '../lib/api'

const TYPE_BADGES: Record<string, string> = {
  web: 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  mobile: 'bg-purple-500/10 text-purple-400 border-purple-500/20',
  api: 'bg-green-500/10 text-green-400 border-green-500/20',
  infra: 'bg-orange-500/10 text-orange-400 border-orange-500/20',
  default: 'bg-white/5 text-white/40 border-white/10',
}

const STATUS_DOT: Record<string, string> = {
  active: 'bg-status-online',
  paused: 'bg-status-working',
  archived: 'bg-white/20',
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
        <div className="text-white/30 text-sm">Loading projects...</div>
      </div>
    )
  }

  if (error && projects.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error}</p>
        <button
          onClick={fetchProjects}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-white/60 text-sm active:text-white transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center gap-2">
          <FolderKanban size={18} className="text-chief" />
          <h1 className="text-lg font-semibold text-white">Projects</h1>
          <span className="text-xs text-white/30 ml-auto">
            {projects.length} total
          </span>
        </div>
      </div>

      {/* Project cards */}
      <div className="px-4 py-3 space-y-2">
        {projects.map((project) => {
          const badgeClass =
            TYPE_BADGES[project.type] || TYPE_BADGES.default
          const dotClass = STATUS_DOT[project.status] || 'bg-white/20'

          // Calculate overall progress from backend todo counts
          const totalTasks = project.todo_total ?? project.phases.reduce(
            (sum, p) => sum + p.total,
            0
          )
          const completedTasks = project.todo_done ?? project.phases.reduce(
            (sum, p) => sum + p.completed,
            0
          )
          const progressPct = project.todo_percent ??
            (totalTasks > 0 ? Math.round((completedTasks / totalTasks) * 100) : 0)

          return (
            <button
              key={project.slug}
              onClick={() => navigate(`/projects/${project.slug}`)}
              className="w-full text-left p-4 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <div className={`w-2 h-2 rounded-full ${dotClass}`} />
                    <h3 className="text-sm font-medium text-white truncate">
                      {project.name}
                    </h3>
                  </div>
                  <p className="text-xs text-white/40 line-clamp-2 ml-4">
                    {project.description}
                  </p>
                </div>
                <ChevronRight size={16} className="text-white/20 shrink-0 mt-0.5" />
              </div>

              <div className="flex items-center gap-2 mt-3">
                <span
                  className={`px-2 py-0.5 rounded-full text-[10px] font-medium border ${badgeClass}`}
                >
                  {project.type}
                </span>
                <div className="flex-1 h-1.5 bg-surface-border rounded-full overflow-hidden ml-2">
                  <div
                    className="h-full bg-chief rounded-full transition-all"
                    style={{ width: `${progressPct}%` }}
                  />
                </div>
                <span className="text-[10px] text-white/30 shrink-0">
                  {progressPct}%
                </span>
              </div>
            </button>
          )
        })}

        {projects.length === 0 && (
          <div className="text-center py-12 text-white/30 text-sm">
            No projects yet
          </div>
        )}
      </div>
    </div>
  )
}
