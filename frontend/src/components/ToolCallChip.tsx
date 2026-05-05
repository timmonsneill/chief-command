import { useState } from 'react'
import {
  Loader2,
  Check,
  Ban,
  ChevronDown,
  ChevronRight,
  FileText,
  Terminal,
  Search,
  Send,
  Wrench,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

/**
 * Inline chip for Gemini brain tool calls (Phase 2).
 *
 * Renders a single line in the voice transcript when the brain decides to
 * call a tool itself — Read / Bash / Grep / dispatch_agent. The chip is
 * meant to fill the silent ~10s while a tool runs so the owner sees that
 * Chief is alive and what it is doing, without flooding the conversation
 * with stdout.
 *
 * Visual weight: deliberately lighter than InlineTaskActivity (which is a
 * full task bubble). One line by default; expandable to show a 240-char
 * preview on terminal frames. No raw stdout here — that's the dispatched-
 * task surface, not the brain-tool surface.
 *
 * Design tokens: steel-blue + amber (Chief Command design system).
 *   Running   — amber accent, spinner
 *   Complete  — emerald check, duration on the right
 *   Error     — red dot, no preview leak in voice modal
 *   Cancelled — muted ink, "cancelled"
 */

export type ToolCallStatus = 'running' | 'complete' | 'error' | 'cancelled'

export interface ToolCallChipProps {
  name: string
  // Persona alias (e.g. Glass for code_review). When present, rendered as
  // the header label instead of the raw tool ID. Optional — tools without a
  // persona fall back to ``name``.
  displayName?: string
  args?: Record<string, unknown>
  status: ToolCallStatus
  durationMs?: number
  preview?: string
}

// Map a tool name to its icon. Default = Wrench for unknown tools.
function iconFor(name: string): LucideIcon {
  switch (name) {
    case 'Read':
      return FileText
    case 'Bash':
      return Terminal
    case 'Grep':
      return Search
    case 'dispatch_agent':
      return Send
    case 'code_review':
      return FileText
    default:
      return Wrench
  }
}

// Best-effort one-line summary of the args record. Backend has already
// truncated each field to 200 chars; we do another aesthetic truncate
// here so the chip stays one line on mobile. Order:
//   1. Most "speakable" arg first (file_path, command, pattern, task_spec).
//   2. Fall back to first string-valued arg.
//   3. Empty string if no args.
function summarizeArgs(name: string, args?: Record<string, unknown>): string {
  if (!args) return ''
  const preferredKeys: Record<string, string[]> = {
    Read: ['file_path', 'path'],
    Bash: ['command', 'cmd'],
    Grep: ['pattern', 'query'],
    dispatch_agent: ['task_spec', 'prompt', 'description', 'subagent_type'],
    code_review: ['target', 'focus'],
  }
  const keys = preferredKeys[name] ?? []
  for (const key of keys) {
    const v = args[key]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  // Fallback: first string-valued arg in iteration order.
  for (const v of Object.values(args)) {
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return ''
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1).trimEnd() + '…'
}

function formatDuration(ms?: number): string {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(ms < 10000 ? 2 : 1)}s`
}

export function ToolCallChip({
  name,
  displayName,
  args,
  status,
  durationMs,
  preview,
}: ToolCallChipProps) {
  // Preview is only shown on terminal frames AND only on user request — the
  // chip stays one line by default to avoid pushing the assistant reply off
  // screen during voice mode. Click to expand.
  const [expanded, setExpanded] = useState(false)
  // Rule #9 (no-narration spec): tool failures stay invisible. The owner
  // saw a red `TOOL Read · error chief-comma… 2ms` chip on a Read failure
  // — that's a leaked failure surface. State still mutates upstream so
  // logs / dispatch / retries can react; we just don't render the chip.
  // Early-return must come AFTER all hooks so render order stays stable
  // across status transitions (running → error).
  if (status === 'error') {
    return null
  }
  const Icon = iconFor(name)
  // Prefer the persona alias (Glass, etc.) over the raw tool ID. ``name`` is
  // the function-call ID (Read / Bash / code_review); ``displayName`` is
  // what Chief verbalizes and what the chip should show.
  const headerName = displayName ?? name

  const argsSummary = truncate(summarizeArgs(name, args), 80)
  // After the error early-return above, status is 'running' | 'complete' |
  // 'cancelled'. The preview surface is only meaningful on terminal frames,
  // and the only terminal status that survives is 'complete'.
  const hasPreview =
    status === 'complete' &&
    typeof preview === 'string' &&
    preview.trim().length > 0

  // Status-driven look. Mirrors InlineTaskActivity palette so dispatched-
  // tasks and tool-calls feel like the same family.
  const containerClass =
    status === 'running'
      ? 'bg-accent/5 border-accent/25 hover:bg-accent/10'
      : status === 'cancelled'
        ? 'bg-surface-overlay border-surface-border'
        : 'bg-emerald-50/60 border-emerald-200/70'

  const iconClass =
    status === 'running'
      ? 'text-accent-dark animate-spin'
      : status === 'cancelled'
        ? 'text-ink/50'
        : 'text-emerald-600'

  // Status badge — leading uppercase tag so the eye locks onto STATE before NAME.
  const statusLabel: Record<ToolCallStatus, string> = {
    running: 'Tool',
    complete: 'Tool',
    error: 'Tool',
    cancelled: 'Tool',
  }

  // Right-side meta. Running shows nothing (animation says it all). Complete
  // shows duration. Cancelled shows the literal word.
  let rightMeta = ''
  if (status === 'complete') {
    rightMeta = formatDuration(durationMs)
  } else if (status === 'cancelled') {
    rightMeta = 'cancelled'
  }

  // Status verb shown inline next to the tool name. Helps the owner read
  // the state in one glance — "Read · running" vs "Read · 36ms".
  const inlineVerb =
    status === 'running'
      ? 'running'
      : status === 'cancelled'
        ? 'cancelled'
        : null

  return (
    <div className="flex justify-start">
      <div className="w-full max-w-[85%] space-y-1.5">
        <button
          type="button"
          onClick={() => hasPreview && setExpanded((v) => !v)}
          aria-expanded={hasPreview ? expanded : undefined}
          aria-label={`${headerName} ${status}${argsSummary ? `: ${argsSummary}` : ''}`}
          disabled={!hasPreview}
          className={`group w-full flex items-center gap-2 px-2.5 py-1.5 rounded-md border text-left transition-colors min-h-[32px] ${containerClass} ${
            hasPreview
              ? 'cursor-pointer focus:outline-none focus-visible:ring-2 focus-visible:ring-primary/40 focus-visible:ring-offset-1 focus-visible:ring-offset-surface'
              : 'cursor-default'
          }`}
        >
          {status === 'running' ? (
            <Loader2 size={12} aria-hidden="true" className={`shrink-0 ${iconClass}`} />
          ) : status === 'cancelled' ? (
            <Ban size={12} aria-hidden="true" className={`shrink-0 ${iconClass}`} />
          ) : (
            <Check size={12} aria-hidden="true" className={`shrink-0 ${iconClass}`} />
          )}
          <Icon size={12} aria-hidden="true" className="shrink-0 text-ink/40" />
          <span className="text-[11px] font-semibold uppercase tracking-wide text-ink/45 shrink-0">
            {statusLabel[status]}
          </span>
          <span className="text-[12px] font-medium text-ink/80 shrink-0">{headerName}</span>
          {inlineVerb && (
            <span className="text-[11px] text-ink/45 shrink-0">· {inlineVerb}</span>
          )}
          {argsSummary && (
            <span className="text-[12px] font-mono text-ink/55 truncate min-w-0 flex-1">
              {argsSummary}
            </span>
          )}
          {!argsSummary && <span className="flex-1" />}
          {rightMeta && (
            <span className="text-[11px] font-mono text-ink/45 shrink-0">{rightMeta}</span>
          )}
          {hasPreview && (
            expanded ? (
              <ChevronDown size={12} aria-hidden="true" className="shrink-0 text-ink/40" />
            ) : (
              <ChevronRight size={12} aria-hidden="true" className="shrink-0 text-ink/30 md:opacity-0 md:group-hover:opacity-100 transition-opacity" />
            )
          )}
        </button>
        {hasPreview && expanded && (
          <pre className="text-[11px] font-mono text-ink/70 bg-surface-overlay/70 border border-surface-border rounded-md px-2.5 py-2 whitespace-pre-wrap break-words leading-relaxed max-h-48 overflow-y-auto">
            {preview}
          </pre>
        )}
      </div>
    </div>
  )
}

export default ToolCallChip
