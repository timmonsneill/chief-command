import { useState, useEffect, useCallback } from 'react'
import { BarChart2, ChevronDown, ChevronRight, AlertTriangle, RefreshCw } from 'lucide-react'
import { sessionsApi, type Session, type SessionDetail, type UsageSummary } from '../lib/api'

const MODEL_COLORS: Record<string, string> = {
  'claude-haiku-4-5': 'bg-chief/50',
  'claude-sonnet-4-6': 'bg-chief',
  'claude-opus-4-7': 'bg-purple-500',
}

function centsToDisplay(cents: number): string {
  if (cents < 100) return `¢${cents}`
  return `$${(cents / 100).toFixed(2)}`
}

function formatDuration(s: number | null): string {
  if (!s) return '--'
  if (s < 60) return `${Math.round(s)}s`
  const m = Math.floor(s / 60)
  const sec = Math.round(s % 60)
  return `${m}m ${sec}s`
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

function RollupCard({ label, cents }: { label: string; cents: number }) {
  return (
    <div className="flex-1 p-3 rounded-xl bg-surface-raised border border-surface-border text-center">
      <p className="text-[10px] font-semibold uppercase tracking-wider text-white/40 mb-1">{label}</p>
      <p className="text-xl font-semibold text-white">{centsToDisplay(cents)}</p>
    </div>
  )
}

function SessionRow({
  session,
  expanded,
  onToggle,
}: {
  session: Session
  expanded: boolean
  onToggle: () => void
}) {
  const [detail, setDetail] = useState<SessionDetail | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    if (!expanded || detail) return
    setLoadingDetail(true)
    sessionsApi.get(session.id).then((d) => {
      setDetail(d)
    }).catch(() => {
      // silently ignore — detail not available
    }).finally(() => {
      setLoadingDetail(false)
    })
  }, [expanded, session.id, detail])

  const modelCounts: Record<string, number> = {}
  if (detail) {
    for (const turn of detail.turns) {
      modelCounts[turn.model] = (modelCounts[turn.model] ?? 0) + 1
    }
  }

  return (
    <div className="rounded-xl bg-surface-raised border border-surface-border overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-4 py-3 min-h-[52px] text-left active:bg-surface-overlay transition-colors"
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-white/70">
              {formatDateTime(session.started_at)}
            </span>
            {session.turn_count > 0 && (
              <span className="text-[10px] text-white/30">{session.turn_count} turns</span>
            )}
            {session.duration_s !== null && (
              <span className="text-[10px] text-white/30">{formatDuration(session.duration_s)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-sm font-semibold text-white">
            {centsToDisplay(session.total_cost_cents)}
          </span>
          {expanded ? (
            <ChevronDown size={14} className="text-white/30" />
          ) : (
            <ChevronRight size={14} className="text-white/30" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-3 border-t border-surface-border">
          {loadingDetail && (
            <p className="text-xs text-white/30 py-2">Loading turns...</p>
          )}
          {detail && (
            <div className="mt-2 space-y-2">
              {Object.keys(modelCounts).length > 0 && (
                <div className="flex items-center gap-1.5 mb-3">
                  {Object.entries(modelCounts).map(([model, count]) => (
                    <div key={model} className="flex items-center gap-1">
                      <div className={`w-2 h-2 rounded-full ${MODEL_COLORS[model] ?? 'bg-white/30'}`} />
                      <span className="text-[10px] text-white/40">{model.split('-').slice(-2).join('-')} ×{count}</span>
                    </div>
                  ))}
                </div>
              )}
              {detail.turns.map((turn) => (
                <div
                  key={turn.id}
                  className="p-2.5 rounded-lg bg-surface border border-surface-border"
                >
                  <div className="flex items-center justify-between mb-1">
                    <div className="flex items-center gap-1.5">
                      <div className={`w-1.5 h-1.5 rounded-full ${MODEL_COLORS[turn.model] ?? 'bg-white/30'}`} />
                      <span className="text-[10px] font-medium text-white/50">{turn.model}</span>
                    </div>
                    <span className="text-[10px] text-white/40">{centsToDisplay(turn.cost_cents)}</span>
                  </div>
                  <div className="flex gap-3 text-[10px] text-white/30">
                    <span>in {turn.input_tokens.toLocaleString()}</span>
                    <span>out {turn.output_tokens.toLocaleString()}</span>
                    {turn.cache_read_tokens > 0 && (
                      <span>cached {turn.cache_read_tokens.toLocaleString()}</span>
                    )}
                  </div>
                  {turn.user_text && (
                    <p className="text-xs text-white/40 mt-1.5 truncate">{turn.user_text}</p>
                  )}
                </div>
              ))}
              {detail.turns.length === 0 && (
                <p className="text-xs text-white/30">No turns recorded</p>
              )}
            </div>
          )}
          {!loadingDetail && !detail && (
            <p className="text-xs text-white/30 py-2">Turn details unavailable</p>
          )}
        </div>
      )}
    </div>
  )
}

export default function SessionsPage() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setError('')
    try {
      const [sessionList, usageSummary] = await Promise.allSettled([
        sessionsApi.list(),
        sessionsApi.usageSummary(),
      ])
      if (sessionList.status === 'fulfilled') setSessions(sessionList.value)
      if (usageSummary.status === 'fulfilled') setSummary(usageSummary.value)
    } catch {
      setError('Failed to load sessions')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-white/30 text-sm">Loading sessions...</div>
      </div>
    )
  }

  const alertLevel = summary?.alert_level ?? 'none'

  return (
    <div className="h-full overflow-y-auto">
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart2 size={18} className="text-chief" />
            <h1 className="text-lg font-semibold text-white">Sessions</h1>
          </div>
          <button
            onClick={fetchData}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-white/30 active:text-white/60 transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="px-4 py-3 space-y-4">
        {/* Alert banner */}
        {alertLevel !== 'none' && (
          <div
            className={`flex items-center gap-2 p-3 rounded-xl border text-sm ${
              alertLevel === 'critical'
                ? 'bg-status-offline/10 border-status-offline/30 text-status-offline'
                : 'bg-status-working/10 border-status-working/30 text-status-working'
            }`}
          >
            <AlertTriangle size={14} className="shrink-0" />
            <span>
              {alertLevel === 'critical'
                ? 'API spend critical — on pace for $300+/mo'
                : 'API spend elevated — on pace for $200+/mo'}
            </span>
          </div>
        )}

        {/* Rolling totals */}
        {summary && (
          <div className="flex gap-2">
            <RollupCard label="Today" cents={summary.today_cents} />
            <RollupCard label="This week" cents={summary.week_cents} />
            <RollupCard label="This month" cents={summary.month_cents} />
          </div>
        )}

        {/* Session list */}
        {error && (
          <p className="text-xs text-white/40 text-center">{error}</p>
        )}

        {sessions.length > 0 && (
          <div className="space-y-2">
            {sessions.map((session) => (
              <SessionRow
                key={session.id}
                session={session}
                expanded={expandedId === session.id}
                onToggle={() =>
                  setExpandedId(expandedId === session.id ? null : session.id)
                }
              />
            ))}
          </div>
        )}

        {sessions.length === 0 && !error && (
          <div className="text-center py-12 text-white/30 text-sm">
            No sessions yet — start a voice conversation
          </div>
        )}
      </div>
    </div>
  )
}
