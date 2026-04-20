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
  ChevronsRight,
  ChevronsLeft,
} from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import ProjectPicker from './ProjectPicker'

// Nav order: Voice first (default home), Team/Agents next, execution surfaces
// (Projects/Memory), then meta (Usage/Terminal). Mirrored in the desktop rail
// and the mobile drawer.
const NAV_ITEMS = [
  { path: '/voice', label: 'Voice', icon: Mic },
  { path: '/team', label: 'Team', icon: Users },
  { path: '/agents', label: 'Agents', icon: Bot },
  { path: '/projects', label: 'Projects', icon: FolderKanban },
  { path: '/memory', label: 'Memory', icon: BookOpen },
  { path: '/usage', label: 'Usage', icon: CircleDollarSign },
  { path: '/terminal', label: 'Terminal', icon: TerminalSquare },
] as const

const RAIL_WIDTH_COLLAPSED = 64
const RAIL_WIDTH_EXPANDED = 232

export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()
  const [drawerOpen, setDrawerOpen] = useState(false)
  // Desktop rail defaults to collapsed so content has maximum canvas. The
  // owner's choice persists across sessions via localStorage.
  const [railExpanded, setRailExpanded] = useState<boolean>(() => {
    if (typeof window === 'undefined') return false
    return localStorage.getItem('chief.railExpanded') === '1'
  })

  useEffect(() => {
    if (typeof window === 'undefined') return
    localStorage.setItem('chief.railExpanded', railExpanded ? '1' : '0')
  }, [railExpanded])

  // lightweight connection just to show the dot
  const { isConnected } = useWebSocket({
    path: '/ws/voice',
    autoConnect: true,
  })

  // Close mobile drawer on route change
  useEffect(() => {
    setDrawerOpen(false)
  }, [location.pathname])

  // Lock body scroll while mobile drawer is open (prevents background rubber-banding on iOS)
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
    <div className="h-[100dvh] flex bg-surface">
      {/* ─── Desktop left rail (md+) ────────────────────────────────────────
          Navy chrome (#1a2230) — signature inverse-contrast surface against
          the light body. Default collapsed to 64px icons; expands to 232px
          with labels when the user clicks the toggle. Active item = amber
          left-border + amber icon + subtle amber row tint. */}
      <aside
        className="hidden md:flex flex-col shrink-0 bg-rail text-rail-ink border-r border-rail-border transition-[width] duration-200 ease-out"
        style={{
          width: railExpanded ? RAIL_WIDTH_EXPANDED : RAIL_WIDTH_COLLAPSED,
        }}
      >
        {/* Brand plate. Fraunces wordmark when expanded, amber glyph when
            collapsed so there's still identity at icon width. */}
        <div className="h-14 flex items-center px-3 border-b border-rail-border shrink-0">
          {railExpanded ? (
            <span className="font-display text-lg font-semibold tracking-tight text-rail-ink">
              Chief<span className="text-accent">.</span>
            </span>
          ) : (
            <span className="w-10 h-10 flex items-center justify-center rounded-lg font-display text-xl font-bold text-accent mx-auto">
              C
            </span>
          )}
        </div>

        <nav className="flex-1 overflow-y-auto py-3">
          {NAV_ITEMS.map(({ path, label, icon: Icon }) => {
            const isActive =
              location.pathname === path ||
              (path === '/projects' && location.pathname.startsWith('/projects'))
            return (
              <button
                key={path}
                onClick={() => handleNav(path)}
                aria-label={label}
                aria-current={isActive ? 'page' : undefined}
                title={railExpanded ? undefined : label}
                className={`
                  group relative w-full flex items-center gap-3 px-3 py-2.5 my-0.5
                  text-sm font-medium transition-colors
                  ${
                    isActive
                      ? 'text-accent bg-[rgba(232,161,64,0.10)]'
                      : 'text-rail-muted hover:text-rail-ink hover:bg-rail-raised'
                  }
                `}
              >
                {/* Amber left-border for active — absolute so it doesn't push
                    the icon around. */}
                <span
                  className={`absolute left-0 top-1 bottom-1 w-[3px] rounded-r-sm transition-colors ${
                    isActive ? 'bg-accent' : 'bg-transparent'
                  }`}
                />
                <Icon
                  size={20}
                  strokeWidth={isActive ? 2.25 : 1.75}
                  className="shrink-0"
                />
                {railExpanded && <span className="truncate">{label}</span>}
              </button>
            )
          })}
        </nav>

        <div className="border-t border-rail-border p-2">
          <button
            onClick={() => setRailExpanded((v) => !v)}
            aria-label={railExpanded ? 'Collapse sidebar' : 'Expand sidebar'}
            className="w-full flex items-center gap-2 px-2 py-2 rounded-md text-rail-muted hover:text-rail-ink hover:bg-rail-raised transition-colors text-xs"
            title={railExpanded ? 'Collapse' : 'Expand'}
          >
            {railExpanded ? (
              <>
                <ChevronsLeft size={16} className="shrink-0" />
                <span className="truncate">Collapse</span>
              </>
            ) : (
              <ChevronsRight size={16} className="mx-auto" />
            )}
          </button>
        </div>
      </aside>

      {/* ─── Main column ─────────────────────────────────────────────────── */}
      <div className="flex-1 flex flex-col min-w-0">
        {/* Top bar — light, single row. Page title in Fraunces on the left.
            Project picker + status dot on the right. Mobile: hamburger appears
            because the rail is hidden. */}
        <header className="h-14 flex items-center justify-between px-4 md:px-6 bg-surface-raised border-b border-surface-border shrink-0">
          <div className="flex items-center gap-3 min-w-0">
            <button
              onClick={() => setDrawerOpen(true)}
              aria-label="Open menu"
              aria-expanded={drawerOpen}
              className="md:hidden w-10 h-10 -ml-2 flex items-center justify-center rounded-lg text-ink/60 active:text-ink active:bg-surface-overlay transition-colors"
            >
              <Menu size={22} strokeWidth={2} />
            </button>
            <h1 className="font-display text-lg md:text-xl font-semibold text-ink tracking-tight truncate">
              {currentLabel}
            </h1>
          </div>

          <div className="flex items-center gap-2.5 md:gap-3">
            <ProjectPicker />
            <div
              title={isConnected ? 'Connected' : 'Offline'}
              className={`w-2 h-2 rounded-full transition-colors ${
                isConnected ? 'bg-status-online' : 'bg-status-offline'
              }`}
            />
          </div>
        </header>

        {/* Main content fills everything below the header */}
        <main className="flex-1 overflow-hidden bg-surface">{children}</main>
      </div>

      {/* ─── Mobile drawer ───────────────────────────────────────────────── */}
      {drawerOpen && (
        <>
          <div
            className="fixed inset-0 z-40 bg-ink/30 backdrop-blur-sm animate-[fadeIn_0.15s_ease-out] md:hidden"
            onClick={() => setDrawerOpen(false)}
          />
          <aside
            role="dialog"
            aria-label="Navigation menu"
            className="fixed right-0 top-0 bottom-0 z-50 w-[78vw] max-w-[320px] bg-surface-raised border-l border-surface-border shadow-card-hover flex flex-col animate-[slideInRight_0.2s_ease-out] pb-[env(safe-area-inset-bottom)] md:hidden"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-surface-border">
              <span className="font-display text-sm font-semibold text-ink uppercase tracking-wider">
                Menu
              </span>
              <button
                onClick={() => setDrawerOpen(false)}
                aria-label="Close menu"
                className="w-9 h-9 flex items-center justify-center rounded-lg text-ink/60 active:text-ink active:bg-surface-overlay transition-colors"
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
                        ? 'text-accent bg-[rgba(232,161,64,0.10)] border-l-[3px] border-accent'
                        : 'text-ink/70 active:text-ink active:bg-surface-overlay border-l-[3px] border-transparent'
                    }`}
                  >
                    <Icon size={18} strokeWidth={isActive ? 2.25 : 1.75} />
                    <span>{label}</span>
                  </button>
                )
              })}
            </nav>

            <div className="px-4 py-3 border-t border-surface-border flex items-center gap-2 text-xs text-ink/50">
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
