import { useState, useRef, useEffect, type FormEvent } from 'react'
import { Plus, Send, Camera, Monitor, Gauge } from 'lucide-react'

interface ComposerProps {
  value: string
  onChange: (v: string) => void
  onSubmit: (e: FormEvent) => void
  onCamera: () => void
  onScreenshot: () => void
  disabled: boolean
  speed: number
  onSpeedChange: (s: number) => void
}

const SPEEDS = [0.75, 1, 1.25, 1.5]

/**
 * Composer — clean by default. No always-on camera/screenshot buttons.
 *
 * - When collapsed: just input + send (+ mic is handled by the page).
 * - When focused or user clicks the inline +: show a popover with
 *   Attach photo / Capture screen / Playback speed.
 */
export default function Composer({
  value,
  onChange,
  onSubmit,
  onCamera,
  onScreenshot,
  disabled,
  speed,
  onSpeedChange,
}: ComposerProps) {
  const [focused, setFocused] = useState(false)
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Close popover on outside tap
  useEffect(() => {
    if (!menuOpen) return
    const onDocPointer = (e: Event) => {
      if (!menuRef.current) return
      if (menuRef.current.contains(e.target as Node)) return
      setMenuOpen(false)
    }
    document.addEventListener('mousedown', onDocPointer)
    document.addEventListener('touchstart', onDocPointer)
    return () => {
      document.removeEventListener('mousedown', onDocPointer)
      document.removeEventListener('touchstart', onDocPointer)
    }
  }, [menuOpen])

  // Show the + button when the input is focused OR contains text OR the menu is open
  const showPlus = focused || value.length > 0 || menuOpen

  function pickPhoto() {
    setMenuOpen(false)
    onCamera()
  }

  function pickScreen() {
    setMenuOpen(false)
    onScreenshot()
  }

  return (
    <form
      onSubmit={onSubmit}
      className="relative flex items-center gap-2"
    >
      {/* Input with inline + button */}
      <div className="relative flex-1">
        {showPlus && (
          <button
            type="button"
            onClick={(e) => {
              e.preventDefault()
              // Keep the input focused so the + button stays visible
              inputRef.current?.focus()
              setMenuOpen((v) => !v)
            }}
            aria-label="Attachments and settings"
            aria-expanded={menuOpen}
            className={`absolute left-1.5 top-1/2 -translate-y-1/2 w-8 h-8 flex items-center justify-center rounded-full transition-all ${
              menuOpen
                ? 'bg-chief text-white rotate-45'
                : 'bg-surface-overlay text-white/70 active:text-white'
            }`}
          >
            <Plus size={16} strokeWidth={2.5} />
          </button>
        )}

        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => onChange(e.target.value)}
          onFocus={() => setFocused(true)}
          // Don't lose focus immediately when tapping the + button — handled by popover's own outside-click handler
          onBlur={() => setTimeout(() => setFocused(false), 150)}
          placeholder="Type a message..."
          className={`w-full h-12 ${showPlus ? 'pl-11' : 'pl-4'} pr-4 rounded-2xl bg-surface-raised border border-surface-border text-white placeholder-white/40 text-sm focus:outline-none focus:border-chief/70 transition-all`}
        />

        {/* Popover — anchored to the + button */}
        {menuOpen && (
          <div
            ref={menuRef}
            className="absolute left-0 bottom-full mb-2 z-30 min-w-[220px] bg-surface-overlay border border-surface-border rounded-2xl shadow-2xl overflow-hidden animate-[popUp_0.15s_ease-out]"
          >
            <button
              type="button"
              onClick={pickPhoto}
              className="w-full flex items-center gap-3 px-4 py-3 text-sm text-white/85 active:bg-surface-raised transition-colors"
            >
              <Camera size={17} className="text-chief-light" />
              Attach photo
            </button>
            <button
              type="button"
              onClick={pickScreen}
              className="w-full flex items-center gap-3 px-4 py-3 text-sm text-white/85 active:bg-surface-raised transition-colors border-t border-surface-border"
            >
              <Monitor size={17} className="text-chief-light" />
              Capture screen
            </button>

            {/* Playback speed — moved out of the always-on strip */}
            <div className="border-t border-surface-border px-3 py-2.5">
              <div className="flex items-center gap-2 mb-1.5 text-[11px] uppercase tracking-wider text-white/45">
                <Gauge size={12} />
                <span>Playback speed</span>
              </div>
              <div className="flex items-center gap-1">
                {SPEEDS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => onSpeedChange(s)}
                    className={`flex-1 py-1.5 rounded-lg text-xs font-medium transition-colors ${
                      speed === s
                        ? 'bg-chief text-white'
                        : 'bg-surface-raised text-white/60 active:text-white'
                    }`}
                  >
                    {s}x
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      <button
        type="submit"
        disabled={!value.trim() || disabled}
        aria-label="Send message"
        className="w-12 h-12 flex items-center justify-center rounded-2xl bg-chief text-white disabled:opacity-25 disabled:cursor-not-allowed active:scale-95 transition-all shadow-lg shadow-chief/20"
      >
        <Send size={17} />
      </button>
    </form>
  )
}
