import { useState, useEffect, useCallback } from 'react'
import { Users, RefreshCw, X, Save } from 'lucide-react'
import { api, type AgentProfile } from '../lib/api'

const TIER_ORDER: AgentProfile['tier'][] = ['chief', 'opus', 'sonnet']

const TIER_LABELS: Record<AgentProfile['tier'], string> = {
  chief: 'Orchestrator',
  opus: 'Opus tier',
  sonnet: 'Sonnet tier',
}

const MODEL_BADGE: Record<AgentProfile['model'], string> = {
  opus: 'bg-chief/20 text-chief-light',
  sonnet: 'bg-white/5 text-white/40',
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

interface MemoryModalProps {
  agent: AgentProfile
  onClose: () => void
}

function MemoryModal({ agent, onClose }: MemoryModalProps) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saveSuccess, setSaveSuccess] = useState(false)

  useEffect(() => {
    let cancelled = false
    async function fetchMemory() {
      setLoading(true)
      setError('')
      try {
        const data = await api.team.getMemory(agent.name)
        if (!cancelled) setContent(data.content)
      } catch {
        if (!cancelled) setError('Failed to load memory')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    fetchMemory()
    return () => { cancelled = true }
  }, [agent.name])

  async function handleSave() {
    setSaving(true)
    setError('')
    setSaveSuccess(false)
    try {
      await api.team.updateMemory(agent.name, content)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 2000)
    } catch {
      setError('Failed to save')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-end sm:items-center justify-center bg-black/70 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      <div className="w-full sm:w-[540px] sm:max-h-[80vh] max-h-[90vh] flex flex-col bg-surface-raised border border-surface-border sm:rounded-2xl rounded-t-2xl overflow-hidden">
        {/* Modal header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border shrink-0">
          <div>
            <span className="text-sm font-semibold text-white">{agent.name}</span>
            <span className="text-xs text-white/40 ml-2">memory</span>
          </div>
          <button
            onClick={onClose}
            className="w-8 h-8 flex items-center justify-center rounded-lg text-white/40 active:text-white/60 transition-colors"
          >
            <X size={16} />
          </button>
        </div>

        {/* Content area */}
        <div className="flex-1 overflow-hidden flex flex-col p-4 gap-3 min-h-0">
          {loading ? (
            <div className="flex items-center justify-center flex-1 text-white/30 text-sm">
              Loading memory...
            </div>
          ) : (
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              className="flex-1 w-full bg-surface border border-surface-border rounded-lg p-3 text-xs text-white/70 font-mono leading-relaxed resize-none focus:outline-none focus:border-chief/50 placeholder-white/20 min-h-[200px]"
              placeholder={`No memory yet for ${agent.name}. Add notes here.`}
              spellCheck={false}
            />
          )}

          {error && (
            <p className="text-xs text-status-offline">{error}</p>
          )}

          <button
            onClick={handleSave}
            disabled={loading || saving}
            className="flex items-center justify-center gap-2 w-full py-2.5 rounded-xl bg-chief text-white text-sm font-medium transition-opacity disabled:opacity-40 active:opacity-80"
          >
            <Save size={14} />
            {saving ? 'Saving...' : saveSuccess ? 'Saved!' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}

interface AgentCardProps {
  agent: AgentProfile
  onTap: (agent: AgentProfile) => void
}

function AgentCard({ agent, onTap }: AgentCardProps) {
  const isChief = agent.tier === 'chief'

  if (isChief) {
    return (
      <button
        onClick={() => onTap(agent)}
        className="w-full text-left p-5 rounded-2xl bg-chief/10 border border-chief/30 active:bg-chief/20 transition-colors"
      >
        <div className="flex items-start justify-between gap-3 mb-3">
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 mb-1">
              <span className="text-xl font-bold text-white">{agent.name}</span>
              <span
                className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model]}`}
              >
                {agent.model}
              </span>
            </div>
            <p className="text-sm text-chief-light font-medium">{agent.role}</p>
          </div>
          <div className="shrink-0 w-10 h-10 rounded-full bg-chief/30 border border-chief/50 flex items-center justify-center">
            <span className="text-chief text-lg font-bold">C</span>
          </div>
        </div>
        <p className="text-xs text-white/50 leading-relaxed mb-3">{agent.description}</p>
        <div className="flex items-center justify-between">
          <span className="text-[10px] text-white/30 truncate pr-2">{agent.lean}</span>
          <span className="text-[10px] text-white/20 shrink-0">
            {formatRelativeTime(agent.last_active)}
          </span>
        </div>
        {agent.invocations_total > 0 && (
          <p className="text-[10px] text-chief/60 mt-1">
            {agent.invocations_total.toLocaleString()} invocations
          </p>
        )}
      </button>
    )
  }

  return (
    <button
      onClick={() => onTap(agent)}
      className="w-full text-left p-4 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors"
    >
      <div className="flex items-start justify-between gap-2 mb-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold text-white">{agent.name}</span>
            <span
              className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium uppercase tracking-wide ${MODEL_BADGE[agent.model]}`}
            >
              {agent.model}
            </span>
          </div>
          <p className="text-xs text-white/60">{agent.role}</p>
        </div>
        <span className="text-[10px] text-white/20 shrink-0 mt-0.5">
          {formatRelativeTime(agent.last_active)}
        </span>
      </div>
      <p className="text-[11px] text-white/35 leading-relaxed line-clamp-2">{agent.lean}</p>
    </button>
  )
}

export default function TeamPage() {
  const [agents, setAgents] = useState<AgentProfile[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedAgent, setSelectedAgent] = useState<AgentProfile | null>(null)

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
        <div className="text-white/30 text-sm">Loading team...</div>
      </div>
    )
  }

  if (error && agents.length === 0) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error}</p>
        <button
          onClick={fetchTeam}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-white/60 text-sm active:text-white transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  const grouped = TIER_ORDER.map((tier) => ({
    tier,
    label: TIER_LABELS[tier],
    agents: agents.filter((a) => a.tier === tier),
  })).filter((g) => g.agents.length > 0)

  return (
    <>
      <div className="h-full overflow-y-auto">
        <div className="sticky top-0 bg-surface/80 backdrop-blur-sm px-4 py-3 border-b border-surface-border z-10">
          <div className="flex items-center gap-2">
            <Users size={18} className="text-chief" />
            <h1 className="text-lg font-semibold text-white">Team</h1>
            <span className="text-xs text-white/30 ml-auto">
              {agents.length} agent{agents.length !== 1 ? 's' : ''}
            </span>
            <button
              onClick={fetchTeam}
              className="w-7 h-7 flex items-center justify-center rounded-lg text-white/30 active:text-white/60 transition-colors"
            >
              <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
            </button>
          </div>
        </div>

        <div className="px-4 py-3 space-y-4">
          {grouped.map(({ tier, label, agents: tierAgents }) => (
            <div key={tier}>
              {tier !== 'chief' && (
                <p className="text-[10px] text-white/25 uppercase tracking-widest font-medium mb-2 px-1">
                  {label}
                </p>
              )}
              <div className={`grid gap-2 ${tier === 'chief' ? '' : 'grid-cols-1 sm:grid-cols-2'}`}>
                {tierAgents.map((agent) => (
                  <AgentCard
                    key={agent.name}
                    agent={agent}
                    onTap={setSelectedAgent}
                  />
                ))}
              </div>
            </div>
          ))}

          {agents.length === 0 && !loading && (
            <div className="text-center py-12 text-white/30 text-sm">
              No team members found
            </div>
          )}
        </div>
      </div>

      {selectedAgent && (
        <MemoryModal
          agent={selectedAgent}
          onClose={() => setSelectedAgent(null)}
        />
      )}
    </>
  )
}
