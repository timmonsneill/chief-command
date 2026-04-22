import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'
import { Loader2, CheckCircle2, AlertCircle, Ban, ChevronDown, ChevronRight, X } from 'lucide-react'
import { TaskBubble, type TaskBubbleStatus } from './TaskBubble'

/**
 * Compact inline activity row for dispatched Claude Code tasks.
 *
 * Renders in the voice chat transcript at the position where the task was
 * dispatched. Shows a single-line "current action" signal — enough to know
 * the agent is alive without flooding the conversation with stdout.
 *
 * Click the row to expand the full TaskBubble beneath it (raw stdout, exit
 * code, summary). That bubble is the canonical detail surface and is NOT
 * duplicated — it lives inside this component, hidden by default.
 *
 * Visual weight: de-emphasized vs. chat bubbles. Steel-blue + amber per
 * design system. Full-width on mobile, max-85% on desktop (matches TaskBubble
 * footprint so alignment feels consistent when expanded).
 */

interface InlineTaskActivityProps {
  taskSpec: string
  startedAt: string
  status: TaskBubbleStatus
  repo?: string
  exitCode?: number
  durationSeconds?: number
  summary?: string
  cancelReason?: string
  stdoutLines: string[]
  onCancel?: () => void
}

// ANSI CSI sequences — terminal color codes etc. Strip them so they don't
// render as garbage chars in the compact row's subtext.
// eslint-disable-next-line no-control-regex
const ANSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g

function stripAnsi(s: string): string {
  return s.replace(ANSI_RE, '')
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1).trimEnd() + '…'
}

function lastNonEmptyLine(lines: string[]): string {
  for (let i = lines.length - 1; i >= 0; i--) {
    const raw = lines[i]
    if (!raw) continue
    // Task output arrives as chunks that may contain embedded newlines.
    const sublines = raw.split('\n')
    for (let j = sublines.length - 1; j >= 0; j--) {
      const cleaned = stripAnsi(sublines[j]).trim()
      if (cleaned) return cleaned
    }
  }
  return ''
}

// Hook: debounces `value` so the UI doesn't re-render on every task_output
// frame. 500ms cadence matches the spec's "1 update per 500ms max" ask and
// keeps the inline row legible during busy builds.
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value)
  const latestRef = useRef(value)
  useEffect(() => {
    latestRef.current = value
  }, [value])
  useEffect(() => {
    const id = window.setTimeout(() => setDebounced(latestRef.current), delayMs)
    return () => window.clearTimeout(id)
  }, [value, delayMs])
  return debounced
}

export function InlineTaskActivity({
  taskSpec,
  startedAt,
  status,
  repo,
  exitCode,
  durationSeconds,
  summary,
  cancelReason,
  stdoutLines,
  onCancel,
}: InlineTaskActivityProps) {
  const [expanded, setExpanded] = useState(false)

  // Recompute on every stdoutLines change, but only surface via the debounced
  // hook — so rapid bursts of output don't cause a 60Hz re-render storm.
  const rawLatest = useMemo(() => lastNonEmptyLine(stdoutLines), [stdoutLines])
  const latestAction = useDebouncedValue(rawLatest, 500)

  const shortSpec = truncate(taskSpec, 80)

  // Subtext — what shows under the headline. Priority:
  //   running   → last task_output line (debounced) or placeholder
  //   complete  → backend summary (truncated for inline)
  //   cancelled → reason
  let subtext = ''
  if (status === 'running') {
    subtext = latestAction || 'Working…'
  } else if (status === 'complete') {
    subtext = summary ? truncate(summary, 120) : exitCode === 0 ? 'Done.' : `Exited ${exitCode}.`
  } else if (status === 'cancelled') {
    subtext = cancelReason ? truncate(cancelReason, 120) : 'Cancelled.'
  }

  // Icon + accent per status. Running uses amber (design-system accent for
  // active states). Complete=emerald (success) or red (failure). Cancelled=muted ink.
  const Icon =
    status === 'running'
      ? Loader2
      : status === 'cancelled'
        ? Ban
        : exitCode === 0
          ? CheckCircle2
          : AlertCircle

  const iconClass =
    status === 'running'
      ? 'text-accent-dark animate-spin'
      : status === 'cancelled'
        ? 'text-ink/50'
        : exitCode === 0
          ? 'text-emerald-600'
          : 'text-red-600'

  const headlineClass =
    status === 'running'
      ? 'text-ink/80'
      : status === 'cancelled'
        ? 'text-ink/60'
        : 'text-ink/80'

  const headlineLabel =
    status === 'running'
      ? `Dispatching${repo ? ` to ${repo}` : ''}`
      : status === 'cancelled'
        ? 'Cancelled'
        : exitCode === 0
          ? 'Completed'
          : `Failed (exit ${exitCode})`

  const rowBgClass =
    status === 'running'
      ? 'bg-accent/5 border-accent/25 hover:bg-accent/10'
      : status === 'cancelled'
        ? 'bg-surface-overlay border-surface-border hover:bg-surface-overlay/80'
        : exitCode === 0
          ? 'bg-emerald-50/60 border-emerald-200/70 hover:bg-emerald-50'
          : 'bg-red-50/60 border-red-200/70 hover:bg-red-50'

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[85%] space-y-2">
        {/* Compact activity row.
            Outer is a <div> so we can put a sibling <button> for Cancel
            without nesting interactive elements (invalid HTML).
            - Left button: flex-1, toggles expand/collapse.
            - Right button (running only): Cancel. Real button, real a11y. */}
        <div className={`group flex items-stretch rounded-lg border transition-colors min-h-[44px] ${rowBgClass}`}>
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={`${headlineLabel}: ${shortSpec}. Click for details.`}
            className="flex-1 min-w-0 text-left flex items-start gap-2.5 px-3 py-2.5 rounded-lg focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-1 focus-visible:ring-offset-surface"
          >
            <Icon size={14} aria-hidden="true" className={`mt-0.5 shrink-0 ${iconClass}`} />
            <div className="min-w-0 flex-1">
              <div className={`flex items-baseline gap-1.5 flex-wrap ${headlineClass}`}>
                <span className="text-[11px] uppercase tracking-wide font-semibold text-ink/50">
                  {headlineLabel}
                </span>
                <span className="text-[13px] font-medium break-words">
                  {shortSpec}
                </span>
              </div>
              {subtext && (
                <p className="text-[12px] text-ink/65 mt-0.5 font-mono break-words line-clamp-2">
                  {subtext}
                </p>
              )}
            </div>
            {/* Chevron — subtle on desktop (fades in on hover), always visible
                on mobile where there is no hover. */}
            {expanded ? (
              <ChevronDown size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-ink/40" />
            ) : (
              <ChevronRight size={14} aria-hidden="true" className="mt-0.5 shrink-0 text-ink/30 md:opacity-0 md:group-hover:opacity-100 transition-opacity" />
            )}
          </button>
          {status === 'running' && onCancel && (
            <button
              type="button"
              onClick={(e: MouseEvent<HTMLButtonElement>) => {
                e.stopPropagation()
                onCancel()
              }}
              aria-label="Cancel task"
              className="shrink-0 flex items-center gap-1 px-3 my-1 mr-1 rounded-md text-[11px] font-medium text-red-700 bg-red-500/10 hover:bg-red-500/20 active:scale-95 transition-all focus:outline-none focus-visible:ring-2 focus-visible:ring-red-400 min-h-[36px]"
            >
              <X size={11} aria-hidden="true" />
              Cancel
            </button>
          )}
        </div>

        {/* Expanded detail — reuses the canonical TaskBubble surface so we
            don't duplicate its rendering or re-test its edge cases. Hidden by
            default; toggled by the compact row above. */}
        {expanded && (
          <div className="pl-1">
            <TaskBubble
              taskSpec={taskSpec}
              startedAt={startedAt}
              status={status}
              repo={repo}
              exitCode={exitCode}
              durationSeconds={durationSeconds}
              summary={summary}
              cancelReason={cancelReason}
              stdoutLines={stdoutLines}
              onCancel={onCancel}
            />
          </div>
        )}
      </div>
    </div>
  )
}

export default InlineTaskActivity
