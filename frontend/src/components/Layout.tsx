import { type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router-dom'
import { Mic, Bot, TerminalSquare, FolderKanban } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'

const tabs = [
  { path: '/voice', label: 'Voice', icon: Mic },
  { path: '/agents', label: 'Agents', icon: Bot },
  { path: '/terminal', label: 'Terminal', icon: TerminalSquare },
  { path: '/projects', label: 'Projects', icon: FolderKanban },
] as const

export default function Layout({ children }: { children: ReactNode }) {
  const location = useLocation()
  const navigate = useNavigate()

  // lightweight connection just to show the dot
  const { isConnected } = useWebSocket({
    path: '/ws/voice',
    autoConnect: true,
  })

  return (
    <div className="h-[100dvh] flex flex-col bg-surface">
      {/* Status bar */}
      <div className="flex items-center justify-between px-4 py-2 bg-surface-raised border-b border-surface-border">
        <span className="text-sm font-semibold text-white/80 tracking-wide">
          Chief
        </span>
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

      {/* Main content */}
      <main className="flex-1 overflow-hidden">{children}</main>

      {/* Bottom navigation */}
      <nav className="bg-surface-raised border-t border-surface-border px-2 pb-[env(safe-area-inset-bottom)]">
        <div className="flex items-center justify-around">
          {tabs.map(({ path, label, icon: Icon }) => {
            const isActive =
              location.pathname === path ||
              (path === '/projects' && location.pathname.startsWith('/projects'))

            return (
              <button
                key={path}
                onClick={() => navigate(path)}
                className={`flex flex-col items-center gap-0.5 py-2 px-3 min-w-[64px] min-h-[44px] transition-colors ${
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
        </div>
      </nav>
    </div>
  )
}
