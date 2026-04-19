import { ChevronRight, Bot } from 'lucide-react'
import type { AgentMemory } from '../../lib/api'

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

interface AgentsTabProps {
  agents: AgentMemory[]
  query: string
  onOpen: (agent: AgentMemory) => void
}

export default function AgentsTab({ agents, query, onOpen }: AgentsTabProps) {
  const q = query.toLowerCase()
  const filtered = agents.filter(
    (a) => !q || a.name.toLowerCase().includes(q) || a.content.toLowerCase().includes(q)
  )

  if (agents.length === 0) {
    return <div className="text-center py-10 text-white/30 text-sm">No per-agent memory</div>
  }

  if (filtered.length === 0) {
    return <div className="text-center py-10 text-white/30 text-sm">No results</div>
  }

  return (
    <div className="space-y-1.5">
      <p className="text-[11px] text-white/35 px-1 mb-1 leading-snug">
        Each builder's personal memory file. Tap to read or edit.
      </p>
      {filtered.map((agent) => (
        <button
          key={agent.name}
          onClick={() => onOpen(agent)}
          className="w-full flex items-center gap-3 p-3 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors text-left"
        >
          <div className="w-8 h-8 rounded-lg bg-chief/15 text-chief-light flex items-center justify-center shrink-0">
            <Bot size={14} />
          </div>
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium text-white truncate">{agent.name}</p>
            {agent.content ? (
              <p className="text-[11px] text-white/35 truncate">
                {agent.content.replace(/\n+/g, ' ').slice(0, 80)}
                {agent.content.length > 80 ? '…' : ''}
              </p>
            ) : (
              <p className="text-[11px] text-white/25 italic">Empty</p>
            )}
          </div>
          {agent.updated_at && (
            <span className="text-[10px] text-white/25 shrink-0">
              {formatRelativeTime(agent.updated_at)}
            </span>
          )}
          <ChevronRight size={15} className="text-white/25 shrink-0" />
        </button>
      ))}
    </div>
  )
}
