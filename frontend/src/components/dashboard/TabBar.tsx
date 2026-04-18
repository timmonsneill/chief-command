import type { ReactNode } from 'react'

export type TabId = 'plan' | 'todos' | 'timeline' | 'integrations' | 'builds'

export interface TabDef {
  id: TabId
  labelFull: string
  labelShort: string
}

export const TABS: TabDef[] = [
  { id: 'plan',         labelFull: 'Plan',         labelShort: 'Plan' },
  { id: 'todos',        labelFull: 'Master Todo',  labelShort: 'Todo' },
  { id: 'timeline',     labelFull: 'Timeline',     labelShort: 'Time' },
  { id: 'integrations', labelFull: 'Integrations', labelShort: 'Intg' },
  { id: 'builds',       labelFull: 'Builds',       labelShort: 'Blds' },
]

interface TabBarProps {
  active: TabId
  onChange: (id: TabId) => void
}

export function TabBar({ active, onChange }: TabBarProps): ReactNode {
  return (
    <div className="sticky top-0 z-20 bg-surface border-b border-surface-border shadow-sm">
      <div className="flex gap-0.5 px-2 overflow-x-auto scrollbar-none">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => onChange(tab.id)}
            className={[
              'flex-shrink-0 px-4 py-4 text-xs font-semibold whitespace-nowrap border-b-2 transition-colors duration-150',
              active === tab.id
                ? 'text-chief border-chief bg-chief/5'
                : 'text-white/40 border-transparent hover:text-chief/70 hover:bg-chief/5',
            ].join(' ')}
          >
            <span className="hidden sm:inline">{tab.labelFull}</span>
            <span className="inline sm:hidden">{tab.labelShort}</span>
          </button>
        ))}
      </div>
    </div>
  )
}
