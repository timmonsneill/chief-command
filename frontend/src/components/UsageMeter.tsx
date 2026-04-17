import { useEffect, useRef, useState } from 'react'
import type { ActiveModel } from '../lib/api'

interface UsageMeterProps {
  sessionId: string | null
  inputTokens: number
  outputTokens: number
  cachedTokens: number
  costCents: number
  model: ActiveModel | null
}

const MODEL_LABELS: Record<ActiveModel, string> = {
  'claude-haiku-4-5': 'Haiku',
  'claude-sonnet-4-6': 'Sonnet',
  'claude-opus-4-7': 'Opus',
}

const MODEL_COLORS: Record<ActiveModel, string> = {
  'claude-haiku-4-5': 'bg-emerald-500/20 text-emerald-400 border-emerald-500/30',
  'claude-sonnet-4-6': 'bg-blue-500/20 text-blue-400 border-blue-500/30',
  'claude-opus-4-7': 'bg-purple-500/20 text-purple-400 border-purple-500/30',
}

function AnimatedCost({ cents }: { cents: number }) {
  const [displayed, setDisplayed] = useState(cents)
  const rafRef = useRef<number | null>(null)
  const startRef = useRef({ from: cents, to: cents, startTime: 0 })

  useEffect(() => {
    const from = displayed
    const to = cents
    if (from === to) return

    startRef.current = { from, to, startTime: performance.now() }
    const duration = 400

    function animate(now: number) {
      const elapsed = now - startRef.current.startTime
      const t = Math.min(elapsed / duration, 1)
      const ease = 1 - Math.pow(1 - t, 3)
      setDisplayed(startRef.current.from + (startRef.current.to - startRef.current.from) * ease)
      if (t < 1) {
        rafRef.current = requestAnimationFrame(animate)
      }
    }

    if (rafRef.current) cancelAnimationFrame(rafRef.current)
    rafRef.current = requestAnimationFrame(animate)

    return () => {
      if (rafRef.current) cancelAnimationFrame(rafRef.current)
    }
  }, [cents])

  const dollars = displayed / 100
  if (dollars < 0.01) return <span>${(displayed / 100).toFixed(4)}</span>
  return <span>${dollars.toFixed(4)}</span>
}

export function UsageMeter({ sessionId, inputTokens, outputTokens, cachedTokens, costCents, model }: UsageMeterProps) {
  if (!sessionId) return null

  const totalTokens = inputTokens + outputTokens

  return (
    <div className="rounded-xl bg-surface-raised border border-surface-border p-4 space-y-3">
      <div className="flex items-center justify-between">
        <span className="text-white/40 text-xs uppercase tracking-wider">Session Cost</span>
        {model && (
          <span className={`text-[10px] font-medium px-2 py-0.5 rounded-full border ${MODEL_COLORS[model]}`}>
            {MODEL_LABELS[model]}
          </span>
        )}
      </div>

      <div className="text-3xl font-semibold text-white tabular-nums">
        <AnimatedCost cents={costCents} />
      </div>

      <div className="grid grid-cols-3 gap-2 text-center">
        <div className="bg-surface-overlay rounded-lg px-2 py-1.5">
          <div className="text-white/80 text-sm font-medium tabular-nums">{inputTokens.toLocaleString()}</div>
          <div className="text-white/30 text-[10px]">Input</div>
        </div>
        <div className="bg-surface-overlay rounded-lg px-2 py-1.5">
          <div className="text-white/80 text-sm font-medium tabular-nums">{outputTokens.toLocaleString()}</div>
          <div className="text-white/30 text-[10px]">Output</div>
        </div>
        <div className="bg-surface-overlay rounded-lg px-2 py-1.5">
          <div className="text-white/80 text-sm font-medium tabular-nums">{cachedTokens.toLocaleString()}</div>
          <div className="text-white/30 text-[10px]">Cached</div>
        </div>
      </div>

      {totalTokens > 0 && (
        <div className="text-white/30 text-[10px] text-right">
          {totalTokens.toLocaleString()} tokens total
        </div>
      )}
    </div>
  )
}
