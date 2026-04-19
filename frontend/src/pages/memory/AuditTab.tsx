import { ChevronRight, ArrowLeft, Clock, Target } from 'lucide-react'
import type { AuditEntry } from '../../lib/api'

const AUDIT_ACTION_COLORS: Record<string, string> = {
  removed: 'text-status-offline',
  updated: 'text-chief-light',
  promoted: 'text-status-online',
  demoted: 'text-status-working',
  created: 'text-status-online',
}

const AUDIT_ACTION_BG: Record<string, string> = {
  removed: 'bg-status-offline/10 border-status-offline/30',
  updated: 'bg-chief/10 border-chief/30',
  promoted: 'bg-status-online/10 border-status-online/30',
  demoted: 'bg-status-working/10 border-status-working/30',
  created: 'bg-status-online/10 border-status-online/30',
}

function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 2) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function formatFullTimestamp(iso: string | null | undefined): string {
  if (!iso) return 'Unknown time'
  try {
    const d = new Date(iso)
    return d.toLocaleString(undefined, {
      weekday: 'short',
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: 'numeric',
      minute: '2-digit',
      second: '2-digit',
    })
  } catch {
    return iso
  }
}

interface AuditTabProps {
  entries: AuditEntry[]
  query: string
  selectedIndex: number | null
  onSelect: (index: number | null) => void
}

export default function AuditTab({ entries, query, selectedIndex, onSelect }: AuditTabProps) {
  if (selectedIndex !== null && entries[selectedIndex]) {
    return (
      <AuditDetail entry={entries[selectedIndex]} onBack={() => onSelect(null)} />
    )
  }

  const q = query.toLowerCase()
  const filtered = entries.filter(
    (e) =>
      !q ||
      e.target.toLowerCase().includes(q) ||
      e.reason.toLowerCase().includes(q) ||
      e.action.toLowerCase().includes(q)
  )

  if (entries.length === 0) {
    return (
      <div className="text-center py-10 text-white/30 text-sm">No audit entries yet</div>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-white/35 px-1 mb-1 leading-snug">
        Memory promotions, updates, and trims. Tap an entry for full detail.
      </p>
      {filtered.length === 0 ? (
        <div className="text-center py-10 text-white/30 text-sm">No matching entries</div>
      ) : (
        filtered.map((entry) => {
          // Use original index for selection so the detail view lines up.
          const originalIndex = entries.indexOf(entry)
          return (
            <button
              key={`${originalIndex}-${entry.timestamp}`}
              onClick={() => onSelect(originalIndex)}
              className="w-full p-3 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors text-left"
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={`text-[10px] font-semibold uppercase tracking-wide ${
                        AUDIT_ACTION_COLORS[entry.action] ?? 'text-white/40'
                      }`}
                    >
                      {entry.action}
                    </span>
                    <span className="text-xs text-white/60 truncate font-mono">
                      {entry.target}
                    </span>
                  </div>
                  {entry.reason && (
                    <p className="text-xs text-white/40 leading-snug line-clamp-2">
                      {entry.reason}
                    </p>
                  )}
                </div>
                <div className="flex flex-col items-end gap-1 shrink-0">
                  <span className="text-[10px] text-white/25">
                    {formatRelativeTime(entry.timestamp)}
                  </span>
                  <ChevronRight size={13} className="text-white/25" />
                </div>
              </div>
            </button>
          )
        })
      )}
    </div>
  )
}

function AuditDetail({ entry, onBack }: { entry: AuditEntry; onBack: () => void }) {
  const actionColor = AUDIT_ACTION_COLORS[entry.action] ?? 'text-white/60'
  const actionBg = AUDIT_ACTION_BG[entry.action] ?? 'bg-surface-overlay border-surface-border'

  return (
    <div className="space-y-4">
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-xs text-white/50 active:text-white transition-colors"
      >
        <ArrowLeft size={13} />
        Back to audit log
      </button>

      {/* Hero */}
      <div className={`rounded-xl border p-4 ${actionBg}`}>
        <p className={`text-[11px] font-semibold uppercase tracking-widest ${actionColor}`}>
          {entry.action}
        </p>
        <p className="text-lg font-semibold text-white mt-1 break-words font-mono">
          {entry.target}
        </p>
      </div>

      {/* Metadata */}
      <div className="space-y-3">
        <DetailRow
          icon={<Clock size={13} />}
          label="When"
          value={formatFullTimestamp(entry.timestamp)}
          sub={formatRelativeTime(entry.timestamp)}
        />
        <DetailRow
          icon={<Target size={13} />}
          label="Target"
          mono
          value={entry.target}
        />
      </div>

      {/* Reason — the body of the entry */}
      <div className="rounded-xl bg-surface-raised border border-surface-border p-4">
        <p className="text-[11px] font-semibold uppercase tracking-widest text-white/50 mb-2">
          Reason
        </p>
        {entry.reason ? (
          <p className="text-sm text-white/80 leading-relaxed whitespace-pre-wrap">
            {entry.reason}
          </p>
        ) : (
          <p className="text-sm text-white/30 italic">No reason recorded.</p>
        )}
      </div>

      <p className="text-[11px] text-white/25 leading-snug px-1">
        Chief writes audit entries when memory is promoted, trimmed, or restructured.
        Reach out to Chief in voice to investigate or revert a change.
      </p>
    </div>
  )
}

function DetailRow({
  icon,
  label,
  value,
  sub,
  mono = false,
}: {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  mono?: boolean
}) {
  return (
    <div className="flex items-start gap-3 px-1">
      <div className="w-6 h-6 rounded-md bg-surface-overlay flex items-center justify-center text-white/50 shrink-0 mt-0.5">
        {icon}
      </div>
      <div className="flex-1 min-w-0">
        <p className="text-[10px] font-semibold uppercase tracking-widest text-white/40">
          {label}
        </p>
        <p
          className={`text-sm text-white/85 break-words ${mono ? 'font-mono' : ''} mt-0.5`}
        >
          {value}
        </p>
        {sub && <p className="text-[11px] text-white/35 mt-0.5">{sub}</p>}
      </div>
    </div>
  )
}
