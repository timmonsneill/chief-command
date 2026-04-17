import { useState, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Mic, Bot, TerminalSquare, FolderKanban, CircleDollarSign, Users, BookOpen, MoreHorizontal } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import ProjectPicker from './ProjectPicker'

// Voice · Team · Agents · Projects · Usage · Memory
// Terminal collapses into a "More" overflow menu on narrow screens.
const PRIMARY_TABS = [
  { path: '/voice', label: 'Voice', icon: Mic },
  { path: '/team', label: 'Team', icon: Users },
  { path: '/agents', label: 'Agents', icon: Bot },
  { path: '/projects', label: 'Projects', icon: FolderKanban },
  { path: '/usage', label: 'Usage', icon: CircleDollarSign },
  { path: '/memory', label: 'Memory', icon: BookOpen },
] as const

const OVERFLOW_TABS = [
  { path: '/terminal', label: 'Terminal', icon: TerminalSquare },
] as const

export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [overflowOpen, setOverflowOpen] = useState(false)

  // lightweight connection just to show the dot
  const { isConnected } = useWebSocket({
    path: '/ws/voice',
    autoConnect: true,
  })

  const isOverflowActive = OVERFLOW_TABS.some(
    (t) => location.pathname === t.path
  )

  function handleOverflowNav(path: string) {
    setOverflowOpen(false)
    navigate(path)
  }

  return (
    <div className="h-[100dvh] flex flex-col bg-surface">
      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-surface-raised border-b border-surface-border">
        <span className="text-sm font-semibold text-white/80 tracking-wide">
          Chief
        </span>
        <div className="flex items-center gap-3">
          <ProjectPicker />
          <div className="flex items-center gap-2">
            <div
              className={`w-2 h-2 rounded-full transition-colors ${
                isConnected ? 'bg-status-online' : 'bg-status-offline'
              }`}
            />
            <span className="text-xs text-white/40">
              {isConnected ? 'Connected' : 'Offline'}
            </span>
          </div>
        </div>
      </div>

      {/* Main content */}
      <main className="flex-1 overflow-hidden">{children}</main>

      {/* Bottom navigation */}
      <nav className="bg-surface-raised border-t border-surface-border px-2 pb-[env(safe-area-inset-bottom)]">
        <div className="flex items-center justify-around">
          {PRIMARY_TABS.map(({ path, label, icon: Icon }) => {
            const isActive =
              location.pathname === path ||
              (path === '/projects' && location.pathname.startsWith('/projects'))

            return (
              <button
                key={path}
                onClick={() => {
                  setOverflowOpen(false)
                  navigate(path)
                }}
                className={`flex flex-col items-center gap-0.5 py-2 px-2 min-w-[52px] min-h-[44px] transition-colors ${
                  isActive
                    ? 'text-chief'
                    : 'text-white/40 active:text-white/60'
                }`}
              >
                <Icon size={20} strokeWidth={isActive ? 2.5 : 1.5} />
                <span className="text-[10px] font-medium">{label}</span>
              </button>
            )
          })}

          {/* More button — Sessions + Terminal overflow */}
          <div className="relative">
            <button
              onClick={() => setOverflowOpen((v) => !v)}
              className={`flex flex-col items-center gap-0.5 py-2 px-2 min-w-[52px] min-h-[44px] transition-colors ${
                isOverflowActive || overflowOpen
                  ? 'text-chief'
                  : 'text-white/40 active:text-white/60'
              }`}
            >
              <MoreHorizontal size={20} strokeWidth={isOverflowActive || overflowOpen ? 2.5 : 1.5} />
              <span className="text-[10px] font-medium">More</span>
            </button>

            {overflowOpen && (
              <>
                {/* Backdrop */}
                <div
                  className="fixed inset-0 z-20"
                  onClick={() => setOverflowOpen(false)}
                />
                {/* Dropdown */}
                <div className="absolute bottom-full right-0 mb-2 z-30 bg-surface-raised border border-surface-border rounded-xl shadow-xl overflow-hidden min-w-[140px]">
                  {OVERFLOW_TABS.map(({ path, label, icon: Icon }) => {
                    const isActive = location.pathname === path
                    return (
                      <button
                        key={path}
                        onClick={() => handleOverflowNav(path)}
                        className={`w-full flex items-center gap-3 px-4 py-3 text-sm transition-colors active:bg-surface-overlay ${
                          isActive ? 'text-chief' : 'text-white/60'
                        }`}
                      >
                        <Icon size={16} strokeWidth={isActive ? 2.5 : 1.5} />
                        {label}
                      </button>
                    )
                  })}
                </div>
              </>
            )}
          </div>
        </div>
      </nav>
    </div>
  )
}
