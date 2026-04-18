import { useState, useEffect, useCallback } from 'react'
import { BookOpen, RefreshCw, ChevronDown, ChevronRight, Save, Search } from 'lucide-react'
import { api, type MemoryEntry, type ProjectMemory, type AgentMemory, type AuditEntry } from '../lib/api'
import { useProjectContext } from '../hooks/useProjectContext'

type TabId = 'global' | 'per_project' | 'per_agent' | 'audit'

const TABS: { id: TabId; label: string }[] = [
  { id: 'global', label: 'Global' },
  { id: 'per_project', label: 'Per-project' },
  { id: 'per_agent', label: 'Per-agent' },
  { id: 'audit', label: 'Audit log' },
]

const AUDIT_ACTION_COLORS: Record<string, string> = {
  removed: 'text-status-offline',
  updated: 'text-chief-light',
  promoted: 'text-status-online',
  demoted: 'text-status-working',
  created: 'text-status-online',
}

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

interface EntryEditorProps {
  title: string
  description?: string
  content: string
  updatedAt?: string | null
  filename: string
  onSave: (filename: string, content: string) => Promise<void>
}

function EntryEditor({ title, description, content: initialContent, updatedAt, filename, onSave }: EntryEditorProps) {
  const [expanded, setExpanded] = useState(false)
  const [content, setContent] = useState(initialContent)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saveSuccess, setSaveSuccess] = useState(false)

  async function handleSave() {
    setSaving(true)
    setSaveError('')
    setSaveSuccess(false)
    try {
      await onSave(filename, content)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 2000)
    } catch {
      setSaveError('Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl bg-surface-raised border border-surface-border overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-start gap-3 p-3 text-left active:bg-surface-overlay transition-colors"
      >
        <div className="mt-0.5 text-white/30 shrink-0">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white truncate">{title}</p>
          {description && (
            <p className="text-xs text-white/40 mt-0.5 leading-snug line-clamp-1">{description}</p>
          )}
        </div>
        {updatedAt && (
          <span className="text-[10px] text-white/20 shrink-0">{formatRelativeTime(updatedAt)}</span>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-surface-border pt-3">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full h-48 bg-surface border border-surface-border rounded-lg p-3 text-xs text-white/70 font-mono leading-relaxed resize-y focus:outline-none focus:border-chief/50 placeholder-white/20"
            spellCheck={false}
            placeholder="Empty"
          />
          {saveError && <p className="text-xs text-status-offline">{saveError}</p>}
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center justify-center gap-1.5 w-full py-2 rounded-lg bg-chief text-white text-xs font-medium transition-opacity disabled:opacity-40 active:opacity-80"
          >
            <Save size={12} />
            {saving ? 'Saving...' : saveSuccess ? 'Saved!' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}

interface AgentEntryEditorProps {
  agent: AgentMemory
  onSave: (name: string, content: string) => Promise<void>
}

function AgentEntryEditor({ agent, onSave }: AgentEntryEditorProps) {
  const [expanded, setExpanded] = useState(false)
  const [content, setContent] = useState(agent.content)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState('')
  const [saveSuccess, setSaveSuccess] = useState(false)

  async function handleSave() {
    setSaving(true)
    setSaveError('')
    setSaveSuccess(false)
    try {
      await onSave(agent.name, content)
      setSaveSuccess(true)
      setTimeout(() => setSaveSuccess(false), 2000)
    } catch {
      setSaveError('Save failed')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="rounded-xl bg-surface-raised border border-surface-border overflow-hidden">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-3 p-3 text-left active:bg-surface-overlay transition-colors"
      >
        <div className="text-white/30 shrink-0">
          {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </div>
        <span className="text-sm font-medium text-white flex-1">{agent.name}</span>
        {agent.updated_at && (
          <span className="text-[10px] text-white/20 shrink-0">{formatRelativeTime(agent.updated_at)}</span>
        )}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-2 border-t border-surface-border pt-3">
          <textarea
            value={content}
            onChange={(e) => setContent(e.target.value)}
            className="w-full h-48 bg-surface border border-surface-border rounded-lg p-3 text-xs text-white/70 font-mono leading-relaxed resize-y focus:outline-none focus:border-chief/50 placeholder-white/20"
            spellCheck={false}
            placeholder={`No memory yet for ${agent.name}`}
          />
          {saveError && <p className="text-xs text-status-offline">{saveError}</p>}
          <button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center justify-center gap-1.5 w-full py-2 rounded-lg bg-chief text-white text-xs font-medium transition-opacity disabled:opacity-40 active:opacity-80"
          >
            <Save size={12} />
            {saving ? 'Saving...' : saveSuccess ? 'Saved!' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}

interface MemoryData {
  global: MemoryEntry[]
  per_project: ProjectMemory[]
  per_agent: AgentMemory[]
  audit_log: AuditEntry[]
}

export default function MemoryPage() {
  const [activeTab, setActiveTab] = useState<TabId>('global')
  const [data, setData] = useState<MemoryData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchQuery, setSearchQuery] = useState('')
  const { current: currentProject } = useProjectContext()

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

  async function handleSaveEntry(filename: string, content: string) {
    await api.memory.update(filename, content)
    // Optimistic update in local state
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
  }

  async function handleSaveAgentMemory(name: string, content: string) {
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
  }

  const q = searchQuery.trim().toLowerCase()

  function filterEntries(entries: MemoryEntry[]): MemoryEntry[] {
    if (!q) return entries
    return entries.filter(
      (e) =>
        e.title.toLowerCase().includes(q) ||
        e.description?.toLowerCase().includes(q) ||
        e.content.toLowerCase().includes(q)
    )
  }

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
          >
            <RefreshCw size={13} className={loading ? 'animate-spin' : ''} />
          </button>
        </div>

        {/* Search */}
        <div className="px-4 pb-2">
          <div className="relative">
            <Search size={13} className="absolute left-3 top-1/2 -translate-y-1/2 text-white/25 pointer-events-none" />
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
        <div className="flex px-4 gap-1 pb-0">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
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
          <div className="space-y-2">
            {filterEntries(data?.global ?? []).map((entry) => (
              <EntryEditor
                key={entry.filename}
                title={entry.title}
                description={entry.description}
                content={entry.content}
                updatedAt={entry.updated_at}
                filename={entry.filename}
                onSave={handleSaveEntry}
              />
            ))}
            {filterEntries(data?.global ?? []).length === 0 && (
              <div className="text-center py-10 text-white/30 text-sm">
                {q ? 'No results' : 'No global memory entries'}
              </div>
            )}
          </div>
        )}

        {activeTab === 'per_project' && (
          <div className="space-y-4">
            {(data?.per_project ?? [])
              .filter((pm) => currentProject === 'All' || pm.project === currentProject)
              .map((pm) => {
              const filtered = filterEntries(pm.entries)
              if (q && filtered.length === 0) return null
              return (
                <div key={pm.project}>
                  <div className="flex items-center gap-2 mb-2">
                    <p className="text-[10px] text-white/25 uppercase tracking-widest font-medium">
                      {pm.project}
                    </p>
                    <div
                      className={`w-1.5 h-1.5 rounded-full ${
                        pm.status === 'active' ? 'bg-status-online' : 'bg-white/20'
                      }`}
                    />
                  </div>
                  <div className="space-y-2">
                    {filtered.map((entry) => (
                      <EntryEditor
                        key={entry.filename}
                        title={entry.title}
                        description={entry.description}
                        content={entry.content}
                        updatedAt={entry.updated_at}
                        filename={entry.filename}
                        onSave={handleSaveEntry}
                      />
                    ))}
                    {filtered.length === 0 && !q && (
                      <p className="text-xs text-white/20 px-2">No entries</p>
                    )}
                  </div>
                </div>
              )
            })}
            {(data?.per_project ?? []).length === 0 && (
              <div className="text-center py-10 text-white/30 text-sm">
                No project memory entries
              </div>
            )}
          </div>
        )}

        {activeTab === 'per_agent' && (
          <div className="space-y-2">
            {(data?.per_agent ?? [])
              .filter((a) => !q || a.name.toLowerCase().includes(q) || a.content.toLowerCase().includes(q))
              .map((agent) => (
                <AgentEntryEditor
                  key={agent.name}
                  agent={agent}
                  onSave={handleSaveAgentMemory}
                />
              ))}
            {(data?.per_agent ?? []).length === 0 && (
              <div className="text-center py-10 text-white/30 text-sm">
                No per-agent memory
              </div>
            )}
          </div>
        )}

        {activeTab === 'audit' && (
          <div className="space-y-2">
            {(data?.audit_log ?? [])
              .filter(
                (e) =>
                  !q ||
                  e.target.toLowerCase().includes(q) ||
                  e.reason.toLowerCase().includes(q) ||
                  e.action.toLowerCase().includes(q)
              )
              .map((entry, i) => (
                <div
                  key={i}
                  className="p-3 rounded-xl bg-surface-raised border border-surface-border"
                >
                  <div className="flex items-start gap-2 justify-between">
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span
                          className={`text-[10px] font-semibold uppercase tracking-wide ${
                            AUDIT_ACTION_COLORS[entry.action] ?? 'text-white/40'
                          }`}
                        >
                          {entry.action}
                        </span>
                        <span className="text-xs text-white/60 truncate">{entry.target}</span>
                      </div>
                      {entry.reason && (
                        <p className="text-xs text-white/35 leading-snug">{entry.reason}</p>
                      )}
                    </div>
                    <span className="text-[10px] text-white/20 shrink-0">
                      {formatRelativeTime(entry.timestamp)}
                    </span>
                  </div>
                </div>
              ))}
            {(data?.audit_log ?? []).length === 0 && (
              <div className="text-center py-10 text-white/30 text-sm">
                No audit entries yet
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
