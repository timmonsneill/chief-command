import { useState, useEffect, useCallback } from 'react'
import { ArrowLeft, Pencil, RefreshCw, Save, CheckCircle2, AlertCircle } from 'lucide-react'
import { api, type AgentProfile } from '../lib/api'

// ─── Constants ────────────────────────────────────────────────────────────────

const REVIEWER_NAMES = new Set(['Vera', 'Hawke', 'Sable', 'Pax', 'Quill', 'Hip'])

const MODEL_BADGE: Record<AgentProfile['model'], { label: string; cls: string }> = {
  opus: { label: 'Opus', cls: 'bg-indigo-500/20 text-indigo-300 border border-indigo-500/30' },
  sonnet: { label: 'Sonnet', cls: 'bg-white/5 text-white/35 border border-white/10' },
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
    headerClass: 'text-indigo-400',
  },
  {
    key: 'opus',
    label: 'Opus Tier',
    sublabel: 'Senior specialists',
    tintClass: 'bg-indigo-500/[0.03]',
    headerClass: 'text-indigo-300/70',
  },
  {
    key: 'builders',
    label: 'Builders',
    sublabel: 'Frontend & backend',
    tintClass: 'bg-emerald-500/[0.03]',
    headerClass: 'text-emerald-400/70',
  },
  {
    key: 'reviewers',
    label: 'Reviewers',
    sublabel: 'Quality & validation',
    tintClass: 'bg-amber-500/[0.03]',
    headerClass: 'text-amber-400/70',
  },
]

function sectionFor(agent: AgentProfile): Section {
  if (agent.tier === 'chief') return 'leadership'
  if (agent.tier === 'opus') return 'opus'
  if (REVIEWER_NAMES.has(agent.name)) return 'reviewers'
  return 'builders'
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
      await api.team.putMemory(agent.name, content)
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
          className="w-8 h-8 flex items-center justify-center rounded-lg text-white/40 hover:text-white/80 transition-colors"
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
          <p className="font-display font-semibold text-white leading-tight truncate">{agent.name}</p>
          <p className="text-xs text-white/40 truncate">{agent.role}</p>
        </div>

        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      {/* Editor body — fills all remaining space */}
      <div className="flex-1 relative min-h-0">
        {loading ? (
          <div className="absolute inset-0 flex items-center justify-center text-white/30 text-sm">
            Loading memory…
          </div>
        ) : (
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="absolute inset-0 w-full h-full bg-transparent text-white/80 font-mono text-sm leading-7 resize-none focus:outline-none p-6 md:px-12 md:py-8 placeholder-white/20"
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
            <span className="flex items-center gap-1.5 text-xs text-emerald-400 animate-[fadeIn_0.15s_ease-out]">
              <CheckCircle2 size={13} />
              Saved
            </span>
          )}
          {saveState === 'error' && (
            <span className="flex items-center gap-1.5 text-xs text-red-400 truncate">
              <AlertCircle size={13} className="shrink-0" />
              <span className="truncate">{errorMsg}</span>
            </span>
          )}
        </div>

        <button
          onClick={onClose}
          className="px-4 py-2 rounded-xl text-sm text-white/50 hover:text-white/80 transition-colors"
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
  onEdit: () => void
}

function ChiefHero({ agent, onEdit }: ChiefHeroProps) {
  return (
    <div className="relative px-5 pt-8 pb-7 flex flex-col items-center text-center bg-indigo-500/[0.04] border-b border-indigo-500/10">
      {/* Edit button */}
      <button
        onClick={onEdit}
        className="absolute top-4 right-4 w-8 h-8 flex items-center justify-center rounded-lg text-white/30 hover:text-white/70 hover:bg-white/5 transition-colors"
        aria-label={`Edit ${agent.name} memory`}
      >
        <Pencil size={14} />
      </button>

      {/* Avatar */}
      <div className="w-40 h-40 rounded-full overflow-hidden bg-indigo-500/10 border-2 border-indigo-500/20 mb-5 shadow-2xl">
        <img
          src={avatarUrl(agent.name, 160)}
          alt={agent.name}
          loading="eager"
          className="w-full h-full object-cover"
        />
      </div>

      {/* Name + badge */}
      <div className="flex items-center gap-2 mb-1">
        <h1 className="font-display text-3xl font-bold text-white tracking-tight">{agent.name}</h1>
        <span className={`text-[10px] px-2 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      <p className="font-display text-base font-medium text-indigo-300 mb-2">{agent.role}</p>
      <p className="text-sm text-white/45 leading-relaxed max-w-xs">{agent.lean}</p>

      {agent.invocations_total > 0 && (
        <p className="mt-3 text-xs text-indigo-400/60">
          {agent.invocations_total.toLocaleString()} invocations
        </p>
      )}
      <p className="text-[11px] text-white/20 mt-1">{formatRelativeTime(agent.last_active)}</p>
    </div>
  )
}

// ─── Agent card ───────────────────────────────────────────────────────────────

interface AgentCardProps {
  agent: AgentProfile
  onEdit: (agent: AgentProfile) => void
}

function AgentCard({ agent, onEdit }: AgentCardProps) {
  return (
    <div className="group relative flex flex-col gap-3 p-5 rounded-2xl bg-surface-raised border border-surface-border hover:border-white/10 transition-colors">
      {/* Edit pencil */}
      <button
        onClick={() => onEdit(agent)}
        className="absolute top-3 right-3 w-7 h-7 flex items-center justify-center rounded-lg text-white/0 group-hover:text-white/40 hover:!text-white/80 hover:bg-white/5 transition-all"
        aria-label={`Edit ${agent.name} memory`}
      >
        <Pencil size={13} />
      </button>

      {/* Avatar */}
      <div className="w-20 h-20 rounded-full overflow-hidden bg-white/5 border border-white/8 mx-auto">
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
          <span className="font-display font-semibold text-white text-base leading-tight">{agent.name}</span>
        </div>
        <p className="text-xs text-white/50 mb-1">{agent.role}</p>
        <span className={`inline-block text-[9px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model].cls}`}>
          {MODEL_BADGE[agent.model].label}
        </span>
      </div>

      {/* Lean / tagline */}
      <p className="text-[11px] text-white/30 leading-relaxed line-clamp-2 text-center">{agent.lean}</p>

      {/* Last active */}
      <p className="text-[10px] text-white/20 text-center mt-auto">{formatRelativeTime(agent.last_active)}</p>
    </div>
  )
}

// ─── Tier section ─────────────────────────────────────────────────────────────

interface TierSectionProps {
  meta: SectionMeta
  agents: AgentProfile[]
  onEdit: (agent: AgentProfile) => void
}

function TierSection({ meta, agents, onEdit }: TierSectionProps) {
  if (agents.length === 0) return null

  return (
    <div className={`rounded-2xl overflow-hidden border border-white/5 ${meta.tintClass}`}>
      {/* Section header */}
      <div className="px-5 pt-5 pb-3">
        <div className="flex items-baseline gap-2">
          <h2 className={`font-display font-semibold text-sm uppercase tracking-widest ${meta.headerClass}`}>
            {meta.label}
          </h2>
          <span className="text-xs text-white/20">{meta.sublabel}</span>
        </div>
      </div>

      {/* Cards grid */}
      <div className="px-4 pb-5 grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
        {agents.map((agent) => (
          <AgentCard key={agent.name} agent={agent} onEdit={onEdit} />
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
        <div className="text-white/30 text-sm">Loading team…</div>
      </div>
    )
  }

  if (error && agents.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error}</p>
        <button
          onClick={fetchTeam}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-white/60 text-sm hover:text-white transition-colors"
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

  return (
    <>
      <div className="h-full overflow-y-auto">
        {/* Sticky top bar */}
        <div className="sticky top-0 z-10 bg-surface/80 backdrop-blur-sm border-b border-surface-border px-4 py-3">
          <div className="flex items-center gap-2">
            <h1 className="font-display text-base font-semibold text-white">Team</h1>
            <span className="text-xs text-white/30 ml-auto">
              {agents.length} agent{agents.length !== 1 ? 's' : ''}
            </span>
            <button
              onClick={fetchTeam}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-white/30 hover:text-white/60 transition-colors"
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
            <div className="rounded-3xl overflow-hidden border border-indigo-500/10">
              <ChiefHero agent={chief} onEdit={() => setEditingAgent(chief)} />
            </div>
          )}

          {/* Tier sections */}
          {nonLeadershipSections.map((meta) => (
            <TierSection
              key={meta.key}
              meta={meta}
              agents={sectionMap[meta.key]}
              onEdit={setEditingAgent}
            />
          ))}

          {agents.length === 0 && !loading && (
            <div className="text-center py-16 text-white/30 text-sm">No team members found</div>
          )}
        </div>
      </div>

      {/* Full-page editor — mounts over everything */}
      {editingAgent && (
        <EditorView
          agent={editingAgent}
          onClose={() => setEditingAgent(null)}
        />
      )}
    </>
  )
}
