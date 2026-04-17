import { useState, useEffect, useCallback } from 'react'
import { Bot, RefreshCw, Zap } from 'lucide-react'
import { api, type Agent } from '../lib/api'

const STATUS_CONFIG: Record<string, { dot: string; text: string; label: string }> = {
  running: { dot: 'bg-status-working animate-pulse', text: 'text-status-working', label: 'Running' },
  completed: { dot: 'bg-status-online', text: 'text-status-online', label: 'Done' },
  failed: { dot: 'bg-status-offline', text: 'text-status-offline', label: 'Failed' },
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

function agentDisplayName(agent: Agent): string {
  if (agent.name && agent.name !== agent.id) return agent.name
  return agent.subagent_type || 'Agent'
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchAgents = useCallback(async () => {
    setError('')
    try {
      const data = await api.agents.list()
      setAgents(data)
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
        <div className="text-white/30 text-sm">Loading agents...</div>
      </div>
    )
  }

  if (error && agents.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error}</p>
        <button
          onClick={fetchAgents}
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
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-chief" />
            <h1 className="text-lg font-semibold text-white">Agents</h1>
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
              className="w-8 h-8 flex items-center justify-center rounded-lg text-white/30 active:text-white/60 transition-colors"
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
            <div
              key={agent.id}
              className="p-3 rounded-xl bg-surface-raised border border-surface-border"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <div className={`w-2 h-2 rounded-full shrink-0 ${cfg.dot}`} />
                    <span className="text-sm font-medium text-white truncate">
                      {displayName}
                    </span>
                    {agent.subagent_type && agent.subagent_type !== displayName && (
                      <span className="text-[10px] text-white/30 shrink-0">
                        {agent.subagent_type}
                      </span>
                    )}
                  </div>
                  {agent.summary && (
                    <p className="text-xs text-white/40 mt-1 ml-4 leading-relaxed line-clamp-2">
                      {agent.summary}
                    </p>
                  )}
                  {agent.worktree_path && (
                    <p className="text-[10px] text-white/20 mt-0.5 ml-4 truncate">
                      {agent.worktree_path.replace(/.*\//, '')}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <span className={`text-xs font-medium ${cfg.text}`}>
                    {cfg.label}
                  </span>
                  {elapsed && (
                    <span className="text-[10px] text-white/30">{elapsed}</span>
                  )}
                  {lastActive && (
                    <span className="text-[10px] text-white/20">{lastActive}</span>
                  )}
                </div>
              </div>
            </div>
          )
        })}

        {agents.length === 0 && !loading && (
          <div className="text-center py-12 text-white/30 text-sm">
            No agent activity found
          </div>
        )}
      </div>
    </div>
  )
}
