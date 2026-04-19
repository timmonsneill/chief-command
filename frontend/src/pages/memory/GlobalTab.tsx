import { ChevronRight, Gavel, User, FolderKanban, BookMarked } from 'lucide-react'
import type { MemoryEntry } from '../../lib/api'

interface GlobalTabProps {
  entries: MemoryEntry[]
  query: string
  onOpen: (entry: MemoryEntry) => void
}

type SectionKey = 'rules' | 'profile' | 'project' | 'reference'

interface Section {
  key: SectionKey
  label: string
  tagline: string
  icon: typeof Gavel
  match: (entry: MemoryEntry) => boolean
}

// Order matters — owner wants orchestration rules / feedback prominent.
const SECTIONS: Section[] = [
  {
    key: 'rules',
    label: 'House Rules & Feedback',
    tagline: 'Agent orchestration rules and recurring corrections',
    icon: Gavel,
    match: (e) => e.type === 'feedback',
  },
  {
    key: 'profile',
    label: 'Owner Profile',
    tagline: 'Who Neill is and how he likes to work',
    icon: User,
    match: (e) => e.type === 'user',
  },
  {
    key: 'project',
    label: 'Project Notes',
    tagline: 'Cross-project context worth keeping global',
    icon: FolderKanban,
    match: (e) => e.type === 'project',
  },
  {
    key: 'reference',
    label: 'References',
    tagline: 'Docs, TODOs, long-lived reference material',
    icon: BookMarked,
    match: (e) => e.type === 'reference',
  },
]

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

export default function GlobalTab({ entries, query, onOpen }: GlobalTabProps) {
  const filtered = filterEntries(entries, query)

  // Bucket by known types; anything unrecognized goes to a trailing "Other" bucket.
  const buckets: Record<SectionKey, MemoryEntry[]> = {
    rules: [],
    profile: [],
    project: [],
    reference: [],
  }
  const other: MemoryEntry[] = []

  for (const entry of filtered) {
    const section = SECTIONS.find((s) => s.match(entry))
    if (section) buckets[section.key].push(entry)
    else other.push(entry)
  }

  if (filtered.length === 0) {
    return (
      <div className="text-center py-10 text-white/30 text-sm">
        {query ? 'No results' : 'No global memory entries'}
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {SECTIONS.map((section) => {
        const items = buckets[section.key]
        if (items.length === 0) return null
        const Icon = section.icon
        return (
          <section key={section.key}>
            <div className="flex items-center gap-2 mb-2 px-1">
              <Icon size={13} className="text-chief-light" />
              <h2 className="text-[11px] font-semibold uppercase tracking-widest text-white/60">
                {section.label}
              </h2>
              <span className="text-[10px] text-white/25 ml-auto">
                {items.length}
              </span>
            </div>
            <p className="text-[11px] text-white/30 px-1 mb-2 leading-snug">
              {section.tagline}
            </p>
            <div className="space-y-1.5">
              {items.map((entry) => (
                <EntryRow key={entry.filename} entry={entry} onOpen={onOpen} />
              ))}
            </div>
          </section>
        )
      })}

      {other.length > 0 && (
        <section>
          <div className="flex items-center gap-2 mb-2 px-1">
            <h2 className="text-[11px] font-semibold uppercase tracking-widest text-white/60">
              Other
            </h2>
            <span className="text-[10px] text-white/25 ml-auto">{other.length}</span>
          </div>
          <div className="space-y-1.5">
            {other.map((entry) => (
              <EntryRow key={entry.filename} entry={entry} onOpen={onOpen} />
            ))}
          </div>
        </section>
      )}
    </div>
  )
}

function EntryRow({
  entry,
  onOpen,
}: {
  entry: MemoryEntry
  onOpen: (entry: MemoryEntry) => void
}) {
  return (
    <button
      onClick={() => onOpen(entry)}
      className="w-full flex items-start gap-3 p-3 rounded-xl bg-surface-raised border border-surface-border text-left active:bg-surface-overlay transition-colors"
    >
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
  )
}
