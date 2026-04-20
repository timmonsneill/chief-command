import { useState, useEffect, useCallback } from 'react'
import { Bot, RefreshCw, Zap, ArrowLeft, ChevronRight } from 'lucide-react'
import { api, type Agent } from '../lib/api'

const STATUS_CONFIG: Record<string, { dot: string; text: string; label: string; pill: string }> = {
  running: {
    dot: 'bg-status-working animate-pulse',
    text: 'text-status-working',
    label: 'Running',
    pill: 'bg-status-working/10 text-status-working',
  },
  completed: {
    dot: 'bg-status-online',
    text: 'text-status-online',
    label: 'Done',
    pill: 'bg-status-online/10 text-status-online',
  },
  failed: {
    dot: 'bg-status-offline',
    text: 'text-status-offline',
    label: 'Failed',
    pill: 'bg-status-offline/10 text-status-offline',
  },
}

function formatElapsed(seconds: number | null): string {
  if (seconds === null || seconds === undefined) return ''
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${s}s`
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 2) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function formatAbsoluteTime(iso: string | null): string {
  if (!iso) return ''
  return new Date(iso).toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  })
}

function agentDisplayName(agent: Agent): string {
  if (agent.name && agent.name !== agent.id) return agent.name
  return agent.subagent_type || 'Agent'
}

// ─── Detail View ─────────────────────────────────────────────────────────────

function AgentDetail({ agent, onBack }: { agent: Agent; onBack: () => void }) {
  const cfg = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.completed
  const displayName = agentDisplayName(agent)
  const elapsed = formatElapsed(agent.elapsed_seconds ?? null)

  const [showStartedAbsolute, setShowStartedAbsolute] = useState(false)
  const [showCompletedAbsolute, setShowCompletedAbsolute] = useState(false)

  const hasContent = Boolean(agent.task || agent.summary)

  return (
    <div className="h-full overflow-y-auto">
      {/* Header */}
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center gap-3">
          <button
            onClick={onBack}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-ink/40 active:text-ink/80 transition-colors shrink-0"
            aria-label="Back to agent list"
          >
            <ArrowLeft size={18} />
          </button>
          <div className="flex-1 min-w-0">
            <h1 className="font-display text-xl font-semibold text-ink leading-tight truncate">
              {displayName}
            </h1>
            {agent.subagent_type && agent.subagent_type !== displayName && (
              <p className="text-xs text-ink/40 truncate">{agent.subagent_type}</p>
            )}
          </div>
          <span className={`shrink-0 px-2.5 py-1 rounded-full text-xs font-medium ${cfg.pill}`}>
            {cfg.label}
          </span>
        </div>
      </div>

      <div className="px-4 py-4 space-y-4">
        {/* Meta strip */}
        <div className="p-3 rounded-xl bg-surface-raised border border-surface-border space-y-2.5">
          {agent.model && (
            <MetaRow label="Model">
              <span className="px-2 py-0.5 rounded bg-surface-overlay text-ink/70 text-xs font-mono">
                {agent.model}
              </span>
            </MetaRow>
          )}

          {agent.started_at && (
            <MetaRow label="Started">
              <button
                onClick={() => setShowStartedAbsolute((v) => !v)}
                className="text-xs text-ink/60 hover:text-ink/80 transition-colors"
              >
                {showStartedAbsolute
                  ? formatAbsoluteTime(agent.started_at)
                  : formatRelativeTime(agent.started_at)}
              </button>
            </MetaRow>
          )}

          {agent.completed_at && (
            <MetaRow label="Completed">
              <button
                onClick={() => setShowCompletedAbsolute((v) => !v)}
                className="text-xs text-ink/60 hover:text-ink/80 transition-colors"
              >
                {showCompletedAbsolute
                  ? formatAbsoluteTime(agent.completed_at)
                  : formatRelativeTime(agent.completed_at)}
              </button>
            </MetaRow>
          )}

          {elapsed && (
            <MetaRow label="Elapsed">
              <span className="text-xs text-ink/60">{elapsed}</span>
            </MetaRow>
          )}

          {agent.worktree_path && (
            <MetaRow label="Worktree">
              <span className="text-[11px] font-mono text-ink/50 break-all">
                {agent.worktree_path}
              </span>
            </MetaRow>
          )}
        </div>

        {/* Task section */}
        {agent.task && (
          <section>
            <h2 className="text-[11px] font-medium text-ink/30 uppercase tracking-wider mb-2 px-1">
              Task
            </h2>
            <div className="p-4 rounded-xl bg-surface-raised border border-surface-border">
              <p className="text-sm text-ink/80 leading-relaxed whitespace-pre-wrap">
                {agent.task}
              </p>
            </div>
          </section>
        )}

        {/* Summary section */}
        {agent.summary && (
          <section>
            <h2 className="text-[11px] font-medium text-ink/30 uppercase tracking-wider mb-2 px-1">
              Summary
            </h2>
            <div className="p-4 rounded-xl bg-surface-raised border border-surface-border">
              <p className="text-sm text-ink/80 leading-relaxed whitespace-pre-wrap">
                {agent.summary}
              </p>
            </div>
          </section>
        )}

        {!hasContent && (
          <p className="text-center text-ink/20 text-sm py-4">No task or summary recorded</p>
        )}

        {/* Raw JSON expander */}
        <details className="group">
          <summary className="flex items-center gap-2 cursor-pointer list-none px-3 py-2.5 rounded-xl bg-surface-raised border border-surface-border text-xs text-ink/30 hover:text-ink/50 transition-colors select-none">
            <ChevronRight
              size={13}
              className="transition-transform duration-200 group-open:rotate-90"
            />
            View raw
          </summary>
          <div className="mt-2 p-4 rounded-xl bg-surface-raised border border-surface-border overflow-x-auto">
            <pre className="text-[11px] font-mono text-ink/40 leading-relaxed whitespace-pre">
              {JSON.stringify(agent, null, 2)}
            </pre>
          </div>
        </details>
      </div>
    </div>
  )
}

function MetaRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-3">
      <span className="text-[11px] text-ink/30 w-20 shrink-0 pt-0.5">{label}</span>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  )
}

// ─── List View ────────────────────────────────────────────────────────────────

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [viewingAgent, setViewingAgent] = useState<Agent | null>(null)

  const fetchAgents = useCallback(async () => {
    setError('')
    try {
      const data = await api.agents.list()
      setAgents(data)
      // Silently update detail view if the agent is refreshed
      setViewingAgent((current) => {
        if (!current) return null
        const updated = data.find((a) => a.id === current.id)
        return updated ?? current
      })
    } catch {
      setError('Failed to load agents')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchAgents()
    const interval = setInterval(fetchAgents, 3000)
    return () => clearInterval(interval)
  }, [fetchAgents])

  const runningCount = agents.filter((a) => a.status === 'running').length

  if (loading && agents.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-ink/30 text-sm">Loading agents...</div>
      </div>
    )
  }

  if (error && agents.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-ink/40 text-sm">{error}</p>
        <button
          onClick={fetchAgents}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-ink/60 text-sm active:text-ink transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  // Detail view
  if (viewingAgent) {
    return (
      <AgentDetail
        agent={viewingAgent}
        onBack={() => setViewingAgent(null)}
      />
    )
  }

  // List view
  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-chief" />
            <h1 className="font-display text-lg font-semibold text-ink">Agents</h1>
          </div>
          <div className="flex items-center gap-2">
            {runningCount > 0 && (
              <span className="flex items-center gap-1.5 px-2 py-0.5 rounded-full bg-status-working/10 text-status-working text-xs font-medium">
                <Zap size={10} />
                {runningCount} running
              </span>
            )}
            <button
              onClick={fetchAgents}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-ink/30 active:text-ink/60 transition-colors"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
      </div>

      <div className="px-4 py-3 space-y-2">
        {agents.map((agent) => {
          const cfg = STATUS_CONFIG[agent.status] ?? STATUS_CONFIG.completed
          const displayName = agentDisplayName(agent)
          const elapsed = formatElapsed(agent.elapsed_seconds ?? null)
          const lastActive = formatRelativeTime(agent.last_active ?? null)

          return (
            <button
              key={agent.id}
              onClick={() => setViewingAgent(agent)}
              className="w-full text-left p-3 rounded-xl bg-surface-raised border border-surface-border cursor-pointer active:bg-surface-overlay transition-colors group"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
                    <span className="text-sm font-medium text-ink truncate">
                      {displayName}
                    </span>
                    {agent.subagent_type && agent.subagent_type !== displayName && (
                      <span className="text-[10px] text-ink/30 shrink-0">
                        {agent.subagent_type}
                      </span>
                    )}
                  </div>
                  {agent.task && (
                    <p className="text-xs text-ink/40 mt-1 ml-4 leading-relaxed line-clamp-2">
                      {agent.task}
                    </p>
                  )}
                  {!agent.task && agent.summary && (
                    <p className="text-xs text-ink/40 mt-1 ml-4 leading-relaxed line-clamp-2">
                      {agent.summary}
                    </p>
                  )}
                  {agent.worktree_path && (
                    <p className="text-[10px] text-ink/20 mt-0.5 ml-4 truncate">
                      {agent.worktree_path.replace(/.*\//, '')}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <span className={`text-xs font-medium ${cfg.text}`}>
                    {cfg.label}
                  </span>
                  {elapsed && (
                    <span className="text-[10px] text-ink/30">{elapsed}</span>
                  )}
                  {lastActive && (
                    <span className="text-[10px] text-ink/20">{lastActive}</span>
                  )}
                  <ChevronRight
                    size={13}
                    className="text-ink/20 group-hover:text-ink/40 transition-colors mt-0.5"
                  />
                </div>
              </div>
            </button>
          )
        })}

        {agents.length === 0 && !loading && (
          <div className="text-center py-12 text-ink/30 text-sm">
            No agent activity found
          </div>
        )}
      </div>
    </div>
  )
}
