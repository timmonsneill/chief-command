import { ChevronRight, ArrowLeft, FolderOpen, Folder, FileText } from 'lucide-react'
import type { MemoryEntry, ProjectMemory } from '../../lib/api'

interface ProjectsTabProps {
  projects: ProjectMemory[]
  query: string
  selectedProject: string | null
  onSelectProject: (project: string | null) => void
  onOpenFile: (project: ProjectMemory, entry: MemoryEntry) => void
}

function filterEntries(entries: MemoryEntry[], q: string): MemoryEntry[] {
  if (!q) return entries
  const query = q.toLowerCase()
  return entries.filter(
    (e) =>
      e.title.toLowerCase().includes(query) ||
      e.description?.toLowerCase().includes(query) ||
      e.content.toLowerCase().includes(query)
  )
}

export default function ProjectsTab({
  projects,
  query,
  selectedProject,
  onSelectProject,
  onOpenFile,
}: ProjectsTabProps) {
  if (selectedProject) {
    const pm = projects.find((p) => p.project === selectedProject)
    if (!pm) {
      return (
        <div className="text-center py-10 text-white/30 text-sm">
          Project not found.
          <button
            onClick={() => onSelectProject(null)}
            className="block mx-auto mt-2 text-chief-light underline"
          >
            Back to projects
          </button>
        </div>
      )
    }
    return <ProjectFileList pm={pm} query={query} onBack={() => onSelectProject(null)} onOpen={(entry) => onOpenFile(pm, entry)} />
  }

  // Top level: project cards. When a query is active, show match counts so
  // owner knows which project to drill into.
  if (projects.length === 0) {
    return (
      <div className="text-center py-10 text-white/30 text-sm">
        No project memory entries
      </div>
    )
  }

  return (
    <div className="space-y-2">
      <p className="text-[11px] text-white/35 px-1 mb-1 leading-snug">
        All projects. Tap to see each project's memory files separately.
      </p>
      {projects.map((pm) => {
        const matching = filterEntries(pm.entries, query)
        const hideForSearch = query && matching.length === 0
        if (hideForSearch) return null
        return (
          <button
            key={pm.project}
            onClick={() => onSelectProject(pm.project)}
            className="w-full flex items-center gap-3 p-4 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors text-left"
          >
            <div
              className={`w-9 h-9 flex items-center justify-center rounded-lg shrink-0 ${
                pm.status === 'active'
                  ? 'bg-chief/15 text-chief-light'
                  : 'bg-surface-overlay text-white/40'
              }`}
            >
              <Folder size={18} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <p className="text-sm font-semibold text-white truncate">{pm.project}</p>
                <div
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    pm.status === 'active' ? 'bg-status-online' : 'bg-white/20'
                  }`}
                  aria-label={pm.status}
                />
              </div>
              <p className="text-[11px] text-white/40 mt-0.5">
                {query ? (
                  <>
                    {matching.length} of {pm.entries.length} file
                    {pm.entries.length === 1 ? '' : 's'} match
                  </>
                ) : (
                  <>
                    {pm.entries.length} file{pm.entries.length === 1 ? '' : 's'}
                    {pm.status === 'done' && ' · archived'}
                  </>
                )}
              </p>
            </div>
            <ChevronRight size={16} className="text-white/30 shrink-0" />
          </button>
        )
      })}
    </div>
  )
}

function ProjectFileList({
  pm,
  query,
  onBack,
  onOpen,
}: {
  pm: ProjectMemory
  query: string
  onBack: () => void
  onOpen: (entry: MemoryEntry) => void
}) {
  const filtered = filterEntries(pm.entries, query)

  return (
    <div className="space-y-3">
      <button
        onClick={onBack}
        className="flex items-center gap-1.5 text-xs text-white/50 active:text-white transition-colors"
      >
        <ArrowLeft size={13} />
        All projects
      </button>

      <div className="flex items-center gap-2 px-1">
        <FolderOpen size={15} className="text-chief-light" />
        <h2 className="text-sm font-semibold text-white">{pm.project}</h2>
        <div
          className={`w-1.5 h-1.5 rounded-full ${
            pm.status === 'active' ? 'bg-status-online' : 'bg-white/20'
          }`}
        />
        <span className="ml-auto text-[10px] text-white/30">
          {pm.entries.length} file{pm.entries.length === 1 ? '' : 's'}
        </span>
      </div>

      {filtered.length === 0 ? (
        <div className="text-center py-10 text-white/30 text-sm">
          {query ? 'No files match search' : 'No files in this project'}
        </div>
      ) : (
        <div className="space-y-1.5">
          {filtered.map((entry) => (
            <button
              key={entry.filename}
              onClick={() => onOpen(entry)}
              className="w-full flex items-start gap-3 p-3 rounded-xl bg-surface-raised border border-surface-border active:bg-surface-overlay transition-colors text-left"
            >
              <FileText size={14} className="text-white/35 shrink-0 mt-0.5" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-white truncate">{entry.title}</p>
                {entry.description && (
                  <p className="text-xs text-white/40 mt-0.5 leading-snug line-clamp-2">
                    {entry.description}
                  </p>
                )}
              </div>
              <ChevronRight size={15} className="text-white/25 shrink-0 mt-0.5" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
