import { useState, useEffect, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  Share2,
  RefreshCw,
  GitCommit,
  Calendar,
  CheckSquare,
  Square,
  Activity,
} from 'lucide-react'
import { toast } from 'sonner'
import { api, type Project } from '../lib/api'

const STATUS_CONFIG: Record<string, { bg: string; text: string; label: string }> = {
  active: { bg: 'bg-status-online/10', text: 'text-status-online', label: 'Active' },
  paused: { bg: 'bg-status-working/10', text: 'text-status-working', label: 'Paused' },
  done: { bg: 'bg-white/5', text: 'text-white/40', label: 'Done' },
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

export default function ProjectDashboard() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchProject = useCallback(async () => {
    if (!slug) return
    setLoading(true)
    setError('')
    try {
      const data = await api.projects.get(slug)
      setProject(data)
    } catch {
      setError('Failed to load project')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    fetchProject()
  }, [fetchProject])

  function handleShare() {
    const url = `${window.location.origin}/share/${slug}`
    navigator.clipboard.writeText(url).then(() => {
      toast.success('Share link copied')
    })
  }

  if (loading && !project) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-white/30 text-sm">Loading project...</div>
      </div>
    )
  }

  if (error || !project) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error || 'Project not found'}</p>
        <button
          onClick={fetchProject}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-white/60 text-sm active:text-white transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  const statusCfg = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.active

  const todos = (project.todos || []) as { text: string; done: boolean }[]
  const openTodos = todos.filter((t) => !t.done)
  const doneTodos = todos.filter((t) => t.done)

  const phases = (project.phases || []) as unknown as {
    name: string
    complete: boolean
    percent: number
    total: number
    completed: number
  }[]

  const recentActivity = project.recent_activity || []
  const milestones = project.milestones || []
  const builds = project.builds || []
  const progress = project.todo_progress ?? {
    total: todos.length,
    done: doneTodos.length,
    percent: todos.length > 0 ? Math.round((doneTodos.length / todos.length) * 100) : 0,
  }

  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center gap-3">
          <button
            onClick={() => navigate('/projects')}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-white/40 active:text-white transition-colors"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="text-base font-semibold text-white truncate">{project.name}</h1>
          </div>
          <button
            onClick={handleShare}
            className="w-9 h-9 flex items-center justify-center rounded-lg text-white/40 active:text-chief transition-colors"
          >
            <Share2 size={16} />
          </button>
        </div>
      </div>

      <div className="px-4 py-4 space-y-5">
        {/* Header card */}
        <div className="p-4 rounded-xl bg-surface-raised border border-surface-border">
          <div className="flex items-start justify-between gap-3 mb-3">
            <div className="flex-1 min-w-0">
              <h2 className="text-base font-semibold text-white">{project.name}</h2>
              {project.description && (
                <p className="text-sm text-white/50 mt-1 leading-relaxed">{project.description}</p>
              )}
            </div>
            <span
              className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-medium ${statusCfg.bg} ${statusCfg.text}`}
            >
              {statusCfg.label}
            </span>
          </div>
          <div className="flex items-center gap-3">
            <div className="flex-1 h-2 bg-surface-border rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all ${
                  progress.percent === 100 ? 'bg-status-online' : 'bg-chief'
                }`}
                style={{ width: `${progress.percent}%` }}
              />
            </div>
            <span className="text-xs text-white/40 shrink-0">
              {progress.done}/{progress.total} ({progress.percent}%)
            </span>
          </div>
        </div>

        {/* Phases */}
        {phases.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Phases
            </h2>
            <div className="space-y-3">
              {phases.map((phase) => (
                <div key={phase.name} className="p-3 rounded-xl bg-surface-raised border border-surface-border">
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <div
                        className={`w-1.5 h-1.5 rounded-full ${
                          phase.complete ? 'bg-status-online' : 'bg-chief'
                        }`}
                      />
                      <span className="text-sm text-white/80">{phase.name}</span>
                    </div>
                    <span className="text-xs text-white/40">
                      {phase.completed}/{phase.total} · {phase.percent}%
                    </span>
                  </div>
                  <div className="h-1.5 bg-surface-border rounded-full overflow-hidden">
                    <div
                      className={`h-full rounded-full transition-all ${
                        phase.complete ? 'bg-status-online' : 'bg-chief'
                      }`}
                      style={{ width: `${phase.percent}%` }}
                    />
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}

        {/* Todos — two column open/done */}
        {todos.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Todos
            </h2>
            <div className="grid grid-cols-2 gap-3">
              <div className="p-3 rounded-xl bg-surface-raised border border-surface-border">
                <div className="flex items-center gap-1.5 mb-2">
                  <Square size={12} className="text-white/40" />
                  <span className="text-xs font-medium text-white/50">Open ({openTodos.length})</span>
                </div>
                <div className="space-y-1.5">
                  {openTodos.slice(0, 15).map((todo, i) => (
                    <p key={i} className="text-xs text-white/70 leading-snug">
                      {todo.text}
                    </p>
                  ))}
                  {openTodos.length === 0 && (
                    <p className="text-xs text-white/25 italic">All done</p>
                  )}
                  {openTodos.length > 15 && (
                    <p className="text-xs text-white/30">+{openTodos.length - 15} more</p>
                  )}
                </div>
              </div>
              <div className="p-3 rounded-xl bg-surface-raised border border-surface-border">
                <div className="flex items-center gap-1.5 mb-2">
                  <CheckSquare size={12} className="text-status-online" />
                  <span className="text-xs font-medium text-white/50">Done ({doneTodos.length})</span>
                </div>
                <div className="space-y-1.5">
                  {doneTodos.slice(0, 15).map((todo, i) => (
                    <p key={i} className="text-xs text-white/30 line-through leading-snug">
                      {todo.text}
                    </p>
                  ))}
                  {doneTodos.length === 0 && (
                    <p className="text-xs text-white/25 italic">Nothing done yet</p>
                  )}
                  {doneTodos.length > 15 && (
                    <p className="text-xs text-white/30">+{doneTodos.length - 15} more</p>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {/* Recent Activity */}
        <section>
          <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3 flex items-center gap-1.5">
            <Activity size={11} />
            Recent Activity
          </h2>
          {recentActivity.length > 0 ? (
            <div className="space-y-2">
              {recentActivity.map((commit, i) => (
                <div key={i} className="flex items-start gap-3 py-1.5">
                  <GitCommit size={12} className="text-white/20 mt-0.5 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-xs text-white/70 leading-snug truncate">{commit.message}</p>
                    <p className="text-[10px] text-white/30 mt-0.5">
                      {commit.hash && <span className="font-mono mr-1">{commit.hash}</span>}
                      {formatDateTime(commit.date)}
                    </p>
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-white/30">No activity yet</p>
          )}
        </section>

        {/* Build History */}
        {builds.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Build History
            </h2>
            <div className="space-y-2">
              {builds.slice(0, 5).map((build) => {
                const b = build as unknown as Record<string, unknown>
                const fc = (b.findings_count as Record<string, number>) ?? {}
                const total = Object.values(fc).reduce((s, v) => s + v, 0)
                return (
                  <div
                    key={b.id as string}
                    className="p-3 rounded-xl bg-surface-raised border border-surface-border"
                  >
                    <div className="flex items-center justify-between">
                      <span className="text-xs text-white/40">
                        {formatDateTime(b.timestamp as string)}
                      </span>
                      {total === 0 ? (
                        <span className="text-xs text-status-online">Clean</span>
                      ) : (
                        <span className="text-xs text-white/40">{total} findings</span>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>
        )}

        {/* Timeline / Milestones */}
        {milestones.length > 0 && (
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3 flex items-center gap-1.5">
              <Calendar size={11} />
              Timeline
            </h2>
            <div className="space-y-3">
              {milestones.map((m, i) => (
                <div key={i} className="flex items-start gap-3">
                  <div className="mt-1 w-1.5 h-1.5 rounded-full bg-chief/50 shrink-0" />
                  <div>
                    <p className="text-sm text-white/70 leading-snug">{m.label}</p>
                    <p className="text-[10px] text-white/30 mt-0.5">{formatDate(m.date)}</p>
                  </div>
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  )
}
