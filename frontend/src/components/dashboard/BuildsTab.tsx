import type { ReactNode } from 'react'
import { Shield } from 'lucide-react'
import type { Build } from '../../lib/api'

function formatDateTime(iso: string): string {
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    })
  } catch {
    return iso
  }
}

const SEVERITY_STYLES: Record<string, string> = {
  CRITICAL: 'text-rose-600',
  HIGH:     'text-accent-dark',
  MEDIUM:   'text-yellow-600',
  LOW:      'text-ink/40',
}

interface BuildsTabProps {
  builds: Build[]
}

export function BuildsTab({ builds }: BuildsTabProps): ReactNode {
  if (builds.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <div className="rounded-xl border border-dashed border-surface-border p-8">
          <Shield size={24} className="text-ink/20 mx-auto mb-3" />
          <p className="text-sm text-ink/30">No build history yet</p>
          <p className="text-xs text-ink/20 mt-1">
            Review sweep records will appear here after the first automated build review
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-5 space-y-4">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold text-ink/50 uppercase tracking-wider">
          Review Sweeps
        </h2>
        <span className="text-xs text-ink/30">{builds.length} records</span>
      </div>

      <div className="space-y-3">
        {builds.map((build) => {
          const fc = build.findings_count ?? { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 }
          const total = Object.values(fc).reduce((s, v) => s + v, 0)
          const isClean = total === 0

          return (
            <div
              key={build.id}
              className="p-4 rounded-xl bg-surface-raised border border-surface-border"
            >
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-ink/40">{formatDateTime(build.timestamp)}</span>
                {isClean ? (
                  <span className="text-xs font-semibold text-status-online">Clean ✓</span>
                ) : (
                  <span className="text-xs text-ink/40">{total} findings</span>
                )}
              </div>

              {!isClean && (
                <div className="flex items-center gap-4 mt-2">
                  {(Object.entries(fc) as [string, number][])
                    .filter(([, count]) => count > 0)
                    .map(([severity, count]) => (
                      <div key={severity} className="flex items-center gap-1">
                        <span className={`text-[11px] font-semibold ${SEVERITY_STYLES[severity] ?? 'text-ink/40'}`}>
                          {count}
                        </span>
                        <span className="text-[11px] text-ink/30">{severity}</span>
                      </div>
                    ))}
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
