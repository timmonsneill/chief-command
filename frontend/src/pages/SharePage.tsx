import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import { Check, Clock, AlertTriangle, RefreshCw } from 'lucide-react'
import type { Project } from '../lib/api'

const SEVERITY_COLORS: Record<string, string> = {
  CRITICAL: 'text-red-400',
  HIGH: 'text-orange-400',
  MEDIUM: 'text-yellow-400',
  LOW: 'text-white/40',
}

export default function SharePage() {
  const { slug } = useParams<{ slug: string }>()
  const [project, setProject] = useState<Project | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchProject = useCallback(async () => {
    if (!slug) return
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`/api/projects/${slug}`)
      if (!res.ok) throw new Error('Not found')
      const data = await res.json()
      setProject(data)
    } catch {
      setError('Project not found')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    fetchProject()
  }, [fetchProject])

  if (loading) {
    return (
      <div className="h-[100dvh] flex items-center justify-center bg-surface">
        <div className="text-white/30 text-sm">Loading...</div>
      </div>
    )
  }

  if (error || !project) {
    return (
      <div className="h-[100dvh] flex flex-col items-center justify-center bg-surface gap-3">
        <p className="text-white/40 text-sm">{error || 'Not found'}</p>
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

  // Group todos by category
  const todosByCategory: Record<string, typeof project.todos> = {}
  for (const todo of project.todos) {
    if (!todosByCategory[todo.category]) todosByCategory[todo.category] = []
    todosByCategory[todo.category].push(todo)
  }

  return (
    <div className="min-h-[100dvh] bg-surface flex flex-col">
      <div className="flex-1 max-w-2xl mx-auto w-full">
        {/* Header */}
        <div className="px-4 py-6 border-b border-surface-border">
          <h1 className="text-xl font-bold text-white">{project.name}</h1>
          <p className="text-sm text-white/40 mt-1">{project.description}</p>
        </div>

        <div className="px-4 py-4 space-y-6">
          {/* Phases */}
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Phases
            </h2>
            <div className="space-y-3">
              {project.phases.map((phase) => {
                const pct =
                  phase.total > 0
                    ? Math.round((phase.completed / phase.total) * 100)
                    : 0
                return (
                  <div key={phase.name}>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-sm text-white/80">
                        {phase.name}
                      </span>
                      <span className="text-xs text-white/40">
                        {phase.completed}/{phase.total} ({pct}%)
                      </span>
                    </div>
                    <div className="h-2 bg-surface-border rounded-full overflow-hidden">
                      <div
                        className={`h-full rounded-full transition-all ${
                          pct === 100 ? 'bg-status-online' : 'bg-chief'
                        }`}
                        style={{ width: `${pct}%` }}
                      />
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          {/* Todos */}
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Todos
            </h2>
            <div className="space-y-4">
              {Object.entries(todosByCategory).map(([category, todos]) => (
                <div key={category}>
                  <h3 className="text-xs font-medium text-white/40 mb-2">
                    {category}
                  </h3>
                  <div className="space-y-1">
                    {todos.map((todo) => (
                      <div
                        key={todo.id}
                        className="flex items-start gap-2.5 py-1.5"
                      >
                        <div
                          className={`w-4 h-4 rounded border shrink-0 mt-0.5 flex items-center justify-center ${
                            todo.done
                              ? 'bg-chief border-chief'
                              : 'border-surface-border'
                          }`}
                        >
                          {todo.done && (
                            <Check size={10} className="text-white" />
                          )}
                        </div>
                        <span
                          className={`text-sm leading-relaxed ${
                            todo.done
                              ? 'text-white/30 line-through'
                              : 'text-white/70'
                          }`}
                        >
                          {todo.text}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              ))}
              {project.todos.length === 0 && (
                <p className="text-xs text-white/30">No todos</p>
              )}
            </div>
          </section>

          {/* Timeline */}
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Timeline
            </h2>
            <div className="space-y-3">
              {project.timeline.slice(0, 10).map((entry) => (
                <div key={entry.id} className="flex items-start gap-3">
                  <div className="mt-1">
                    <Clock size={12} className="text-white/20" />
                  </div>
                  <div>
                    <p className="text-sm text-white/70">
                      {entry.description}
                    </p>
                    <p className="text-[10px] text-white/30 mt-0.5">
                      {new Date(entry.date).toLocaleDateString(undefined, {
                        month: 'short',
                        day: 'numeric',
                        hour: '2-digit',
                        minute: '2-digit',
                      })}
                    </p>
                  </div>
                </div>
              ))}
              {project.timeline.length === 0 && (
                <p className="text-xs text-white/30">No activity yet</p>
              )}
            </div>
          </section>

          {/* Build History */}
          <section>
            <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider mb-3">
              Build History
            </h2>
            <div className="space-y-2">
              {project.builds.slice(0, 5).map((build) => {
                const total =
                  build.findings_count.CRITICAL +
                  build.findings_count.HIGH +
                  build.findings_count.MEDIUM +
                  build.findings_count.LOW

                return (
                  <div
                    key={build.id}
                    className="p-3 rounded-xl bg-surface-raised border border-surface-border"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs text-white/40">
                        {new Date(build.timestamp).toLocaleDateString(
                          undefined,
                          {
                            month: 'short',
                            day: 'numeric',
                            hour: '2-digit',
                            minute: '2-digit',
                          }
                        )}
                      </span>
                      {total > 0 && (
                        <div className="flex items-center gap-1">
                          <AlertTriangle
                            size={10}
                            className="text-status-working"
                          />
                          <span className="text-xs text-white/40">
                            {total} findings
                          </span>
                        </div>
                      )}
                    </div>
                    <div className="flex items-center gap-3">
                      {(
                        ['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const
                      ).map((s) => {
                        const count = build.findings_count[s]
                        if (!count) return null
                        return (
                          <span
                            key={s}
                            className={`text-xs font-medium ${SEVERITY_COLORS[s]}`}
                          >
                            {count} {s.toLowerCase()}
                          </span>
                        )
                      })}
                      {total === 0 && (
                        <span className="text-xs text-status-online">
                          Clean build
                        </span>
                      )}
                    </div>
                  </div>
                )
              })}
              {project.builds.length === 0 && (
                <p className="text-xs text-white/30">No builds yet</p>
              )}
            </div>
          </section>
        </div>
      </div>

      {/* Footer */}
      <footer className="text-center py-6 border-t border-surface-border mt-4">
        <span className="text-xs text-white/20">
          Powered by Chief Command
        </span>
      </footer>
    </div>
  )
}
