import { useEffect, useState } from 'react'
import { Loader2, X, CheckCircle2, AlertCircle, ChevronDown, ChevronRight, Ban } from 'lucide-react'

export type TaskBubbleStatus = 'running' | 'complete' | 'cancelled'

interface TaskBubbleProps {
  taskSpec: string
  startedAt: string
  /** "running" | "complete" | "cancelled". Derived by VoicePage from WS events. */
  status: TaskBubbleStatus
  /** Repo display name (e.g. "Arch", "Chief Command") */
  repo?: string
  exitCode?: number
  durationSeconds?: number
  summary?: string
  cancelReason?: string
  stdoutLines: string[]
  onCancel?: () => void
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`
  const m = Math.floor(seconds / 60)
  const s = Math.floor(seconds % 60)
  return `${m}m ${s}s`
}

function useElapsed(startedAt: string, active: boolean): number {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    if (!active) return
    const start = new Date(startedAt).getTime()
    const tick = () => setElapsed((Date.now() - start) / 1000)
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [startedAt, active])
  return elapsed
}

export function TaskBubble({
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
}: TaskBubbleProps) {
  const [showOutput, setShowOutput] = useState(false)
  const liveElapsed = useElapsed(startedAt, status === 'running')

  const borderClass =
    status === 'running'
      ? 'border-l-amber-400'
      : status === 'cancelled'
      ? 'border-l-red-400/60'
      : exitCode === 0
      ? 'border-l-emerald-400'
      : 'border-l-red-400'

  const shownDuration =
    typeof durationSeconds === 'number'
      ? formatDuration(durationSeconds)
      : formatDuration(liveElapsed)

  return (
    <div className="flex justify-start">
      <div
        className={`max-w-[85%] w-full rounded-2xl rounded-bl-md bg-surface-raised border border-surface-border border-l-4 ${borderClass} px-4 py-3 space-y-2`}
      >
        {/* Header row */}
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-xs uppercase tracking-wide text-ink/40 font-medium">
              Dispatched to Claude Code{repo ? ` (${repo})` : ''}
            </p>
            <p className="text-sm text-ink/90 mt-0.5 break-words">{taskSpec}</p>
          </div>

          {status === 'running' && onCancel && (
            <button
              onClick={onCancel}
              className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-md text-xs bg-red-600/20 border border-red-600/40 text-red-700 hover:bg-red-600/30 active:scale-95 transition-all"
              aria-label="Cancel task"
            >
              <X size={12} />
              Cancel
            </button>
          )}
        </div>

        {/* Status row */}
        <div className="flex items-center gap-2 text-xs">
          {status === 'running' && (
            <>
              <Loader2 size={12} className="text-accent-dark animate-spin" />
              <span className="text-accent-dark font-medium">Running</span>
              <span className="text-ink/30">·</span>
              <span className="text-ink/60 tabular-nums">{shownDuration}</span>
            </>
          )}

          {status === 'complete' && (
            <>
              {exitCode === 0 ? (
                <>
                  <CheckCircle2 size={12} className="text-emerald-600" />
                  <span className="px-1.5 py-0.5 rounded bg-emerald-500/15 text-emerald-700 font-medium">
                    exit 0
                  </span>
                </>
              ) : (
                <>
                  <AlertCircle size={12} className="text-red-600" />
                  <span className="px-1.5 py-0.5 rounded bg-red-500/15 text-red-700 font-medium">
                    exit {exitCode}
                  </span>
                </>
              )}
              <span className="text-ink/30">·</span>
              <span className="text-ink/60 tabular-nums">{shownDuration}</span>
            </>
          )}

          {status === 'cancelled' && (
            <>
              <Ban size={12} className="text-ink/50" />
              <span className="px-1.5 py-0.5 rounded bg-ink/10 text-ink/60 font-medium">
                Cancelled
              </span>
              {cancelReason && (
                <>
                  <span className="text-ink/30">·</span>
                  <span className="text-ink/50 truncate">{cancelReason}</span>
                </>
              )}
            </>
          )}
        </div>

        {/* Summary (complete only) */}
        {status === 'complete' && summary && (
          <p className="text-sm text-ink/80 leading-relaxed">{summary}</p>
        )}

        {/* Output toggle */}
        {stdoutLines.length > 0 && (
          <div>
            <button
              onClick={() => setShowOutput((v) => !v)}
              className="flex items-center gap-1 text-[11px] text-ink/40 hover:text-ink/70 transition-colors"
            >
              {showOutput ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
              {showOutput ? 'Hide output' : 'Show output'}
              <span className="text-ink/30">({stdoutLines.length} lines)</span>
            </button>
            {showOutput && (
              <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-black/40 border border-ink/5 px-3 py-2 text-[11px] font-mono text-ink/70 whitespace-pre-wrap break-words">
                {stdoutLines.join('\n')}
              </pre>
            )}
          </div>
        )}
      </div>
    </div>
  )
}

export default TaskBubble
