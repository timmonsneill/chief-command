import { useNavigate } from 'react-router-dom'
import type { ActiveModel } from '../lib/api'

interface SessionBadgeProps {
  sessionId: string | null
  costCents: number
  turnCount: number
  model: ActiveModel | null
}

const MODEL_DOT: Record<ActiveModel, string> = {
  'claude-haiku-4-5': 'bg-emerald-400',
  'claude-sonnet-4-6': 'bg-blue-400',
  'claude-opus-4-7': 'bg-purple-400',
}

export function SessionBadge({ sessionId, costCents, turnCount, model }: SessionBadgeProps) {
  const navigate = useNavigate()

  if (!sessionId) return null

  const dollars = costCents / 100
  const costLabel = dollars < 0.01 ? `$${dollars.toFixed(4)}` : `$${dollars.toFixed(4)}`

  return (
    <button
      onClick={() => navigate(`/sessions/${sessionId}`)}
      className="flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-raised border border-surface-border text-xs text-ink/60 hover:text-ink/90 hover:border-ink/20 transition-colors"
    >
      {model && (
        <span className={`w-1.5 h-1.5 rounded-full ${MODEL_DOT[model]}`} />
      )}
      <span className="tabular-nums font-medium text-ink/80">{costLabel}</span>
      <span className="text-ink/30">·</span>
      <span>{turnCount} {turnCount === 1 ? 'turn' : 'turns'}</span>
    </button>
  )
}
