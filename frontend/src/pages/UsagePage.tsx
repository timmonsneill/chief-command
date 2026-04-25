import { useState, useEffect, useCallback, useRef } from 'react'
import { CircleDollarSign, ChevronDown, ChevronRight, AlertTriangle, RefreshCw, Zap, Mic, Volume2 } from 'lucide-react'
import {
  sessionsApi,
  type Session,
  type SessionDetail,
  type UsageSummary,
  type SessionUsage,
  type UsageByModel,
  type UsageDayPoint,
  type VoiceStatRollup,
} from '../lib/api'
import { useProjectContext } from '../hooks/useProjectContext'

// ── Helpers ────────────────────────────────────────────────────────────────

const WARNING_CENTS = 15_00  // $15/day = warning
const CRITICAL_CENTS = 30_00 // $30/day = critical

function centsToDisplay(cents: number): string {
  if (cents < 100) return `¢${cents}`
  return `$${(cents / 100).toFixed(2)}`
}

function centsToFullDisplay(cents: number): string {
  return `$${(cents / 100).toFixed(2)}`
}

// Voice costs are sub-cent per-turn. Use more decimals for tiny values so the
// owner can see the number, but drop them for obvious $ amounts.
function usdToDisplay(usd: number): string {
  if (!isFinite(usd) || usd <= 0) return '$0.00'
  if (usd < 0.01) return `¢${(usd * 100).toFixed(3)}`
  if (usd < 1) return `¢${(usd * 100).toFixed(1)}`
  return `$${usd.toFixed(2)}`
}

function secondsToDisplay(s: number): string {
  if (!isFinite(s) || s <= 0) return '0s'
  if (s < 60) return `${s.toFixed(1)}s`
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

function charsToDisplay(n: number): string {
  if (n < 1000) return `${n}`
  if (n < 1_000_000) return `${(n / 1000).toFixed(1)}k`
  return `${(n / 1_000_000).toFixed(2)}M`
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

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric' })
  } catch {
    return iso
  }
}

function alertColor(level: 'none' | 'warning' | 'critical') {
  if (level === 'critical') return 'text-status-offline'
  if (level === 'warning') return 'text-status-working'
  return 'text-status-online'
}

function levelFromCents(cents: number): 'none' | 'warning' | 'critical' {
  if (cents >= CRITICAL_CENTS) return 'critical'
  if (cents >= WARNING_CENTS) return 'warning'
  return 'none'
}

const MODEL_COLORS: Record<string, string> = {
  'claude-haiku-4-5': 'bg-chief/50',
  'claude-sonnet-4-6': 'bg-chief',
  'claude-opus-4-7': 'bg-purple-500',
}

const MODEL_LABELS: Record<string, string> = {
  'claude-haiku-4-5': 'Haiku',
  'claude-sonnet-4-6': 'Sonnet',
  'claude-opus-4-7': 'Opus',
}

// ── Hero cost card ─────────────────────────────────────────────────────────

function HeroCostCard({
  label,
  cents,
  alertLevel,
}: {
  label: string
  cents: number
  alertLevel?: 'none' | 'warning' | 'critical'
}) {
  const level = alertLevel ?? levelFromCents(cents)
  const colorClass = alertColor(level)

  return (
    <div className="flex-1 p-4 rounded-2xl bg-surface-raised border border-surface-border text-center">
      <p className="text-[11px] font-semibold uppercase tracking-widest text-ink/40 mb-2">{label}</p>
      <p className={`font-display text-3xl md:text-5xl font-bold tabular-nums ${colorClass}`}>
        {centsToFullDisplay(cents)}
      </p>
      {level !== 'none' && (
        <span
          className={`mt-2 inline-block text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full ${
            level === 'critical'
              ? 'bg-status-offline/20 text-status-offline'
              : 'bg-status-working/20 text-status-working'
          }`}
        >
          {level}
        </span>
      )}
    </div>
  )
}

// ── Current session strip ──────────────────────────────────────────────────

function CurrentSessionStrip({ session }: { session: SessionUsage }) {
  const started = new Date(session.started_at)
  const age = Math.floor((Date.now() - started.getTime()) / 1000)
  const voiceUsd = session.voice?.total_usd ?? 0

  return (
    <div className="rounded-2xl bg-chief/10 border border-chief/30 px-4 py-3 flex items-center gap-3">
      <div className="w-2 h-2 rounded-full bg-chief animate-pulse shrink-0" />
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold text-chief mb-0.5">Active session</p>
        <p className="text-[11px] text-ink/50 truncate">
          {session.model.split('-').slice(-2).join('-')} · {session.turn_count} turns · started {formatDuration(age)} ago
        </p>
      </div>
      <div className="shrink-0 text-right">
        <p className="font-display text-lg font-bold text-ink tabular-nums">
          {centsToDisplay(session.session_total_cents)}
        </p>
        {voiceUsd > 0 ? (
          <p className="text-[10px] text-ink/40 tabular-nums">
            + {usdToDisplay(voiceUsd)} voice
          </p>
        ) : (
          <p className="text-[10px] text-ink/30">this session</p>
        )}
      </div>
    </div>
  )
}

// ── Per-model breakdown ────────────────────────────────────────────────────

function ModelBreakdown({ data }: { data: UsageByModel }) {
  // Narrow to the per-model columns only. UsageByModel gained a `voice?` field
  // that holds the voice rollup (not a Record<string, ModelUsageStats>), so we
  // can't use `keyof UsageByModel` directly here — it would include 'voice'
  // and break the per-model lookup below.
  type ModelPeriod = 'today' | 'week' | 'month'
  const periods: { key: ModelPeriod; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: 'week', label: 'Week' },
    { key: 'month', label: 'Month' },
  ]

  const allModels = Array.from(
    new Set([
      ...Object.keys(data.today),
      ...Object.keys(data.week),
      ...Object.keys(data.month),
    ])
  )

  if (allModels.length === 0) {
    return (
      <p className="text-xs text-ink/30 text-center py-4">No model data yet</p>
    )
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr>
            <th className="text-left text-ink/40 font-medium pb-2 pr-4">Model</th>
            {periods.map((p) => (
              <th key={p.key} className="text-right text-ink/40 font-medium pb-2 pl-3">{p.label}</th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-surface-border">
          {allModels.map((model) => (
            <tr key={model}>
              <td className="py-2 pr-4">
                <div className="flex items-center gap-2">
                  <div className={`w-2 h-2 rounded-full shrink-0 ${MODEL_COLORS[model] ?? 'bg-ink/30'}`} />
                  <span className="text-ink/70">{MODEL_LABELS[model] ?? model}</span>
                </div>
              </td>
              {periods.map((p) => {
                const stat = data[p.key][model]
                return (
                  <td key={p.key} className="py-2 pl-3 text-right text-ink/60 tabular-nums">
                    {stat ? centsToDisplay(stat.cost_cents) : '—'}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Daily bar chart (inline SVG) ───────────────────────────────────────────

const CHART_HEIGHT = 80
const BAR_GAP = 2

function DailyChart({ days }: { days: UsageDayPoint[] }) {
  const [hovered, setHovered] = useState<UsageDayPoint | null>(null)
  const containerRef = useRef<HTMLDivElement>(null)

  if (days.length === 0) {
    return <p className="text-xs text-ink/30 text-center py-4">No daily data yet</p>
  }

  const maxCents = Math.max(...days.map((d) => d.cost_cents), 1)
  const totalWidth = 100 // percent — we'll use viewBox

  const barWidth = (totalWidth - BAR_GAP * (days.length - 1)) / days.length
  const viewBoxWidth = days.length * (barWidth + BAR_GAP) - BAR_GAP

  return (
    <div ref={containerRef} className="select-none">
      {hovered && (
        <div className="mb-2 text-center">
          <span className="text-xs font-semibold text-ink">{centsToDisplay(hovered.cost_cents)}</span>
          <span className="text-xs text-ink/40 ml-2">{formatDate(hovered.date)}</span>
          {hovered.turns > 0 && (
            <span className="text-[10px] text-ink/30 ml-2">{hovered.turns} turns</span>
          )}
        </div>
      )}
      <svg
        viewBox={`0 0 ${viewBoxWidth} ${CHART_HEIGHT}`}
        className="w-full"
        style={{ height: `${CHART_HEIGHT}px` }}
      >
        {days.map((day, i) => {
          const barH = Math.max(2, (day.cost_cents / maxCents) * (CHART_HEIGHT - 4))
          const x = i * (barWidth + BAR_GAP)
          const y = CHART_HEIGHT - barH
          const level = levelFromCents(day.cost_cents)
          const fill = level === 'critical'
            ? '#ef4444'
            : level === 'warning'
            ? '#f59e0b'
            : 'rgba(99,188,177,0.7)' // chief color tinted
          const isHov = hovered?.date === day.date

          return (
            <rect
              key={day.date}
              x={x}
              y={y}
              width={barWidth}
              height={barH}
              rx={1.5}
              fill={fill}
              opacity={isHov ? 1 : 0.7}
              style={{ cursor: 'pointer' }}
              onMouseEnter={() => setHovered(day)}
              onMouseLeave={() => setHovered(null)}
              onTouchStart={() => setHovered(day)}
            />
          )
        })}
      </svg>
      <div className="flex justify-between mt-1">
        <span className="text-[9px] text-ink/25">{formatDate(days[0].date)}</span>
        <span className="text-[9px] text-ink/25">{formatDate(days[days.length - 1].date)}</span>
      </div>
    </div>
  )
}

// ── Session row (unchanged from SessionsPage) ──────────────────────────────

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
            <span className="text-xs font-medium text-ink/70">
              {formatDateTime(session.started_at)}
            </span>
            {session.turn_count > 0 && (
              <span className="text-[10px] text-ink/30">{session.turn_count} turns</span>
            )}
            {session.duration_s !== null && (
              <span className="text-[10px] text-ink/30">{formatDuration(session.duration_s)}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-sm font-semibold text-ink">
            {centsToDisplay(session.total_cost_cents)}
          </span>
          {expanded ? (
            <ChevronDown size={14} className="text-ink/30" />
          ) : (
            <ChevronRight size={14} className="text-ink/30" />
          )}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-3 border-t border-surface-border">
          {loadingDetail && (
            <p className="text-xs text-ink/30 py-2">Loading turns...</p>
          )}
          {detail && (
            <div className="mt-2 space-y-2">
              {Object.keys(modelCounts).length > 0 && (
                <div className="flex items-center gap-1.5 mb-3">
                  {Object.entries(modelCounts).map(([model, count]) => (
                    <div key={model} className="flex items-center gap-1">
                      <div className={`w-2 h-2 rounded-full ${MODEL_COLORS[model] ?? 'bg-ink/30'}`} />
                      <span className="text-[10px] text-ink/40">{model.split('-').slice(-2).join('-')} ×{count}</span>
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
                      <div className={`w-1.5 h-1.5 rounded-full ${MODEL_COLORS[turn.model] ?? 'bg-ink/30'}`} />
                      <span className="text-[10px] font-medium text-ink/50">{turn.model}</span>
                    </div>
                    <span className="text-[10px] text-ink/40">{centsToDisplay(turn.cost_cents)}</span>
                  </div>
                  <div className="flex gap-3 text-[10px] text-ink/30">
                    <span>in {turn.input_tokens.toLocaleString()}</span>
                    <span>out {turn.output_tokens.toLocaleString()}</span>
                    {turn.cache_read_tokens > 0 && (
                      <span>cached {turn.cache_read_tokens.toLocaleString()}</span>
                    )}
                  </div>
                  {turn.user_text && (
                    <p className="text-xs text-ink/40 mt-1.5 truncate">{turn.user_text}</p>
                  )}
                </div>
              ))}
              {detail.turns.length === 0 && (
                <p className="text-xs text-ink/30">No turns recorded</p>
              )}
            </div>
          )}
          {!loadingDetail && !detail && (
            <p className="text-xs text-ink/30 py-2">Turn details unavailable</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Section wrapper ────────────────────────────────────────────────────────

function Section({ title, icon: Icon, children }: { title: string; icon: React.ElementType; children: React.ReactNode }) {
  return (
    <div className="rounded-2xl bg-surface-raised border border-surface-border overflow-hidden">
      <div className="px-4 py-3 border-b border-surface-border flex items-center gap-2">
        <Icon size={14} className="text-chief" />
        <h2 className="text-xs font-semibold uppercase tracking-widest text-ink/60">{title}</h2>
      </div>
      <div className="p-4">{children}</div>
    </div>
  )
}

// ── Voice cost cards (STT + TTS + combined) ────────────────────────────────
//
// Same visual language as the Claude hero cards — rounded surface, uppercase
// label, large number — but with Mic/Volume icons and slightly smaller font so
// the Claude totals stay the primary eye-catch. Each card's footer shows the
// underlying unit (seconds of audio / chars of text) so the owner can sanity-
// check the $ against Google's pricing page at a glance.

function VoiceCostCard({
  label,
  icon: Icon,
  primaryText,
  secondaryText,
  warn = false,
}: {
  label: string
  icon: React.ElementType
  primaryText: string
  secondaryText: string
  warn?: boolean
}) {
  return (
    <div className="flex-1 p-4 rounded-2xl bg-surface-raised border border-surface-border text-center min-w-0">
      <div className="flex items-center justify-center gap-1.5 mb-2">
        <Icon size={11} className="text-ink/40" />
        <p className="text-[11px] font-semibold uppercase tracking-widest text-ink/40">{label}</p>
      </div>
      <p
        className={`font-display text-2xl md:text-3xl font-bold tabular-nums ${
          warn ? 'text-status-working' : 'text-ink'
        }`}
      >
        {primaryText}
      </p>
      <p className="text-[10px] text-ink/30 mt-1 truncate">{secondaryText}</p>
    </div>
  )
}

function providerBreakdownSummary(
  breakdown: VoiceStatRollup['stt']['provider_breakdown'],
  kind: 'stt' | 'tts',
): string {
  const entries = Object.entries(breakdown || {})
  if (entries.length === 0) return 'no activity'
  return entries
    .map(([provider, data]) => {
      const unit = kind === 'stt' ? secondsToDisplay(data.seconds ?? 0) : charsToDisplay(data.chars ?? 0)
      return `${provider} ${unit}`
    })
    .join(' · ')
}

function VoiceCostRow({
  window,
  warnTotal,
  warningUsd,
}: {
  window: VoiceStatRollup
  warnTotal: boolean
  warningUsd: number
}) {
  // Threshold is server-driven via /api/usage/summary.voice_warning_usd so the
  // string here matches whatever the alert was tripped against. Fallback is
  // baked in by the `?? 50` at the call site.
  const thresholdLabel = `$${Math.round(warningUsd)}`
  return (
    <div className="flex gap-3 items-stretch">
      <VoiceCostCard
        label="STT"
        icon={Mic}
        primaryText={usdToDisplay(window.stt.cost_usd)}
        secondaryText={`${secondsToDisplay(window.stt.seconds)} · ${providerBreakdownSummary(window.stt.provider_breakdown, 'stt')}`}
      />
      <VoiceCostCard
        label="TTS"
        icon={Volume2}
        primaryText={usdToDisplay(window.tts.cost_usd)}
        secondaryText={`${charsToDisplay(window.tts.chars)} chars · ${providerBreakdownSummary(window.tts.provider_breakdown, 'tts')}`}
      />
      <VoiceCostCard
        label="Voice total"
        icon={CircleDollarSign}
        primaryText={usdToDisplay(window.total_usd)}
        secondaryText={warnTotal ? `monthly voice > ${thresholdLabel}` : 'STT + TTS'}
        warn={warnTotal}
      />
    </div>
  )
}

function VoiceSection({
  voice,
  voiceAlert,
  warningUsd,
}: {
  voice: { today: VoiceStatRollup; week: VoiceStatRollup; month: VoiceStatRollup }
  voiceAlert: boolean
  warningUsd: number
}) {
  type Window = 'today' | 'week' | 'month'
  const [active, setActive] = useState<Window>('today')
  const tabs: { key: Window; label: string }[] = [
    { key: 'today', label: 'Today' },
    { key: 'week', label: 'Week' },
    { key: 'month', label: 'Month' },
  ]
  const data = voice[active]

  return (
    <Section title="Voice (Google STT + TTS)" icon={Mic}>
      <div className="flex gap-1 mb-3">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActive(t.key)}
            className={`flex-1 py-1.5 px-2 rounded-lg text-[11px] font-semibold uppercase tracking-wider transition-colors ${
              active === t.key
                ? 'bg-chief/20 text-chief'
                : 'bg-surface text-ink/40 active:bg-surface-overlay'
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>
      <VoiceCostRow
        window={data}
        warnTotal={voiceAlert && active === 'month'}
        warningUsd={warningUsd}
      />
    </Section>
  )
}

// ── Main page ──────────────────────────────────────────────────────────────

export default function UsagePage() {
  const [sessions, setSessions] = useState<Session[]>([])
  const [summary, setSummary] = useState<UsageSummary | null>(null)
  const [currentSession, setCurrentSession] = useState<SessionUsage | null>(null)
  const [byModel, setByModel] = useState<UsageByModel | null>(null)
  const [byModelUnavailable, setByModelUnavailable] = useState(false)
  const [dailyPoints, setDailyPoints] = useState<UsageDayPoint[] | null>(null)
  const [dailyUnavailable, setDailyUnavailable] = useState(false)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const { current: currentProject } = useProjectContext()

  const fetchData = useCallback(async () => {
    setError('')
    setByModelUnavailable(false)
    setDailyUnavailable(false)
    const [sessionList, usageSummary, current] = await Promise.allSettled([
      sessionsApi.list(currentProject),
      sessionsApi.usageSummary(),
      sessionsApi.getCurrent(),
    ])
    if (sessionList.status === 'fulfilled') setSessions(sessionList.value)
    if (usageSummary.status === 'fulfilled') setSummary(usageSummary.value)
    if (current.status === 'fulfilled') setCurrentSession(current.value)
    if (sessionList.status === 'rejected' && usageSummary.status === 'rejected') {
      setError('Failed to load usage data')
    }
    setLoading(false)

    try {
      setByModel(await sessionsApi.byModel())
    } catch {
      setByModelUnavailable(true)
    }

    try {
      const dl = await sessionsApi.daily(30)
      setDailyPoints(dl.days)
    } catch {
      setDailyUnavailable(true)
    }
  }, [currentProject])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-ink/30 text-sm">Loading usage...</div>
      </div>
    )
  }

  const alertLevel = summary?.alert_level ?? 'none'

  return (
    <div className="h-full overflow-y-auto">
      {/* Sticky header */}
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <CircleDollarSign size={18} className="text-chief" />
            <h1 className="font-display text-lg font-semibold text-ink">Usage</h1>
          </div>
          <button
            onClick={fetchData}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-ink/30 active:text-ink/60 transition-colors"
          >
            <RefreshCw size={14} />
          </button>
        </div>
      </div>

      <div className="px-4 py-4 space-y-4">
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

        {/* Voice spend alert — independent of Claude alerts. Fires once the
            rolling monthly STT+TTS total crosses the server-side threshold
            (settings.monthly_voice_warning_usd, default $50). */}
        {summary?.voice_alert_level === 'warning' && (
          <div className="flex items-center gap-2 p-3 rounded-xl border text-sm bg-status-working/10 border-status-working/30 text-status-working">
            <Mic size={14} className="shrink-0" />
            <span>
              Voice spend elevated — Google STT+TTS past ${Math.round(summary.voice_warning_usd ?? 50)} this month
            </span>
          </div>
        )}

        {/* Hero cost numbers — always shown, defaults to $0.00 while data loads */}
        <div className="flex gap-3">
          <HeroCostCard label="Today" cents={summary?.today_cents ?? 0} />
          <HeroCostCard label="Week" cents={summary?.week_cents ?? 0} />
          <HeroCostCard label="Month" cents={summary?.month_cents ?? 0} alertLevel={alertLevel} />
        </div>

        {/* Current session strip */}
        {currentSession && (
          <CurrentSessionStrip session={currentSession} />
        )}

        {/* Voice (Google STT + TTS) — hidden until backend reports a voice
            block. Backwards-compat: pre-migration deployments omit `voice`
            from /api/usage/summary and we skip rendering. */}
        {summary?.voice && (
          <VoiceSection
            voice={summary.voice}
            voiceAlert={summary.voice_alert_level === 'warning'}
            warningUsd={summary.voice_warning_usd ?? 50}
          />
        )}

        {/* Per-model breakdown */}
        {!byModelUnavailable && byModel ? (
          <Section title="By model" icon={Zap}>
            <ModelBreakdown data={byModel} />
          </Section>
        ) : byModelUnavailable ? (
          <div className="rounded-2xl bg-surface-raised border border-surface-border px-4 py-4 text-center">
            <p className="text-xs text-ink/30">Coming soon — per-model breakdown</p>
          </div>
        ) : null}

        {/* Daily trend */}
        {!dailyUnavailable && dailyPoints ? (
          <Section title="30-day trend" icon={CircleDollarSign}>
            <DailyChart days={dailyPoints} />
          </Section>
        ) : dailyUnavailable ? (
          <div className="rounded-2xl bg-surface-raised border border-surface-border px-4 py-4 text-center">
            <p className="text-xs text-ink/30">Coming soon — daily trend chart</p>
          </div>
        ) : null}

        {/* Recent sessions */}
        <Section title="Recent sessions" icon={CircleDollarSign}>
          {error && (
            <p className="text-xs text-ink/40 text-center">{error}</p>
          )}
          {sessions.length > 0 ? (
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
          ) : !error ? (
            <p className="text-center py-6 text-ink/30 text-sm">
              No sessions yet — start a voice conversation
            </p>
          ) : null}
        </Section>
      </div>
    </div>
  )
}
