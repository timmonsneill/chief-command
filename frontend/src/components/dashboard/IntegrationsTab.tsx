import type { ReactNode } from 'react'
import { Puzzle } from 'lucide-react'

// Known integrations hardcoded per-project.
// Future task: move to backend PROJECTS.json config.
const KNOWN_INTEGRATIONS: Record<string, Integration[]> = {
  'chief-command': [
    { name: 'Anthropic API', description: 'Claude models — Haiku, Sonnet, Opus', status: 'active' },
    { name: 'Cloudflare Tunnel', description: 'Secure public HTTPS tunnel to local backend', status: 'active' },
    { name: 'Playwright', description: 'Browser automation for E2E testing', status: 'active' },
    { name: 'slowapi', description: 'FastAPI rate limiting middleware', status: 'active' },
    { name: 'DiceBear', description: 'Avatar generation for agent profiles', status: 'active' },
    { name: 'Space Grotesk', description: 'Display font — Google Fonts CDN', status: 'active' },
  ],
}

interface Integration {
  name: string
  description: string
  status: 'active' | 'planned' | 'deprecated'
}

const STATUS_STYLES: Record<Integration['status'], { pill: string; dot: string }> = {
  active:     { pill: 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20', dot: 'bg-emerald-500' },
  planned:    { pill: 'bg-chief/10 text-chief-light border border-chief/20', dot: 'bg-chief' },
  deprecated: { pill: 'bg-white/5 text-white/30 border border-surface-border', dot: 'bg-white/20' },
}

interface IntegrationsTabProps {
  projectSlug: string
}

export function IntegrationsTab({ projectSlug }: IntegrationsTabProps): ReactNode {
  const integrations = KNOWN_INTEGRATIONS[projectSlug] ?? []

  if (integrations.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <div className="rounded-xl border border-dashed border-surface-border p-8">
          <Puzzle size={24} className="text-white/20 mx-auto mb-3" />
          <p className="text-sm text-white/30">No integrations configured</p>
          <p className="text-xs text-white/20 mt-1">
            Add an <code className="bg-surface-border rounded px-1 text-white/30">integrations</code> key to PROJECTS.json
          </p>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-5 space-y-4">
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold text-white/50 uppercase tracking-wider">
          External Services
        </h2>
        <span className="text-xs text-white/30">{integrations.length} configured</span>
      </div>

      <div className="rounded-xl bg-surface-raised border border-surface-border overflow-hidden">
        <div className="divide-y divide-surface-border">
          {integrations.map((integration) => {
            const styles = STATUS_STYLES[integration.status]
            return (
              <div key={integration.name} className="flex items-center gap-4 px-4 py-3.5">
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${styles.dot}`} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium text-white/80">{integration.name}</p>
                  <p className="text-xs text-white/35 mt-0.5 truncate">{integration.description}</p>
                </div>
                <span className={`shrink-0 text-[10px] font-bold uppercase tracking-wide px-2 py-0.5 rounded-full ${styles.pill}`}>
                  {integration.status}
                </span>
              </div>
            )
          })}
        </div>
      </div>

      <p className="text-[11px] text-white/20 px-1">
        This list is hardcoded for now — future task will move it to PROJECTS.json config.
      </p>
    </div>
  )
}
