import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { ArrowLeft, ExternalLink, RefreshCw, AlertTriangle } from 'lucide-react'
import { api, type Project } from '../lib/api'
import { TabBar, type TabId } from '../components/dashboard/TabBar'
import { PlanTab } from '../components/dashboard/PlanTab'
import { TodoTab } from '../components/dashboard/TodoTab'
import { TimelineTab } from '../components/dashboard/TimelineTab'
import { IntegrationsTab } from '../components/dashboard/IntegrationsTab'
import { BuildsTab } from '../components/dashboard/BuildsTab'

// Project type extended with optional dashboard_url not yet in api.ts types
type ProjectWithDashboardUrl = Project & { dashboard_url?: string }

const STATUS_CONFIG: Record<string, { dot: string; label: string }> = {
  active:  { dot: 'bg-status-online',  label: 'Active' },
  paused:  { dot: 'bg-status-working', label: 'Paused' },
  done:    { dot: 'bg-ink/20',       label: 'Done' },
}

// ─── Iframe view (for projects with dashboard_url) ───────────────────────────

interface IframeViewProps {
  project: ProjectWithDashboardUrl
  onBack: () => void
}

function IframeView({ project, onBack }: IframeViewProps) {
  const [iframeError, setIframeError] = useState(false)
  const [loadTimedOut, setLoadTimedOut] = useState(false)
  const iframeRef = useRef<HTMLIFrameElement>(null)
  const dashboardUrl = project.dashboard_url!
  const statusCfg = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.active

  function handleIframeError() {
    setIframeError(true)
  }

  function handleIframeLoad() {
    // Detect silent X-Frame-Options / CSP block — browser fires `load` even when
    // the page is blocked, but access to contentDocument throws cross-origin errors.
    // If the iframe appears completely empty after load, assume it was blocked.
    try {
      const doc = iframeRef.current?.contentDocument
      // Same-origin doc with empty body = blocked. Cross-origin access throws, which
      // means the frame IS loaded with content and we can't introspect it — that's fine.
      if (doc && doc.body && doc.body.children.length === 0) {
        setIframeError(true)
      }
    } catch {
      // Cross-origin access denied = frame loaded successfully with remote content.
    }
    setLoadTimedOut(false)
  }

  // Timeout-based fallback: if onLoad hasn't fired within 8s, assume block.
  useEffect(() => {
    const t = setTimeout(() => setLoadTimedOut(true), 8000)
    return () => clearTimeout(t)
  }, [dashboardUrl])

  return (
    <div className="flex flex-col h-full">
      {/* Header bar */}
      <div className="flex items-center gap-3 px-4 py-3 bg-surface/80 backdrop-blur-sm border-b border-surface-border z-10 shrink-0">
        <button
          onClick={onBack}
          className="w-9 h-9 flex items-center justify-center rounded-lg text-ink/40 hover:text-ink transition-colors"
          aria-label="Back to projects"
        >
          <ArrowLeft size={18} />
        </button>

        <div className="flex-1 min-w-0 flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full shrink-0 ${statusCfg.dot}`} />
          <h1 className="text-sm font-semibold text-ink truncate font-display">{project.name}</h1>
        </div>

        <a
          href={dashboardUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-ink/50 hover:text-ink border border-surface-border hover:border-ink/20 transition-colors"
        >
          <ExternalLink size={12} />
          <span className="hidden sm:inline">Open in new tab</span>
          <span className="inline sm:hidden">Open</span>
        </a>
      </div>

      {/* CSP error banner — fires on onError, empty-body detect, or 8s timeout */}
      {(iframeError || loadTimedOut) && (
        <div className="px-4 py-2.5 bg-status-working/10 border-b border-status-working/20 flex items-center gap-2.5 shrink-0">
          <AlertTriangle size={14} className="text-status-working shrink-0" />
          <p className="text-xs text-ink/70 flex-1">
            Dashboard blocked by browser security policy.{' '}
            <a
              href={dashboardUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="text-chief underline underline-offset-2"
            >
              Open externally
            </a>{' '}
            to view it.
          </p>
        </div>
      )}

      {/* Iframe — sandbox drops allow-same-origin (cross-origin by intent) */}
      <iframe
        ref={iframeRef}
        src={dashboardUrl}
        title={`${project.name} dashboard`}
        className="flex-1 w-full border-0"
        onError={handleIframeError}
        onLoad={handleIframeLoad}
        sandbox="allow-scripts allow-popups allow-forms"
      />
    </div>
  )
}

// ─── Native dashboard view (for projects without dashboard_url) ───────────────

interface NativeDashboardProps {
  project: ProjectWithDashboardUrl
  onBack: () => void
}

function NativeDashboard({ project, onBack }: NativeDashboardProps) {
  const [activeTab, setActiveTab] = useState<TabId>('plan')
  const [fadeKey, setFadeKey] = useState(0)
  const statusCfg = STATUS_CONFIG[project.status] ?? STATUS_CONFIG.active

  function handleTabChange(id: TabId) {
    setActiveTab(id)
    setFadeKey((k) => k + 1)
  }

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="flex items-center gap-3 px-4 py-3 bg-surface/80 backdrop-blur-sm border-b border-surface-border z-10 shrink-0">
        <button
          onClick={onBack}
          className="w-9 h-9 flex items-center justify-center rounded-lg text-ink/40 hover:text-ink transition-colors"
          aria-label="Back to projects"
        >
          <ArrowLeft size={18} />
        </button>

        <div className="flex-1 min-w-0 flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full shrink-0 ${statusCfg.dot}`} />
          <h1 className="text-sm font-semibold text-ink truncate font-display">{project.name}</h1>
          <span className="hidden sm:inline text-[11px] text-ink/30 font-medium px-1.5 py-0.5 rounded bg-surface-raised border border-surface-border">
            {statusCfg.label}
          </span>
        </div>
      </div>

      {/* Tab bar */}
      <TabBar active={activeTab} onChange={handleTabChange} />

      {/* Tab content — fade transition on switch */}
      <div className="flex-1 overflow-y-auto">
        <div
          key={fadeKey}
          style={{ animation: 'tabFade 200ms ease-out' }}
        >
          {activeTab === 'plan' && <PlanTab project={project} />}
          {activeTab === 'todos' && <TodoTab project={project} />}
          {activeTab === 'timeline' && <TimelineTab project={project} />}
          {activeTab === 'integrations' && <IntegrationsTab projectSlug={project.slug} />}
          {activeTab === 'builds' && <BuildsTab builds={project.builds || []} />}
        </div>
      </div>
    </div>
  )
}

// ─── Root page component ──────────────────────────────────────────────────────

export default function ProjectDashboard() {
  const { slug } = useParams<{ slug: string }>()
  const navigate = useNavigate()
  const [project, setProject] = useState<ProjectWithDashboardUrl | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const fetchProject = useCallback(async () => {
    if (!slug) return
    setLoading(true)
    setError('')
    try {
      const data = await api.projects.get(slug)
      setProject(data as ProjectWithDashboardUrl)
    } catch {
      setError('Failed to load project')
    } finally {
      setLoading(false)
    }
  }, [slug])

  useEffect(() => {
    fetchProject()
  }, [fetchProject])

  function handleBack() {
    navigate('/projects')
  }

  if (loading && !project) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-ink/30 text-sm">Loading project…</div>
      </div>
    )
  }

  if (error || !project) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-ink/40 text-sm">{error || 'Project not found'}</p>
        <button
          onClick={fetchProject}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-ink/60 text-sm hover:text-ink transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  // Branch: iframe for external dashboard, native for everything else.
  // Only http(s) URLs allowed — blocks javascript:/data:/file: scheme injection.
  const safeDashboardUrl =
    project.dashboard_url && /^https?:\/\//i.test(project.dashboard_url)
      ? project.dashboard_url
      : ''
  if (safeDashboardUrl) {
    return <IframeView project={{ ...project, dashboard_url: safeDashboardUrl }} onBack={handleBack} />
  }

  return <NativeDashboard project={project} onBack={handleBack} />
}
