import type { ReactNode } from 'react'
import { GitCommit, Calendar } from 'lucide-react'
import type { Project } from '../../lib/api'

interface TimelineItem {
  date: string
  label: string
  type: 'milestone' | 'commit'
  hash?: string
}

function formatDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    })
  } catch {
    return iso
  }
}

function formatShortDate(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
    })
  } catch {
    return iso
  }
}

// Assign stagger tier (0–3) to prevent label overlap on the timeline axis
function assignTiers(items: TimelineItem[]): number[] {
  const tiers: number[] = new Array(items.length).fill(0)
  // Simple four-tier round-robin stagger — keeps labels from overlapping
  for (let i = 0; i < items.length; i++) {
    tiers[i] = i % 4
  }
  return tiers
}

interface TimelineTabProps {
  project: Project
}

export function TimelineTab({ project }: TimelineTabProps): ReactNode {
  const milestones: TimelineItem[] = (project.milestones || []).map((m) => ({
    date: m.date,
    label: m.label,
    type: 'milestone',
  }))

  const commits: TimelineItem[] = (project.recent_activity || []).map((c) => ({
    date: c.date,
    label: c.message,
    type: 'commit',
    hash: c.hash,
  }))

  // Merge and sort chronologically
  const allItems: TimelineItem[] = [...milestones, ...commits].sort(
    (a, b) => new Date(a.date).getTime() - new Date(b.date).getTime()
  )

  const tiers = assignTiers(allItems)

  if (allItems.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <div className="rounded-xl border border-dashed border-surface-border p-8">
          <Calendar size={24} className="text-ink/20 mx-auto mb-3" />
          <p className="text-sm text-ink/30">No timeline data yet</p>
          <p className="text-xs text-ink/20 mt-1">
            Add date entries like <code className="bg-surface-border rounded px-1">2026-04-17 — Milestone name</code> to the memory file
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-5">
      <div className="flex items-center justify-between mb-4 px-1">
        <h2 className="text-xs font-semibold text-ink/50 uppercase tracking-wider">Timeline</h2>
        <span className="text-xs text-ink/30">
          {milestones.length} milestones · {commits.length} commits
        </span>
      </div>

      {/* Horizontal timeline — scrollable on mobile */}
      <div className="overflow-x-auto pb-2">
        <div
          className="relative"
          style={{ minWidth: `${Math.max(allItems.length * 120, 320)}px`, paddingBottom: '80px', paddingTop: '8px' }}
        >
          {/* Spine line */}
          <div className="absolute left-0 right-0 top-1/2 h-px bg-surface-border" style={{ top: '120px' }} />

          <div className="flex items-start gap-0">
            {allItems.map((item, idx) => {
              const tier = tiers[idx]
              const topOffset = tier * 24 // 0, 24, 48, 72px
              const isMilestone = item.type === 'milestone'

              return (
                <div
                  key={idx}
                  className="flex-1 flex flex-col items-center relative"
                  style={{ minWidth: '110px' }}
                >
                  {/* Label above the spine — staggered by tier */}
                  <div
                    className="w-full px-1 text-center"
                    style={{ paddingBottom: `${(3 - tier) * 24 + 20}px` }}
                  >
                    <p
                      className={`text-[10px] leading-tight font-medium ${
                        isMilestone ? 'text-primary' : 'text-ink/40'
                      }`}
                      style={{ maxHeight: '2.5rem', overflow: 'hidden' }}
                    >
                      {item.label.length > 30 ? item.label.slice(0, 28) + '…' : item.label}
                    </p>
                    {item.hash && (
                      <p className="text-[9px] font-mono text-ink/20 mt-0.5">{item.hash}</p>
                    )}
                  </div>

                  {/* Dot on the spine */}
                  <div
                    className={`w-3 h-3 rounded-full border-2 z-10 shrink-0 ${
                      isMilestone
                        ? 'bg-chief border-chief shadow-sm shadow-chief/50'
                        : 'bg-surface-border border-ink/20'
                    }`}
                    style={{ marginTop: `${topOffset}px` }}
                  />

                  {/* Date below the spine */}
                  <p className="text-[9px] text-ink/25 mt-2 text-center">
                    {formatShortDate(item.date)}
                  </p>
                </div>
              )
            })}
          </div>
        </div>
      </div>

      {/* Vertical list fallback for readability */}
      <div className="mt-5 space-y-2">
        <h3 className="text-xs font-semibold text-ink/40 uppercase tracking-wider mb-3">Details</h3>
        {allItems.map((item, idx) => (
          <div key={idx} className="flex items-start gap-3 py-1.5">
            {item.type === 'milestone' ? (
              <Calendar size={12} className="text-chief/60 mt-0.5 shrink-0" />
            ) : (
              <GitCommit size={12} className="text-ink/20 mt-0.5 shrink-0" />
            )}
            <div className="flex-1 min-w-0">
              <p className="text-xs text-ink/70 leading-snug">{item.label}</p>
              <p className="text-[10px] text-ink/30 mt-0.5">
                {item.hash && <span className="font-mono mr-1.5">{item.hash}</span>}
                {formatDate(item.date)}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
