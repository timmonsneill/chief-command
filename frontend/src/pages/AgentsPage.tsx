import { useState, useEffect, useCallback } from 'react'
import { Bot, ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { api, type Agent, type ReviewSweep, type ReviewFinding } from '../lib/api'

const STATUS_COLORS: Record<Agent['status'], string> = {
  working: 'bg-status-working',
  complete: 'bg-status-online',
  idle: 'bg-white/20',
}

const STATUS_TEXT_COLORS: Record<Agent['status'], string> = {
  working: 'text-status-working',
  complete: 'text-status-online',
  idle: 'text-white/40',
}

const SEVERITY_COLORS: Record<ReviewFinding['severity'], string> = {
  CRITICAL: 'bg-red-500/10 border-red-500/30 text-red-400',
  HIGH: 'bg-orange-500/10 border-orange-500/30 text-orange-400',
  MEDIUM: 'bg-yellow-500/10 border-yellow-500/30 text-yellow-400',
  LOW: 'bg-white/5 border-white/10 text-white/50',
}

const SEVERITY_DOT: Record<ReviewFinding['severity'], string> = {
  CRITICAL: 'bg-red-500',
  HIGH: 'bg-orange-500',
  MEDIUM: 'bg-yellow-500',
  LOW: 'bg-white/30',
}

function formatDuration(seconds: number | null): string {
  if (!seconds) return '--'
  if (seconds < 60) return `${Math.round(seconds)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.round(seconds % 60)
  return `${m}m ${s}s`
}

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([])
  const [reviews, setReviews] = useState<ReviewSweep[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedSeverity, setExpandedSeverity] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const [agentList, reviewList] = await Promise.all([
        api.agents.list(),
        api.agents.recentReviews(),
      ])
      setAgents(agentList)
      setReviews(reviewList)
    } catch {
      setError('Failed to load agents')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [fetchData])

  const activeCount = agents.filter((a) => a.status === 'working').length

  // Group latest review findings by severity
  const latestReview = reviews[0]
  const findingsBySeverity = (latestReview?.findings || []).reduce(
    (acc, f) => {
      if (!acc[f.severity]) acc[f.severity] = []
      acc[f.severity].push(f)
      return acc
    },
    {} as Record<string, ReviewFinding[]>
  )

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
          onClick={fetchData}
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
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot size={18} className="text-chief" />
            <h1 className="text-lg font-semibold text-white">Agents</h1>
          </div>
          <div className="flex items-center gap-2">
            {activeCount > 0 && (
              <span className="px-2 py-0.5 rounded-full bg-status-working/10 text-status-working text-xs font-medium">
                {activeCount} active
              </span>
            )}
            <button
              onClick={fetchData}
              className="w-8 h-8 flex items-center justify-center rounded-lg text-white/30 active:text-white/60 transition-colors"
            >
              <RefreshCw size={14} />
            </button>
          </div>
        </div>
      </div>

      {/* Agent list */}
      <div className="px-4 py-3 space-y-2">
        {agents.map((agent) => (
          <div
            key={agent.id}
            className="p-3 rounded-xl bg-surface-raised border border-surface-border"
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <div
                    className={`w-2 h-2 rounded-full ${STATUS_COLORS[agent.status]} ${
                      agent.status === 'working' ? 'animate-pulse' : ''
                    }`}
                  />
                  <span className="text-sm font-medium text-white truncate">
                    {agent.name}
                  </span>
                  <span className="text-xs text-white/30">{agent.model}</span>
                </div>
                <p className="text-xs text-white/40 mt-0.5 ml-4">
                  {agent.role}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <span className={`text-xs ${STATUS_TEXT_COLORS[agent.status]}`}>
                  {agent.status}
                </span>
                <span className="text-[10px] text-white/20">
                  {formatDuration(agent.duration_seconds)}
                </span>
              </div>
            </div>
            {agent.task && agent.status === 'working' && (
              <p className="text-xs text-white/50 mt-2 ml-4 leading-relaxed">
                {agent.task}
              </p>
            )}
          </div>
        ))}

        {agents.length === 0 && (
          <div className="text-center py-8 text-white/30 text-sm">
            No agents configured
          </div>
        )}
      </div>

      {/* Recent Reviews */}
      {latestReview && (
        <div className="px-4 pb-4">
          <h2 className="text-sm font-medium text-white/60 mb-2">
            Recent Reviews
          </h2>
          <div className="space-y-2">
            {(['CRITICAL', 'HIGH', 'MEDIUM', 'LOW'] as const).map(
              (severity) => {
                const findings = findingsBySeverity[severity]
                if (!findings?.length) return null
                const isExpanded = expandedSeverity === severity

                return (
                  <div
                    key={severity}
                    className={`rounded-xl border ${SEVERITY_COLORS[severity]}`}
                  >
                    <button
                      onClick={() =>
                        setExpandedSeverity(isExpanded ? null : severity)
                      }
                      className="w-full flex items-center justify-between px-3 py-2.5 min-h-[44px]"
                    >
                      <div className="flex items-center gap-2">
                        <div
                          className={`w-2 h-2 rounded-full ${SEVERITY_DOT[severity]}`}
                        />
                        <span className="text-sm font-medium">
                          {severity}
                        </span>
                        <span className="text-xs opacity-60">
                          ({findings.length})
                        </span>
                      </div>
                      {isExpanded ? (
                        <ChevronDown size={14} />
                      ) : (
                        <ChevronRight size={14} />
                      )}
                    </button>

                    {isExpanded && (
                      <div className="px-3 pb-3 space-y-2">
                        {findings.map((f, i) => (
                          <div
                            key={i}
                            className="text-xs leading-relaxed opacity-80"
                          >
                            <span className="font-medium">[{f.agent}]</span>{' '}
                            {f.message}
                            {f.file && (
                              <span className="text-white/30 ml-1">
                                {f.file}
                                {f.line ? `:${f.line}` : ''}
                              </span>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                )
              }
            )}

            {Object.keys(findingsBySeverity).length === 0 && (
              <div className="text-xs text-white/30 text-center py-3">
                No findings
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
