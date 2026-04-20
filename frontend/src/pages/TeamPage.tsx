import { useState, useEffect, useCallback } from 'react'
import { ArrowLeft, Pencil, RefreshCw, Save, CheckCircle2, AlertCircle, BookOpen } from 'lucide-react'
import { api, type AgentProfile } from '../lib/api'

// ─── Constants ────────────────────────────────────────────────────────────────

const REVIEWER_NAMES = new Set(['Vera', 'Hawke', 'Sable', 'Pax', 'Quill', 'Hip'])

const MODEL_BADGE: Record<AgentProfile['model'], { label: string; cls: string }> = {
  opus: { label: 'Opus', cls: 'bg-indigo-500/20 text-indigo-700 border border-indigo-500/30' },
  sonnet: { label: 'Sonnet', cls: 'bg-ink/5 text-ink/35 border border-ink/10' },
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function avatarUrl(name: string, size?: number): string {
  const params = new URLSearchParams({ seed: name })
  if (size) params.set('size', String(size))
  return `https://api.dicebear.com/7.x/bottts/svg?${params.toString()}`
}

function formatRelativeTime(iso: string | null): string {
  if (!iso) return 'No activity'
  const diff = Date.now() - new Date(iso).getTime()
  const mins = Math.floor(diff / 60000)
  if (mins < 2) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

type Section = 'leadership' | 'opus' | 'builders' | 'reviewers'

interface SectionMeta {
  key: Section
  label: string
  sublabel: string
  tintClass: string
  headerClass: string
}

const SECTIONS: SectionMeta[] = [
  {
    key: 'leadership',
    label: 'Leadership',
    sublabel: 'Orchestrator',
    tintClass: 'bg-indigo-500/[0.04]',
    headerClass: 'text-indigo-600',
  },
  {
    key: 'opus',
    label: 'Opus Tier',
    sublabel: 'Senior specialists',
    tintClass: 'bg-indigo-500/[0.03]',
    headerClass: 'text-indigo-700/70',
  },
  {
    key: 'builders',
    label: 'Builders',
    sublabel: 'Frontend & backend',
    tintClass: 'bg-emerald-500/[0.03]',
    headerClass: 'text-emerald-600/70',
  },
  {
    key: 'reviewers',
    label: 'Reviewers',
    sublabel: 'Quality & validation',
    tintClass: 'bg-amber-500/[0.03]',
    headerClass: 'text-accent-dark/70',
  },
]

function sectionFor(agent: AgentProfile): Section {
  if (agent.tier === 'chief') return 'leadership'
  if (agent.tier === 'opus') return 'opus'
  if (REVIEWER_NAMES.has(agent.name)) return 'reviewers'
  return 'builders'
}

// ─── Memory Parser ────────────────────────────────────────────────────────────

interface ParsedLesson {
  date?: string
  tag?: string
  body: string
}

interface ParsedMemory {
  personality?: string
  tools?: string
  lane?: string[]
  notLane?: string[]
  outputFormat?: string
  lessons: ParsedLesson[]
  raw: string
}

/**
 * Parse agent memory markdown into structured sections.
 * Sections are h2s (## Header). Lessons are paragraphs starting with **(date tag)**.
 */
function parseAgentMemory(md: string): ParsedMemory {
  const result: ParsedMemory = { lessons: [], raw: md }

  if (!md.trim()) return result

  // Strip frontmatter
  const withoutFrontmatter = md.replace(/^---[\s\S]*?---\n/, '').trim()

  // Split into h2 sections
  const sectionRegex = /^## (.+)$/gm
  const sectionStarts: Array<{ title: string; index: number }> = []
  let m: RegExpExecArray | null
  while ((m = sectionRegex.exec(withoutFrontmatter)) !== null) {
    sectionStarts.push({ title: m[1].trim(), index: m.index })
  }

  function extractSection(title: string): string | undefined {
    const found = sectionStarts.find(
      (s) => s.title.toLowerCase() === title.toLowerCase()
    )
    if (!found) return undefined
    const start = withoutFrontmatter.indexOf('\n', found.index) + 1
    const nextSection = sectionStarts.find((s) => s.index > found.index)
    const end = nextSection ? nextSection.index : withoutFrontmatter.length
    return withoutFrontmatter.slice(start, end).trim()
  }

  // Personality
  result.personality = extractSection('Personality')

  // Tools
  result.tools = extractSection('Tools')

  // Lane — parse bullet list
  const laneRaw = extractSection('Lane')
  if (laneRaw) {
    result.lane = laneRaw
      .split('\n')
      .filter((l) => l.trimStart().startsWith('-'))
      .map((l) => l.replace(/^\s*-\s*/, '').trim())
      .filter(Boolean)
  }

  // NOT lane — parse bullet list
  const notLaneRaw = extractSection('NOT lane')
  if (notLaneRaw) {
    result.notLane = notLaneRaw
      .split('\n')
      .filter((l) => l.trimStart().startsWith('-'))
      .map((l) => l.replace(/^\s*-\s*/, '').trim())
      .filter(Boolean)
  }

  // Output format
  result.outputFormat = extractSection('Output format')

  // Lessons accrued — paragraphs starting with **(...)** bold marker
  const lessonsRaw = extractSection('Lessons accrued')
  if (lessonsRaw) {
    // Split on blank lines to get paragraphs
    const paragraphs = lessonsRaw.split(/\n\n+/).map((p) => p.trim()).filter(Boolean)
    const lessonRegex = /^\*\*\(([^)]+)\)\*\*\s*([\s\S]*)$/
    for (const para of paragraphs) {
      const match = lessonRegex.exec(para)
      if (match) {
        const tag = match[1].trim()
        const body = match[2].trim()
        // Parse date from tag — look for YYYY-MM-DD
        const dateMatch = /(\d{4}-\d{2}-\d{2})/.exec(tag)
        const date = dateMatch ? dateMatch[1] : undefined
        // Tag is everything after the date
        const tagLabel = date ? tag.replace(date, '').replace(/^[\s-]+/, '').trim() : tag
        result.lessons.push({ date, tag: tagLabel || undefined, body })
      } else if (para !== '*No lessons accrued yet.*' && para !== 'No lessons accrued yet.') {
        // Plain paragraph without the bold date marker
        result.lessons.push({ body: para })
      }
    }
  }

  return result
}

/**
 * Parse tool tokens from a tools string.
 * Tool names are capitalized words. Bash scope note is everything after "Bash scoped to:".
 */
function parseTools(toolsStr: string): { chips: string[]; scopeNote?: string } {
  const scopeMatch = /Bash scoped to:\s*([^\n]+(?:\n(?!\n)[^\n]+)*)/i.exec(toolsStr)
  const scopeNote = scopeMatch ? scopeMatch[1].trim() : undefined

  // Extract tool names: comma-separated capitalized words before any scope note
  const beforeScope = scopeMatch
    ? toolsStr.slice(0, scopeMatch.index)
    : toolsStr

  const chips = beforeScope
    .split(/[,.\n]+/)
    .map((t) => t.replace(/[`*]/g, '').trim())
    .filter((t) => t.length > 0 && /^[A-Z]/.test(t))

  return { chips, scopeNote }
}

// ─── Detail view ──────────────────────────────────────────────────────────────

interface DetailViewProps {
  agent: AgentProfile
  onBack: () => void
  onEdit: () => void
}

function DetailView({ agent, onBack, onEdit }: DetailViewProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.team.getMemory(agent.name)
      .then((data) => { if (!cancelled) setContent(data.content) })
      .catch(() => { if (!cancelled) setErrorMsg('Failed to load memory') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [agent.name])

  const parsed = content ? parseAgentMemory(content) : null
  const hasStructure = parsed && (
    parsed.personality ||
    parsed.tools ||
    (parsed.lane && parsed.lane.length > 0) ||
    (parsed.notLane && parsed.notLane.length > 0) ||
    parsed.outputFormat ||
    parsed.lessons.length > 0
  )

  return (
    <div className="fixed inset-0 z-40 flex flex-col bg-surface animate-[fadeIn_0.18s_ease-out]">
      {/* Header */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-surface-border bg-surface-raised">
        <button
          onClick={onBack}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-ink/40 hover:text-ink/80 transition-colors"
          aria-label="Back to team"
        >
          <ArrowLeft size={18} />
        </button>

        <div className="w-9 h-9 rounded-full overflow-hidden bg-indigo-500/10 shrink-0">
          <img
            src={avatarUrl(agent.name)}
            alt={agent.name}
            loading="eager"
            className="w-full h-full object-cover"
          />
        </div>

        <div className="flex-1 min-w-0">
          <p className="font-display font-semibold text-ink leading-tight truncate">{agent.name}</p>
          <p className="text-xs text-ink/40 truncate">{agent.role}</p>
        </div>

        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      {/* Scrollable body */}
      <div className="flex-1 overflow-y-auto min-h-0">
        {loading ? (
          <div className="flex items-center justify-center h-48 text-ink/30 text-sm">
            Loading…
          </div>
        ) : errorMsg ? (
          <div className="flex flex-col items-center justify-center h-48 gap-3">
            <p className="text-ink/40 text-sm">{errorMsg}</p>
            <button
              onClick={onEdit}
              className="text-xs text-ink/40 underline underline-offset-2 hover:text-ink/70 transition-colors"
            >
              Edit raw instead
            </button>
          </div>
        ) : !hasStructure ? (
          /* Fallback — raw content block */
          <div className="px-4 py-5 space-y-4">
            <p className="text-xs text-ink/30 text-center">Memory file doesn't have standard sections yet.</p>
            {parsed?.raw && (
              <pre className="bg-surface-raised text-ink/80 text-sm font-mono p-4 rounded-xl overflow-x-auto whitespace-pre-wrap">
                {parsed.raw}
              </pre>
            )}
            <button
              onClick={onEdit}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border border-ink/10 text-ink/50 text-sm hover:text-ink/80 hover:border-ink/20 transition-colors"
            >
              <Pencil size={13} />
              Edit raw
            </button>
          </div>
        ) : (
          <div className="px-4 py-5 space-y-4">

            {/* Personality */}
            {parsed.personality && (
              <div className="rounded-2xl bg-surface-raised border border-surface-border p-5">
                <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-ink/30 mb-3">
                  Personality
                </h3>
                <p className="text-sm text-ink/75 leading-relaxed">{parsed.personality}</p>
              </div>
            )}

            {/* Tools */}
            {parsed.tools && (() => {
              const { chips, scopeNote } = parseTools(parsed.tools)
              return (
                <div className="rounded-2xl bg-surface-raised border border-surface-border p-5">
                  <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-ink/30 mb-3">
                    Tools
                  </h3>
                  {chips.length > 0 && (
                    <div className="flex flex-wrap gap-2 mb-3">
                      {chips.map((chip) => (
                        <span
                          key={chip}
                          className="inline-block text-xs px-2.5 py-1 rounded-lg bg-ink/6 border border-ink/10 text-ink/65 font-mono"
                        >
                          {chip}
                        </span>
                      ))}
                    </div>
                  )}
                  {scopeNote && (
                    <p className="text-[11px] text-ink/30 leading-relaxed">
                      Bash scoped to: {scopeNote}
                    </p>
                  )}
                </div>
              )
            })()}

            {/* Lane */}
            {parsed.lane && parsed.lane.length > 0 && (
              <div className="rounded-2xl bg-surface-raised border border-surface-border p-5">
                <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-ink/30 mb-3">
                  Lane
                </h3>
                <ul className="space-y-2">
                  {parsed.lane.map((item, i) => (
                    <li key={i} className="flex gap-2 text-sm text-ink/70 leading-relaxed">
                      <span className="mt-1.5 w-1 h-1 rounded-full bg-emerald-400/50 shrink-0" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* NOT Lane */}
            {parsed.notLane && parsed.notLane.length > 0 && (
              <div className="rounded-2xl bg-red-500/[0.04] border border-red-500/15 p-5">
                <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-red-600/60 mb-3">
                  NOT Lane
                </h3>
                <ul className="space-y-2">
                  {parsed.notLane.map((item, i) => (
                    <li key={i} className="flex gap-2 text-sm text-ink/55 leading-relaxed">
                      <span className="mt-1.5 w-1 h-1 rounded-full bg-red-400/40 shrink-0" />
                      {item}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* Output Format */}
            {parsed.outputFormat && (
              <div className="rounded-2xl bg-surface-raised border border-surface-border p-5">
                <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-ink/30 mb-3">
                  Output Format
                </h3>
                <pre className="bg-surface-raised text-ink/80 text-sm font-mono p-4 rounded-xl overflow-x-auto whitespace-pre-wrap border border-ink/5">
                  {parsed.outputFormat}
                </pre>
              </div>
            )}

            {/* Lessons — prominent standalone block */}
            <div className="rounded-2xl bg-indigo-500/[0.05] border border-indigo-500/15 p-5">
              <div className="flex items-center gap-2 mb-4">
                <BookOpen size={14} className="text-indigo-600/70" />
                <h3 className="font-display font-semibold text-xs uppercase tracking-widest text-indigo-600/70">
                  Lessons Accrued
                </h3>
                <span className="ml-auto text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-700 border border-indigo-500/20 font-medium">
                  {parsed.lessons.length}
                </span>
              </div>

              {parsed.lessons.length === 0 ? (
                <p className="text-sm text-ink/25 italic text-center py-4">
                  No lessons accrued yet.
                </p>
              ) : (
                <div className="space-y-3">
                  {parsed.lessons.map((lesson, i) => (
                    <div
                      key={i}
                      className="rounded-xl bg-surface-raised border border-ink/5 p-4"
                    >
                      {(lesson.date || lesson.tag) && (
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                          {lesson.date && (
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-indigo-500/15 text-indigo-700/80 border border-indigo-500/20 font-mono">
                              {lesson.date}
                            </span>
                          )}
                          {lesson.tag && (
                            <span className="text-[10px] text-ink/35 font-medium">
                              {lesson.tag}
                            </span>
                          )}
                        </div>
                      )}
                      <p className="text-sm text-ink/65 leading-relaxed">{lesson.body}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Edit raw button */}
            <button
              onClick={onEdit}
              className="w-full flex items-center justify-center gap-2 py-3 rounded-xl border border-ink/8 text-ink/40 text-sm hover:text-ink/70 hover:border-ink/15 transition-colors"
            >
              <Pencil size={13} />
              Edit raw
            </button>

            {/* Bottom padding */}
            <div className="h-6" />
          </div>
        )}
      </div>
    </div>
  )
}

// ─── Full-page editor ─────────────────────────────────────────────────────────

type SaveState = 'idle' | 'saving' | 'saved' | 'error'

interface EditorViewProps {
  agent: AgentProfile
  onClose: () => void
}

function EditorView({ agent, onClose }: EditorViewProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saveState, setSaveState] = useState<SaveState>('idle')
  const [errorMsg, setErrorMsg] = useState('')

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.team.getMemory(agent.name)
      .then((data) => { if (!cancelled) setContent(data.content) })
      .catch(() => { if (!cancelled) setErrorMsg('Failed to load memory') })
      .finally(() => { if (!cancelled) setLoading(false) })
    return () => { cancelled = true }
  }, [agent.name])

  async function handleSave() {
    setSaveState('saving')
    setErrorMsg('')
    try {
      await api.team.updateMemory(agent.name, content)
      setSaveState('saved')
      setTimeout(() => setSaveState('idle'), 2500)
    } catch {
      setSaveState('error')
      setErrorMsg('Save failed — check your connection and try again.')
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex flex-col bg-surface animate-[fadeIn_0.18s_ease-out]">
      {/* Editor header */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-b border-surface-border bg-surface-raised">
        <button
          onClick={onClose}
          className="w-8 h-8 flex items-center justify-center rounded-lg text-ink/40 hover:text-ink/80 transition-colors"
          aria-label="Back"
        >
          <ArrowLeft size={18} />
        </button>

        <div className="w-9 h-9 rounded-full overflow-hidden bg-indigo-500/10 shrink-0">
          <img
            src={avatarUrl(agent.name)}
            alt={agent.name}
            loading="eager"
            className="w-full h-full object-cover"
          />
        </div>

        <div className="flex-1 min-w-0">
          <p className="font-display font-semibold text-ink leading-tight truncate">{agent.name}</p>
          <p className="text-xs text-ink/40 truncate">{agent.role}</p>
        </div>

        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      {/* Editor body — fills all remaining space */}
      <div className="flex-1 relative min-h-0">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center text-ink/30 text-sm">
            Loading memory…
          </div>
        ) : (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="absolute inset-0 w-full h-full bg-transparent text-ink/80 font-mono text-sm leading-7 resize-none focus:outline-none p-6 md:px-12 md:py-8 placeholder-ink/20"
            placeholder={`No memory yet for ${agent.name}. Start writing…`}
            spellCheck={false}
            autoFocus
          />
        )}
      </div>

      {/* Editor footer */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 border-t border-surface-border bg-surface-raised">
        {/* Save state indicator */}
        <div className="flex-1 flex items-center gap-2 min-w-0">
          {saveState === 'saved' && (
            <span className="flex items-center gap-1.5 text-xs text-emerald-600 animate-[fadeIn_0.15s_ease-out]">
              <CheckCircle2 size={13} />
              Saved
            </span>
          )}
          {saveState === 'error' && (
            <span className="flex items-center gap-1.5 text-xs text-red-600 truncate">
              <AlertCircle size={13} className="shrink-0" />
              <span className="truncate">{errorMsg}</span>
            </span>
          )}
        </div>

        <button
          onClick={onClose}
          className="px-4 py-2 rounded-xl text-sm text-ink/50 hover:text-ink/80 transition-colors"
        >
          Cancel
        </button>

        <button
          onClick={handleSave}
          disabled={loading || saveState === 'saving'}
          className="flex items-center gap-2 px-5 py-2 rounded-xl bg-chief text-white text-sm font-medium transition-opacity disabled:opacity-40 hover:bg-chief-dark active:opacity-80"
        >
          <Save size={14} />
          {saveState === 'saving' ? 'Saving…' : 'Save'}
        </button>
      </div>
    </div>
  )
}

// ─── Chief Hero ───────────────────────────────────────────────────────────────

interface ChiefHeroProps {
  agent: AgentProfile
  onView: () => void
  onEdit: () => void
}

function ChiefHero({ agent, onView, onEdit }: ChiefHeroProps) {
  return (
    <button
      onClick={onView}
      className="relative w-full px-5 pt-8 pb-7 flex flex-col items-center text-center bg-surface-raised border-b border-surface-border text-left"
    >
      {/* Edit button */}
      <button
        onClick={(e) => { e.stopPropagation(); onEdit() }}
        className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-lg text-ink/30 hover:text-ink/70 hover:bg-ink/5 transition-colors"
        aria-label={`Edit ${agent.name} memory`}
      >
        <Pencil size={14} />
      </button>

      {/* Avatar */}
      <div className="w-40 h-40 rounded-full overflow-hidden bg-primary/5 border-2 border-primary/15 mb-5 shadow-card-hover">
        <img
          src={avatarUrl(agent.name, 160)}
          alt={agent.name}
          loading="eager"
          className="w-full h-full object-cover"
        />
      </div>

      {/* Name + badge */}
      <div className="flex items-center gap-2 mb-1">
        <h1 className="font-display text-3xl font-bold text-ink tracking-tight">{agent.name}</h1>
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      <p className="font-display text-base font-medium text-indigo-700 mb-2">{agent.role}</p>
      <p className="text-sm text-ink/45 leading-relaxed max-w-xs">{agent.lean}</p>

      {agent.invocations_total > 0 && (
        <p className="mt-3 text-xs text-indigo-600/60">
          {agent.invocations_total.toLocaleString()} invocations
        </p>
      )}
      <p className="text-[11px] text-ink/20 mt-1">{formatRelativeTime(agent.last_active)}</p>
    </button>
  )
}

// ─── Agent card ───────────────────────────────────────────────────────────────

interface AgentCardProps {
  agent: AgentProfile
  onView: (agent: AgentProfile) => void
  onEdit: (agent: AgentProfile) => void
}

function AgentCard({ agent, onView, onEdit }: AgentCardProps) {
  return (
    <div className="group relative rounded-2xl bg-surface-raised border border-surface-border hover:border-ink/10 transition-colors overflow-hidden">
      {/* Clickable body — goes to detail */}
      <button
        onClick={() => onView(agent)}
        className="w-full flex flex-col gap-3 p-5 text-left"
        aria-label={`View ${agent.name} details`}
      >
        {/* Avatar */}
        <div className="w-20 h-20 rounded-full overflow-hidden bg-ink/5 border border-ink/8 mx-auto">
          <img
            src={avatarUrl(agent.name)}
            alt={agent.name}
            loading="lazy"
            className="w-full h-full object-cover"
          />
        </div>

        {/* Info */}
        <div className="text-center">
          <div className="flex items-center justify-center gap-1.5 mb-0.5">
            <span className="font-display font-semibold text-ink text-base leading-tight">{agent.name}</span>
          </div>
          <p className="text-xs text-ink/50 mb-1">{agent.role}</p>
          <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
            {MODEL_BADGE[agent.model].label}
          </span>
        </div>

        {/* Lean / tagline */}
        <p className="text-[11px] text-ink/30 leading-relaxed line-clamp-2 text-center">{agent.lean}</p>

        {/* Last active */}
        <p className="text-[10px] text-ink/20 text-center mt-auto">{formatRelativeTime(agent.last_active)}</p>
      </button>

      {/* Edit pencil — separate interactive region */}
      <button
        onClick={(e) => { e.stopPropagation(); onEdit(agent) }}
        className="absolute top-3 right-3 w-8 h-8 flex items-center justify-center rounded-lg text-ink/40 hover:text-ink/80 hover:bg-ink/5 active:bg-ink/10 transition-all"
        aria-label={`Edit ${agent.name} memory`}
      >
        <Pencil size={13} />
      </button>
    </div>
  )
}

// ─── Tier section ─────────────────────────────────────────────────────────────

interface TierSectionProps {
  meta: SectionMeta
  agents: AgentProfile[]
  onView: (agent: AgentProfile) => void
  onEdit: (agent: AgentProfile) => void
}

function TierSection({ meta, agents, onView, onEdit }: TierSectionProps) {
  if (agents.length === 0) return null

  return (
    <div className={`rounded-2xl overflow-hidden border border-ink/5 ${meta.tintClass}`}>
      {/* Section header */}
      <div className="px-5 pt-5 pb-3">
        <div className="flex items-baseline gap-2">
          <h2 className={`font-display font-semibold text-sm uppercase tracking-widest ${meta.headerClass}`}>
            {meta.label}
          </h2>
          <span className="text-xs text-ink/20">{meta.sublabel}</span>
        </div>
      </div>

      {/* Cards grid */}
      <div className="px-4 pb-5 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
        {agents.map((agent) => (
          <AgentCard key={agent.name} agent={agent} onView={onView} onEdit={onEdit} />
        ))}
      </div>
    </div>
  )
}

// ─── TeamPage ─────────────────────────────────────────────────────────────────

export default function TeamPage() {
  const [agents, setAgents] = useState<AgentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [viewingAgent, setViewingAgent] = useState<AgentProfile | null>(null)
  const [editingAgent, setEditingAgent] = useState<AgentProfile | null>(null)

  const fetchTeam = useCallback(async () => {
    setError('')
    try {
      const data = await api.team.list()
      setAgents(data.agents)
    } catch {
      setError('Failed to load team')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchTeam()
  }, [fetchTeam])

  if (loading && agents.length === 0) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-ink/30 text-sm">Loading team…</div>
      </div>
    )
  }

  if (error && agents.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-ink/40 text-sm">{error}</p>
        <button
          onClick={fetchTeam}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-ink/60 text-sm hover:text-ink transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  // Group agents by section
  const chief = agents.find((a) => a.tier === 'chief')
  const sectionMap: Record<Section, AgentProfile[]> = {
    leadership: [],
    opus: [],
    builders: [],
    reviewers: [],
  }
  for (const agent of agents) {
    const sec = sectionFor(agent)
    if (sec !== 'leadership') sectionMap[sec].push(agent)
  }

  const nonLeadershipSections = SECTIONS.filter((s) => s.key !== 'leadership')

  // Handler: from detail view, switch to raw editor
  function openEditorFromDetail(agent: AgentProfile) {
    setViewingAgent(null)
    setEditingAgent(agent)
  }

  // Handler: close editor — if there's a viewing agent context, return to detail
  function closeEditor() {
    setEditingAgent(null)
    // viewingAgent is still set if we came from detail, so it will re-render
  }

  return (
    <>
      <div className="h-full overflow-y-auto">
        {/* Sticky top bar */}
        <div className="sticky top-0 z-10 bg-surface/80 backdrop-blur-sm border-b border-surface-border px-4 py-3">
          <div className="flex items-center gap-2">
            <h1 className="font-display text-base font-semibold text-ink">Team</h1>
            <span className="text-xs text-ink/30 ml-auto">
              {agents.length} agent{agents.length !== 1 ? 's' : ''}
            </span>
            <button
              onClick={fetchTeam}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-ink/30 hover:text-ink/60 transition-colors"
              aria-label="Refresh"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        {/* Content */}
        <div className="px-4 py-5 space-y-5">
          {/* Chief hero */}
          {chief && (
            <div className="rounded-3xl overflow-hidden border border-surface-border shadow-card">
              <ChiefHero
                agent={chief}
                onView={() => setViewingAgent(chief)}
                onEdit={() => setEditingAgent(chief)}
              />
            </div>
          )}

          {/* Tier sections */}
          {nonLeadershipSections.map((meta) => (
            <TierSection
              key={meta.key}
              meta={meta}
              agents={sectionMap[meta.key]}
              onView={setViewingAgent}
              onEdit={setEditingAgent}
            />
          ))}

          {agents.length === 0 && !loading && (
            <div className="text-center py-16 text-ink/30 text-sm">No team members found</div>
          )}
        </div>
      </div>

      {/* Detail view — z-40, below editor */}
      {viewingAgent && !editingAgent && (
        <DetailView
          agent={viewingAgent}
          onBack={() => setViewingAgent(null)}
          onEdit={() => openEditorFromDetail(viewingAgent)}
        />
      )}

      {/* Full-page editor — z-50, on top */}
      {editingAgent && (
        <EditorView
          agent={editingAgent}
          onClose={closeEditor}
        />
      )}
    </>
  )
}
