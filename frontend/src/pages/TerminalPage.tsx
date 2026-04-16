import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Square, TerminalSquare } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'

interface TerminalEntry {
  id: string
  type: 'command' | 'output' | 'error'
  content: string
  timestamp: string
}

// Strip ANSI escape codes for clean display
function stripAnsi(text: string): string {
  // eslint-disable-next-line no-control-regex
  return text.replace(/\x1b\[[0-9;]*m/g, '')
}

export default function TerminalPage() {
  const [entries, setEntries] = useState<TerminalEntry[]>([])
  const [input, setInput] = useState('')
  const [isRunning, setIsRunning] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const { send, isConnected } = useWebSocket({
    path: '/ws/terminal',
    onMessage: useCallback((data: string) => {
      try {
        const parsed = JSON.parse(data)

        if (parsed.type === 'output' || parsed.type === 'error') {
          setEntries((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              type: parsed.type,
              content: stripAnsi(parsed.data),
              timestamp: new Date().toISOString(),
            },
          ])
        }

        if (parsed.type === 'done') {
          setIsRunning(false)
        }
      } catch {
        // plain text output
        setEntries((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            type: 'output',
            content: stripAnsi(data),
            timestamp: new Date().toISOString(),
          },
        ])
      }
    }, []),
  })

  // Auto-scroll
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [entries])

  // Focus input on mount
  useEffect(() => {
    inputRef.current?.focus()
  }, [])

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (!input.trim() || !isConnected) return

    const command = input.trim()
    setInput('')

    setEntries((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        type: 'command',
        content: command,
        timestamp: new Date().toISOString(),
      },
    ])

    send(JSON.stringify({ type: 'command', data: command }))
    setIsRunning(true)
  }

  function handleKill() {
    send(JSON.stringify({ type: 'kill' }))
    setIsRunning(false)
  }

  return (
    <div className="h-full flex flex-col bg-black">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-2 bg-surface-raised/50 border-b border-surface-border">
        <div className="flex items-center gap-2">
          <TerminalSquare size={14} className="text-chief" />
          <span className="text-xs font-medium text-white/50">Terminal</span>
        </div>
        {isRunning && (
          <span className="flex items-center gap-1.5 text-xs text-status-working">
            <div className="w-1.5 h-1.5 rounded-full bg-status-working animate-pulse" />
            Running
          </span>
        )}
      </div>

      {/* Output area */}
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto p-4 font-mono text-sm leading-relaxed"
        onClick={() => inputRef.current?.focus()}
      >
        {entries.length === 0 && (
          <div className="text-white/20 text-xs">
            {isConnected
              ? 'Ready. Type a command below.'
              : 'Connecting to terminal...'}
          </div>
        )}

        {entries.map((entry) => (
          <div key={entry.id} className="mb-1">
            {entry.type === 'command' ? (
              <div className="flex items-start gap-2">
                <span className="text-chief shrink-0">$</span>
                <span className="text-white">{entry.content}</span>
              </div>
            ) : (
              <pre
                className={`whitespace-pre-wrap break-all ${
                  entry.type === 'error'
                    ? 'text-status-offline'
                    : 'text-white/70'
                }`}
              >
                {entry.content}
              </pre>
            )}
          </div>
        ))}

        {isRunning && (
          <div className="flex items-center gap-1 text-white/30 mt-1">
            <div className="w-2 h-2 bg-status-working rounded-sm animate-pulse" />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="border-t border-surface-border bg-surface-raised/30 px-4 py-3">
        <form onSubmit={handleSubmit} className="flex items-center gap-2">
          <span className="text-chief font-mono text-sm shrink-0">$</span>
          <input
            ref={inputRef}
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder={isRunning ? 'Command running...' : 'Enter command'}
            disabled={isRunning}
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            className="flex-1 bg-transparent text-white font-mono text-sm placeholder-white/20 focus:outline-none disabled:opacity-40"
          />
          {isRunning && (
            <button
              type="button"
              onClick={handleKill}
              className="w-11 h-11 flex items-center justify-center rounded-lg bg-status-offline/10 text-status-offline active:bg-status-offline/20 transition-colors"
              title="Kill process (Ctrl+C)"
            >
              <Square size={14} fill="currentColor" />
            </button>
          )}
        </form>
      </div>
    </div>
  )
}
