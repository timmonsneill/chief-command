import { useState, useEffect, useCallback } from 'react'
import { BookOpen, RefreshCw, Search } from 'lucide-react'
import {
  api,
  type MemoryEntry,
  type ProjectMemory,
  type AgentMemory,
  type AuditEntry,
} from '../lib/api'
import GlobalTab from './memory/GlobalTab'
import ProjectsTab from './memory/ProjectsTab'
import AgentsTab from './memory/AgentsTab'
import AuditTab from './memory/AuditTab'
import FileReader from './memory/FileReader'

type TabId = 'global' | 'per_project' | 'per_agent' | 'audit'

const TABS: { id: TabId; label: string }[] = [
  { id: 'global', label: 'Global' },
  { id: 'per_project', label: 'Projects' },
  { id: 'per_agent', label: 'Agents' },
  { id: 'audit', label: 'Audit' },
]

interface MemoryData {
  global: MemoryEntry[]
  per_project: ProjectMemory[]
  per_agent: AgentMemory[]
  audit_log: AuditEntry[]
}

/**
 * Drill-down state is per-tab so switching tabs doesn't clobber in-flight
 * navigation, and returning to a tab later leaves the user where they were.
 */
interface DrillState {
  global: MemoryEntry | null
  projectsSelected: string | null
  projectsFile: { project: string; filename: string } | null
  agentOpen: string | null
  auditIndex: number | null
}

const EMPTY_DRILL: DrillState = {
  global: null,
  projectsSelected: null,
  projectsFile: null,
  agentOpen: null,
  auditIndex: null,
}

export default function MemoryPage() {
  const [activeTab, setActiveTab] = useState<TabId>('global')
  const [data, setData] = useState<MemoryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const [drill, setDrill] = useState<DrillState>(EMPTY_DRILL)

  const fetchMemory = useCallback(async () => {
    setError('')
    try {
      const result = await api.memory.getAll()
      setData(result)
    } catch {
      setError('Failed to load memory')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchMemory()
  }, [fetchMemory])

  const handleSaveEntry = useCallback(
    async (filename: string, content: string) => {
      await api.memory.update(filename, content)
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          global: prev.global.map((e) =>
            e.filename === filename ? { ...e, content } : e
          ),
          per_project: prev.per_project.map((pm) => ({
            ...pm,
            entries: pm.entries.map((e) =>
              e.filename === filename ? { ...e, content } : e
            ),
          })),
        }
      })
    },
    []
  )

  const handleSaveAgentMemory = useCallback(
    async (name: string, content: string) => {
      await api.team.updateMemory(name, content)
      setData((prev) => {
        if (!prev) return prev
        return {
          ...prev,
          per_agent: prev.per_agent.map((a) =>
            a.name === name ? { ...a, content } : a
          ),
        }
      })
    },
    []
  )

  if (loading && !data) {
    return (
      <div className="h-full flex items-center justify-center">
        <div className="text-white/30 text-sm">Loading memory...</div>
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="h-full flex flex-col items-center justify-center gap-3">
        <p className="text-white/40 text-sm">{error}</p>
        <button
          onClick={fetchMemory}
          className="flex items-center gap-2 px-4 py-2 rounded-lg bg-surface-raised text-white/60 text-sm active:text-white transition-colors"
        >
          <RefreshCw size={14} />
          Retry
        </button>
      </div>
    )
  }

  // Are we currently in any drill-down view? If so we show the FileReader
  // full-panel and skip tab/search chrome.
  const drilldown = resolveDrilldown(drill, data)

  if (drilldown) {
    return (
      <div className="h-full flex flex-col overflow-hidden">
        <FileReader
          title={drilldown.title}
          subtitle={drilldown.subtitle}
          filename={drilldown.filename}
          initialContent={drilldown.content}
          updatedAt={drilldown.updatedAt}
          onBack={drilldown.onBack}
          onSave={drilldown.onSave}
        />
      </div>
    )
  }

  // Audit detail lives inside the audit tab component because it renders its
  // own back/detail chrome and doesn't need the FileReader.
  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Sticky header */}
      <div className="sticky top-0 bg-surface/80 backdrop-blur-sm border-b border-surface-border z-10">
        <div className="flex items-center gap-2 px-4 py-3">
          <BookOpen size={18} className="text-chief" />
          <h1 className="text-lg font-semibold text-white">Memory</h1>
          <button
            onClick={fetchMemory}
            className="w-7 h-7 ml-auto flex items-center justify-center rounded-lg text-white/30 active:text-white/60 transition-colors"
            aria-label="Refresh"
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 pb-2">
          <div className="relative">
            <Search
              size={13}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-white/25 pointer-events-none"
            />
            <input
              type="search"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search entries..."
              className="w-full bg-surface border border-surface-border rounded-lg pl-8 pr-3 py-1.5 text-xs text-white/70 placeholder-white/25 focus:outline-none focus:border-chief/50"
            />
          </div>
        </div>

        {/* Tab bar */}
        <div className="flex px-4 gap-1 pb-0 overflow-x-auto">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors shrink-0 ${
                activeTab === tab.id
                  ? 'text-chief border-chief'
                  : 'text-white/40 border-transparent active:text-white/60'
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Tab content */}
      <div className="flex-1 overflow-y-auto px-4 py-3">
        {activeTab === 'global' && (
          <GlobalTab
            entries={data?.global ?? []}
            query={searchQuery.trim()}
            onOpen={(entry) => setDrill((d) => ({ ...d, global: entry }))}
          />
        )}

        {activeTab === 'per_project' && (
          <ProjectsTab
            projects={data?.per_project ?? []}
            query={searchQuery.trim()}
            selectedProject={drill.projectsSelected}
            onSelectProject={(project) =>
              setDrill((d) => ({ ...d, projectsSelected: project, projectsFile: null }))
            }
            onOpenFile={(pm, entry) =>
              setDrill((d) => ({
                ...d,
                projectsSelected: pm.project,
                projectsFile: { project: pm.project, filename: entry.filename },
              }))
            }
          />
        )}

        {activeTab === 'per_agent' && (
          <AgentsTab
            agents={data?.per_agent ?? []}
            query={searchQuery.trim()}
            onOpen={(agent) => setDrill((d) => ({ ...d, agentOpen: agent.name }))}
          />
        )}

        {activeTab === 'audit' && (
          <AuditTab
            entries={data?.audit_log ?? []}
            query={searchQuery.trim()}
            selectedIndex={drill.auditIndex}
            onSelect={(idx) => setDrill((d) => ({ ...d, auditIndex: idx }))}
          />
        )}
      </div>
    </div>
  )

  /**
   * Resolve the active drill-down across tabs into a FileReader descriptor,
   * or null if no file is currently open. Kept inside the component closure
   * so it has access to save handlers.
   */
  function resolveDrilldown(
    state: DrillState,
    loaded: MemoryData | null
  ):
    | {
        title: string
        subtitle?: string
        filename: string
        content: string
        updatedAt?: string | null
        onBack: () => void
        onSave: (filename: string, content: string) => Promise<void>
      }
    | null {
    if (!loaded) return null

    if (activeTab === 'global' && state.global) {
      // Re-lookup latest entry so saves reflect immediately.
      const fresh = loaded.global.find((e) => e.filename === state.global!.filename)
      const entry = fresh ?? state.global
      return {
        title: entry.title,
        subtitle: entry.description || entry.filename,
        filename: entry.filename,
        content: entry.content,
        updatedAt: entry.updated_at,
        onBack: () => setDrill((d) => ({ ...d, global: null })),
        onSave: handleSaveEntry,
      }
    }

    if (activeTab === 'per_project' && state.projectsFile) {
      const pm = loaded.per_project.find((p) => p.project === state.projectsFile!.project)
      const entry = pm?.entries.find((e) => e.filename === state.projectsFile!.filename)
      if (!pm || !entry) {
        return null
      }
      return {
        title: entry.title,
        subtitle: `${pm.project} · ${entry.filename}`,
        filename: entry.filename,
        content: entry.content,
        updatedAt: entry.updated_at,
        onBack: () =>
          setDrill((d) => ({ ...d, projectsFile: null })),
        onSave: handleSaveEntry,
      }
    }

    if (activeTab === 'per_agent' && state.agentOpen) {
      const agent = loaded.per_agent.find((a) => a.name === state.agentOpen)
      if (!agent) return null
      return {
        title: agent.name,
        subtitle: 'Agent memory',
        filename: agent.name,
        content: agent.content,
        updatedAt: agent.updated_at,
        onBack: () => setDrill((d) => ({ ...d, agentOpen: null })),
        onSave: handleSaveAgentMemory,
      }
    }

    return null
  }
}
