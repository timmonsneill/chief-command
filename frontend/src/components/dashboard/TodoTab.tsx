import type { ReactNode } from 'react'
import { CheckSquare, Square } from 'lucide-react'
import type { Project, Todo } from '../../lib/api'

interface PhaseGroup {
  name: string
  todos: Todo[]
}

interface TodoTabProps {
  project: Project
}

function groupByPhase(todos: Todo[]): PhaseGroup[] {
  const map = new Map<string, Todo[]>()
  for (const todo of todos) {
    const key = todo.category || 'General'
    const arr = map.get(key) ?? []
    arr.push(todo)
    map.set(key, arr)
  }
  return Array.from(map.entries()).map(([name, items]) => ({ name, todos: items }))
}

export function TodoTab({ project }: TodoTabProps): ReactNode {
  const todos = project.todos || []
  const groups = groupByPhase(todos)
  const totalDone = todos.filter((t) => t.done).length
  const total = todos.length

  if (todos.length === 0) {
    return (
      <div className="px-4 py-8 text-center">
        <div className="rounded-xl border border-dashed border-surface-border p-8">
          <p className="text-sm text-ink/30">No todos found</p>
          <p className="text-xs text-ink/20 mt-1">Add checkbox items to the project memory file</p>
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 py-5 space-y-4">
      {/* Summary header */}
      <div className="flex items-center justify-between px-1">
        <h2 className="text-xs font-semibold text-ink/50 uppercase tracking-wider">
          All Tasks
        </h2>
        <span className="text-xs text-ink/30 tabular-nums">
          {totalDone}/{total} done
        </span>
      </div>

      {groups.map((group) => {
        const groupDone = group.todos.filter((t) => t.done).length
        const groupTotal = group.todos.length

        return (
          <div key={group.name} className="rounded-xl bg-surface-raised border border-surface-border overflow-hidden">
            {/* Group header */}
            <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
              <span className="text-xs font-semibold text-ink/70 font-display">{group.name}</span>
              <span className="text-[11px] text-ink/30 tabular-nums">
                {groupDone}/{groupTotal}
              </span>
            </div>

            {/* Todo items */}
            <div className="divide-y divide-surface-border">
              {group.todos.map((todo, idx) => (
                <div
                  key={idx}
                  className="flex items-start gap-3 px-4 py-3"
                >
                  {todo.done ? (
                    <CheckSquare size={14} className="text-status-online mt-0.5 shrink-0" />
                  ) : (
                    <Square size={14} className="text-ink/20 mt-0.5 shrink-0" />
                  )}
                  <span
                    className={`text-xs leading-snug ${
                      todo.done ? 'line-through text-ink/25' : 'text-ink/70'
                    }`}
                  >
                    {todo.text}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )
      })}
    </div>
  )
}
