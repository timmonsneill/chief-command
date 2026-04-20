import type { ReactNode } from 'react'
import type { Project } from '../../lib/api'

const PHASE_ACCENT_COLORS = [
  'bg-emerald-500',
  'bg-chief',
  'bg-indigo-500',
  'bg-amber-500',
  'bg-purple-500',
  'bg-rose-500',
  'bg-sky-500',
  'bg-teal-500',
]

const PHASE_TEXT_COLORS = [
  'text-emerald-600',
  'text-primary',
  'text-indigo-600',
  'text-accent-dark',
  'text-purple-600',
  'text-rose-600',
  'text-sky-600',
  'text-teal-600',
]

const PHASE_BAR_COLORS = [
  'bg-gradient-to-r from-emerald-400 to-emerald-600',
  'bg-gradient-to-r from-chief to-chief-dark',
  'bg-gradient-to-r from-indigo-400 to-indigo-600',
  'bg-gradient-to-r from-amber-400 to-amber-600',
  'bg-gradient-to-r from-purple-400 to-purple-600',
  'bg-gradient-to-r from-rose-400 to-rose-600',
  'bg-gradient-to-r from-sky-400 to-sky-600',
  'bg-gradient-to-r from-teal-400 to-teal-600',
]

interface PhaseData {
  name: string
  complete: boolean
  percent: number
  total: number
  completed: number
}

interface PlanTabProps {
  project: Project
}

export function PlanTab({ project }: PlanTabProps): ReactNode {
  const phases = (project.phases || []) as unknown as PhaseData[]
  const progress = project.todo_progress ?? {
    total: project.todos?.length ?? 0,
    done: (project.todos || []).filter((t) => t.done).length,
    percent: 0,
  }

  return (
    <div className="px-4 py-5 space-y-5">
      {/* Overall progress card */}
      <div className="p-4 rounded-xl bg-surface-raised border border-surface-border">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h2 className="text-sm font-semibold text-ink font-display">{project.name}</h2>
            {project.description && (
              <p className="text-xs text-ink/50 mt-0.5 leading-relaxed max-w-prose">
                {project.description}
              </p>
            )}
          </div>
          <span className="text-2xl font-bold text-chief font-display tabular-nums">
            {progress.percent}%
          </span>
        </div>
        <div className="h-2.5 bg-surface-border rounded-full overflow-hidden">
          <div
            className={`h-full rounded-full transition-all duration-500 ${
              progress.percent === 100
                ? 'bg-gradient-to-r from-emerald-400 to-emerald-600'
                : 'bg-gradient-to-r from-chief to-chief-dark'
            }`}
            style={{ width: `${progress.percent}%` }}
          />
        </div>
        <div className="flex items-center justify-between mt-2">
          <span className="text-[11px] text-ink/30">
            {phases.length} phases · {progress.done}/{progress.total} tasks
          </span>
          <span className="text-[11px] text-ink/40">
            {progress.percent === 100 ? 'Complete' : 'In progress'}
          </span>
        </div>
      </div>

      {/* Phase cards */}
      {phases.length > 0 ? (
        <div className="space-y-3">
          {phases.map((phase, idx) => {
            const accentBg = PHASE_ACCENT_COLORS[idx % PHASE_ACCENT_COLORS.length]
            const textColor = PHASE_TEXT_COLORS[idx % PHASE_TEXT_COLORS.length]
            const barColor = PHASE_BAR_COLORS[idx % PHASE_BAR_COLORS.length]
            const isComplete = phase.complete || phase.percent === 100
            const isCurrent = !isComplete && phase.percent > 0

            return (
              <div
                key={phase.name}
                className={`rounded-xl border border-surface-border overflow-hidden ${
                  isCurrent ? 'bg-surface-raised ring-1 ring-chief/20' : 'bg-surface-raised'
                }`}
              >
                {/* left accent bar */}
                <div className="flex">
                  <div className={`w-1 shrink-0 ${isComplete ? 'bg-emerald-500' : isCurrent ? accentBg : 'bg-surface-border'}`} />
                  <div className="flex-1 p-4">
                    <div className="flex items-start justify-between gap-3 mb-3">
                      <div className="flex items-center gap-2.5">
                        <div
                          className={`w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold text-ink shrink-0 ${
                            isComplete ? 'bg-emerald-600' : isCurrent ? accentBg : 'bg-surface-border'
                          }`}
                        >
                          {idx + 1}
                        </div>
                        <div>
                          <span className="text-sm font-semibold text-ink/90 font-display leading-tight">
                            {phase.name}
                          </span>
                          <div className="flex items-center gap-1.5 mt-0.5">
                            {isComplete && (
                              <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 border border-emerald-500/20">
                                Done ✓
                              </span>
                            )}
                            {isCurrent && !isComplete && (
                              <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-chief/10 text-primary border border-chief/20">
                                Now
                              </span>
                            )}
                            {!isComplete && !isCurrent && (
                              <span className="text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full bg-surface-border text-ink/30">
                                Todo
                              </span>
                            )}
                          </div>
                        </div>
                      </div>
                      <span className={`text-xl font-bold tabular-nums shrink-0 ${isComplete ? 'text-emerald-600' : textColor}`}>
                        {phase.percent}%
                      </span>
                    </div>

                    {/* Progress bar */}
                    <div className="h-2 bg-surface-border rounded-full overflow-hidden mb-2">
                      <div
                        className={`h-full rounded-full transition-all duration-500 ${
                          isComplete
                            ? 'bg-gradient-to-r from-emerald-400 to-emerald-600'
                            : isCurrent
                            ? barColor
                            : 'bg-surface-border'
                        }`}
                        style={{ width: `${phase.percent}%` }}
                      />
                    </div>

                    <div className="text-[11px] text-ink/30">
                      {phase.completed}/{phase.total} tasks complete
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-surface-border p-8 text-center">
          <p className="text-sm text-ink/30">No phases defined yet</p>
          <p className="text-xs text-ink/20 mt-1">Add phase headers to the project memory file</p>
        </div>
      )}
    </div>
  )
}
