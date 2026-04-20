import { useState, useEffect, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import {
  Mic,
  Bot,
  TerminalSquare,
  FolderKanban,
  CircleDollarSign,
  Users,
  BookOpen,
  Menu,
  X,
} from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import ProjectPicker from './ProjectPicker'

// All navigation lives in the slide-out drawer. Voice is the default home;
// everything else is one tap away but doesn't steal vertical space.
const NAV_ITEMS = [
  { path: '/voice', label: 'Voice', icon: Mic },
  { path: '/team', label: 'Team', icon: Users },
  { path: '/agents', label: 'Agents', icon: Bot },
  { path: '/projects', label: 'Projects', icon: FolderKanban },
  { path: '/memory', label: 'Memory', icon: BookOpen },
  { path: '/usage', label: 'Usage', icon: CircleDollarSign },
  { path: '/terminal', label: 'Terminal', icon: TerminalSquare },
] as const

export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [drawerOpen, setDrawerOpen] = useState(false)

  // lightweight connection just to show the dot
  const { isConnected } = useWebSocket({
    path: '/ws/voice',
    autoConnect: true,
  })

  // Close drawer on route change
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // Lock body scroll while drawer is open (prevents background rubber-banding on iOS)
  useEffect(() => {
    if (!drawerOpen) return
    const prev = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.body.style.overflow = prev
    }
  }, [drawerOpen])

  function handleNav(path: string) {
    setDrawerOpen(false)
    navigate(path)
  }

  const currentLabel =
    NAV_ITEMS.find((n) =>
      n.path === '/projects'
        ? location.pathname.startsWith('/projects')
        : location.pathname === n.path
    )?.label ?? 'Chief'

  return (
    <div className="h-[100dvh] flex flex-col bg-surface">
      {/* Top bar — sleeker, single row. Left: page label. Right: project + status + menu. */}
      <header className="flex items-center justify-between px-4 py-2.5 bg-surface border-b border-surface-border">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-base font-semibold text-white tracking-tight truncate">
            {currentLabel}
          </span>
          <span className="text-xs text-white/40 hidden sm:inline">· Chief</span>
        </div>

        <div className="flex items-center gap-2.5">
          <ProjectPicker />
          <div
            title={isConnected ? 'Connected' : 'Offline'}
            className={`w-2 h-2 rounded-full transition-colors ${
              isConnected ? 'bg-status-online' : 'bg-status-offline'
            }`}
          />
          <button
            onClick={() => setDrawerOpen(true)}
            aria-label="Open menu"
            aria-expanded={drawerOpen}
            className="w-10 h-10 flex items-center justify-center rounded-lg text-white/70 active:text-white active:bg-surface-raised transition-colors -mr-1.5"
          >
            <Menu size={22} strokeWidth={2} />
          </button>
        </div>
      </header>

      {/* Main content — fills everything below the header */}
      <main className="flex-1 overflow-hidden">{children}</main>

      {/* Drawer — slides in from the right */}
      {drawerOpen && (
        <>
          {/* Backdrop */}
          <div
            className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm animate-[fadeIn_0.15s_ease-out]"
            onClick={() => setDrawerOpen(false)}
          />
          {/* Panel */}
          <aside
            role="dialog"
            aria-label="Navigation menu"
            className="fixed right-0 top-0 bottom-0 z-50 w-[78vw] max-w-[320px] bg-surface-raised border-l border-surface-border shadow-2xl flex flex-col animate-[slideInRight_0.2s_ease-out] pb-[env(safe-area-inset-bottom)]"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
              <span className="text-sm font-semibold text-white/90 uppercase tracking-wider">
                Menu
              </span>
              <button
                onClick={() => setDrawerOpen(false)}
                aria-label="Close menu"
                className="w-9 h-9 flex items-center justify-center rounded-lg text-white/60 active:text-white active:bg-surface-overlay transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            <nav className="flex-1 overflow-y-auto py-2">
              {NAV_ITEMS.map(({ path, label, icon: Icon }) => {
                const isActive =
                  location.pathname === path ||
                  (path === '/projects' && location.pathname.startsWith('/projects'))

                return (
                  <button
                    key={path}
                    onClick={() => handleNav(path)}
                    className={`w-full flex items-center gap-3 px-4 py-3.5 text-sm font-medium transition-colors ${
                      isActive
                        ? 'text-chief-light bg-chief/10 border-l-2 border-chief'
                        : 'text-white/70 active:text-white active:bg-surface-overlay border-l-2 border-transparent'
                    }`}
                  >
                    <Icon size={18} strokeWidth={isActive ? 2.5 : 1.75} />
                    <span>{label}</span>
                  </button>
                )
              })}
            </nav>

            <div className="px-4 py-3 border-t border-surface-border flex items-center gap-2 text-xs text-white/50">
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  isConnected ? 'bg-status-online' : 'bg-status-offline'
                }`}
              />
              {isConnected ? 'Connected' : 'Offline'}
            </div>
          </aside>
        </>
      )}
    </div>
  )
}
