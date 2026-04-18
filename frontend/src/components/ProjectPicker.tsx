import { useState, useEffect, useRef } from 'react'
import { ChevronDown, Check } from 'lucide-react'
import { useProjectContext } from '../hooks/useProjectContext'

export default function ProjectPicker() {
  const { current, available, setContext, isLoading } = useProjectContext()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Close on outside click
  useEffect(() => {
    if (!open) return
    function handleClick(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClick)
    return () => document.removeEventListener('mousedown', handleClick)
  }, [open])

  async function select(project: string) {
    setOpen(false)
    if (project === current) return
    setLoading(true)
    try {
      await setContext(project)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="relative" ref={dropdownRef}>
      <button
        onClick={() => setOpen((v) => !v)}
        disabled={loading || isLoading}
        className="flex items-center gap-1.5 px-2 py-1 rounded-lg bg-surface-overlay border border-surface-border text-xs text-white/60 active:text-white/80 transition-colors disabled:opacity-50"
      >
        <span className="font-medium text-white/80">{current}</span>
        <ChevronDown
          size={12}
          className={`text-white/30 transition-transform ${open ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div className="absolute right-0 top-full mt-1 z-50 min-w-[140px] bg-surface-raised border border-surface-border rounded-xl shadow-xl overflow-hidden">
          {available.map((project) => (
            <button
              key={project}
              onClick={() => select(project)}
              className="w-full flex items-center justify-between px-3 py-2.5 text-left text-xs transition-colors active:bg-surface-overlay hover:bg-surface-overlay"
            >
              <span className={project === current ? 'text-white font-medium' : 'text-white/60'}>
                {project}
              </span>
              {project === current && (
                <Check size={12} className="text-chief shrink-0 ml-2" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
