import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, PhoneOff, Send, Camera, Monitor, ChevronDown, ChevronUp } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import { useVad } from '../hooks/useVad'
import { useProjectContext } from '../hooks/useProjectContext'
import { UsageMeter } from '../components/UsageMeter'
import { SessionBadge } from '../components/SessionBadge'
import type { VoiceMessage, Agent, WsEvent, ActiveModel, WsUsageEvent } from '../lib/api'

type VoiceState = 'idle' | 'listening' | 'speaking' | 'thinking'

function float32ToWav(samples: Float32Array, sampleRate = 16000): ArrayBuffer {
  const numSamples = samples.length
  const buffer = new ArrayBuffer(44 + numSamples * 2)
  const view = new DataView(buffer)

  function writeStr(offset: number, str: string) {
    for (let i = 0; i < str.length; i++) view.setUint8(offset + i, str.charCodeAt(i))
  }

  writeStr(0, 'RIFF')
  view.setUint32(4, 36 + numSamples * 2, true)
  writeStr(8, 'WAVE')
  writeStr(12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true)
  view.setUint16(22, 1, true)
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeStr(36, 'data')
  view.setUint32(40, numSamples * 2, true)

  let offset = 44
  for (let i = 0; i < numSamples; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]))
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true)
    offset += 2
  }

  return buffer
}

// ─── State label ──────────────────────────────────────────────────────────────

const STATE_LABELS: Record<VoiceState, string> = {
  idle: 'Ready',
  listening: 'Listening...',
  speaking: 'Chief is speaking...',
  thinking: 'Thinking...',
}

const STATE_COLORS: Record<VoiceState, string> = {
  idle: 'text-white/30',
  listening: 'text-emerald-400',
  speaking: 'text-chief',
  thinking: 'text-status-working',
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function VoicePage() {
  const [messages, setMessages] = useState<VoiceMessage[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [textInput, setTextInput] = useState('')
  const [speed, setSpeed] = useState(1)
  const [conversationActive, setConversationActive] = useState(false)
  const [showUsage, setShowUsage] = useState(false)
  const [showVadDebug, setShowVadDebug] = useState(false)

  const [activeModel, setActiveModel] = useState<ActiveModel | null>(null)
  const [usage, setUsage] = useState<WsUsageEvent | null>(null)
  const [turnCount, setTurnCount] = useState(0)

  const { current: currentProject } = useProjectContext()

  const scrollRef = useRef<HTMLDivElement>(null)
  const responseBuffer = useRef('')

  const audioQueueRef = useRef<ArrayBuffer[]>([])
  const isPlayingAudioRef = useRef(false)
  // iOS Safari: HTMLAudioElement.play() only works inside a user-gesture stack.
  // Creating a new Audio() per chunk in a WebSocket callback fails silently on
  // iPhone (DOMException swallowed by .catch{}). Fix: a single AudioContext
  // primed on first tap, then every chunk is decoded + played via Web Audio API.
  const audioContextRef = useRef<AudioContext | null>(null)
  const currentSourceRef = useRef<AudioBufferSourceNode | null>(null)

  const unlockAudio = useCallback(() => {
    if (audioContextRef.current) {
      // Re-resume in case iOS auto-suspended between gestures.
      if (audioContextRef.current.state === 'suspended') {
        audioContextRef.current.resume().catch(() => {})
      }
      return
    }
    const Ctor = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext
    if (!Ctor) return
    const ctx = new Ctor()
    // Play a 1-sample silent buffer to "unlock" audio output on iOS.
    try {
      const buf = ctx.createBuffer(1, 1, 22050)
      const src = ctx.createBufferSource()
      src.buffer = buf
      src.connect(ctx.destination)
      src.start(0)
    } catch {
      // Best-effort unlock; proceed regardless.
    }
    audioContextRef.current = ctx
  }, [])

  const stopAudioPlayback = useCallback(() => {
    audioQueueRef.current = []
    if (currentSourceRef.current) {
      try {
        currentSourceRef.current.stop()
      } catch {
        // Already stopped / not yet started — ignore.
      }
      currentSourceRef.current = null
    }
    isPlayingAudioRef.current = false
  }, [])

  const playNextChunk = useCallback(async () => {
    if (isPlayingAudioRef.current || audioQueueRef.current.length === 0) return
    const ctx = audioContextRef.current
    if (!ctx) {
      // No primed AudioContext — shouldn't happen after a user gesture, but
      // drop the chunk rather than silently queueing forever.
      audioQueueRef.current = []
      return
    }
    isPlayingAudioRef.current = true

    const chunk = audioQueueRef.current.shift()!
    try {
      // decodeAudioData requires a detached ArrayBuffer copy on some browsers.
      const buffer = await ctx.decodeAudioData(chunk.slice(0))
      const source = ctx.createBufferSource()
      source.buffer = buffer
      source.playbackRate.value = speed
      source.connect(ctx.destination)
      currentSourceRef.current = source
      source.onended = () => {
        currentSourceRef.current = null
        isPlayingAudioRef.current = false
        if (audioQueueRef.current.length > 0) playNextChunk()
        else setVoiceState('listening')
      }
      source.start(0)
    } catch {
      currentSourceRef.current = null
      isPlayingAudioRef.current = false
      if (audioQueueRef.current.length > 0) playNextChunk()
      else setVoiceState('listening')
    }
  }, [speed])

  const { send, isConnected } = useWebSocket({
    path: '/ws/voice',
    onBinary: useCallback((data: ArrayBuffer) => {
      audioQueueRef.current.push(data)
    }, []),
    onMessage: useCallback((data: string) => {
      try {
        const parsed = JSON.parse(data) as WsEvent

        if (parsed.type === 'transcript') {
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'user',
              content: parsed.content,
              timestamp: new Date().toISOString(),
            },
          ])
        }

        if (parsed.type === 'active_model') {
          setActiveModel(parsed.model)
        }

        if (parsed.type === 'token') {
          responseBuffer.current += parsed.text
          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant' && last.id === 'streaming') {
              return [
                ...prev.slice(0, -1),
                { ...last, content: responseBuffer.current },
              ]
            }
            return [
              ...prev,
              {
                id: 'streaming',
                role: 'assistant',
                content: responseBuffer.current,
                timestamp: new Date().toISOString(),
              },
            ]
          })
        }

        if (parsed.type === 'message_done') {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, id: crypto.randomUUID() } : m
            )
          )
          responseBuffer.current = ''
          setVoiceState('listening')
        }

        if (parsed.type === 'tts_start') {
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, id: crypto.randomUUID() } : m
            )
          )
          responseBuffer.current = ''
          setVoiceState('speaking')
        }

        if (parsed.type === 'tts_end') {
          if (audioQueueRef.current.length > 0) {
            playNextChunk()
          } else {
            setVoiceState('listening')
          }
        }

        if (parsed.type === 'turn_cancelled') {
          // Backend aborted the turn (barge-in or superseded). Binary TTS frames
          // in flight at cancel-time can still arrive and land in audioQueueRef;
          // flush so they don't replay on the next turn.
          stopAudioPlayback()
          setVoiceState(conversationActive ? 'listening' : 'idle')
        }

        if (parsed.type === 'usage') {
          setUsage(parsed)
          setTurnCount((n) => n + 1)
        }

        if (parsed.type === 'agent_status') {
          setAgents(
            (parsed.agents || []).map((a: Record<string, string>, i: number) => ({
              id: `agent-${i}`,
              name: a.name || 'Agent',
              role: a.role || '',
              model: a.model || '',
              task: a.last_output || a.role || '',
              status: a.status === 'running' ? 'working' : (a.status as Agent['status']),
              started_at: null,
              duration_seconds: null,
            }))
          )
        }

        if (parsed.type === 'error' as string) {
          const err = parsed as unknown as { type: 'error'; message: string }
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: `Error: ${err.message}`,
              timestamp: new Date().toISOString(),
            },
          ])
          setVoiceState(conversationActive ? 'listening' : 'idle')
        }
      } catch {
        // ignore non-JSON
      }
    }, [conversationActive, playNextChunk, stopAudioPlayback]),
  })

  const { start: startVad, stop: stopVad, speaking: vadSpeaking, error: vadError, status: vadStatus, frameCount: vadFrames, speechStartCount: vadStarts, speechEndCount: vadEnds, lastAudioSamples: vadLastSamples } = useVad({
    onSpeechStart: useCallback(() => {
      // Barge-in: if Chief is speaking, cut local audio AND tell backend to stop
      // generating tokens / synthesizing TTS so we don't bill for output we'll never hear.
      if (isPlayingAudioRef.current) {
        stopAudioPlayback()
        send(JSON.stringify({ type: 'interrupt' }))
      }
      setVoiceState('speaking')
    }, [stopAudioPlayback, send]),
    onSpeechEnd: useCallback((audio: Float32Array) => {
      setVoiceState('thinking')
      const wav = float32ToWav(audio)
      send(wav)
    }, [send]),
  })

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // Send project context frame to backend whenever the context changes or WS connects.
  // This keeps the backend in sync so it can scope the system prompt correctly.
  useEffect(() => {
    if (!isConnected) return
    send(JSON.stringify({ type: 'context', project: currentProject }))
  }, [isConnected, currentProject, send])

  useEffect(() => {
    if (!conversationActive) {
      setVoiceState('idle')
    } else if (!vadSpeaking && voiceState === 'idle') {
      setVoiceState('listening')
    }
  }, [conversationActive, vadSpeaking, voiceState])

  async function handleStartConversation() {
    unlockAudio()
    setConversationActive(true)
    setVoiceState('listening')
    await startVad()
  }

  function handleEndConversation() {
    stopVad()
    stopAudioPlayback()
    setConversationActive(false)
    setVoiceState('idle')
  }

  async function handleToggleVoice() {
    if (conversationActive) handleEndConversation()
    else await handleStartConversation()
  }

  function handleTextSend(e: FormEvent) {
    e.preventDefault()
    if (!textInput.trim() || voiceState === 'thinking') return

    // Prime audio on any user gesture so Chief's spoken reply plays on iPhone.
    unlockAudio()
    send(JSON.stringify({ type: 'text', content: textInput.trim() }))

    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: 'user',
        content: textInput.trim(),
        timestamp: new Date().toISOString(),
      },
    ])
    setTextInput('')
    setVoiceState('thinking')
  }

  async function handleCamera() {
    const input = document.createElement('input')
    input.type = 'file'
    input.accept = 'image/*'
    input.capture = 'environment'
    input.onchange = async () => {
      const file = input.files?.[0]
      if (!file) return
      const reader = new FileReader()
      reader.onloadend = () => {
        send(JSON.stringify({ type: 'image', data: reader.result as string }))
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'user',
            content: '[Photo attached]',
            timestamp: new Date().toISOString(),
          },
        ])
        setVoiceState('thinking')
      }
      reader.readAsDataURL(file)
    }
    input.click()
  }

  async function handleScreenshot() {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({ video: true })
      const track = stream.getVideoTracks()[0]
      const canvas = document.createElement('canvas')
      const video = document.createElement('video')
      video.srcObject = stream
      await video.play()
      canvas.width = video.videoWidth
      canvas.height = video.videoHeight
      canvas.getContext('2d')?.drawImage(video, 0, 0)
      track.stop()
      const dataUrl = canvas.toDataURL('image/png')
      send(JSON.stringify({ type: 'screenshot', data: dataUrl }))
      setMessages((prev) => [
        ...prev,
        {
          id: crypto.randomUUID(),
          role: 'user',
          content: '[Screenshot attached]',
          timestamp: new Date().toISOString(),
        },
      ])
      setVoiceState('thinking')
    } catch {
      // User cancelled or not supported
    }
  }

  const speeds = [0.75, 1, 1.25, 1.5]

  function formatTime(iso: string) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const workingAgents = agents.filter((a) => a.status === 'working')

  // Determine label shown under orb during active session
  function getActiveLabel(): string {
    if (vadSpeaking) return 'Listening to you...'
    if (voiceState === 'thinking') return 'Thinking...'
    if (voiceState === 'speaking') return 'Chief is speaking'
    return 'Listening...'
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header strip */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-surface">
        <div className="flex items-center gap-3">
          <span className={`text-xs font-medium ${STATE_COLORS[voiceState]}`}>
            {voiceState === 'idle' ? 'Ready' : STATE_LABELS[voiceState]}
          </span>
          {!isConnected && (
            <span className="text-xs text-white/30">Connecting...</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {usage && (
            <SessionBadge
              sessionId={usage.session_id}
              costCents={usage.session_total_cents}
              turnCount={turnCount}
              model={activeModel}
            />
          )}
          {usage && (
            <button
              onClick={() => setShowUsage((v) => !v)}
              className="p-1 rounded-md text-white/30 hover:text-white/60 transition-colors"
            >
              <ChevronDown
                size={14}
                className={`transition-transform ${showUsage ? 'rotate-180' : ''}`}
              />
            </button>
          )}
        </div>
      </div>

      {/* Usage meter (collapsible) */}
      {showUsage && usage && (
        <div className="px-4 py-3 border-b border-surface-border bg-surface">
          <UsageMeter
            sessionId={usage.session_id}
            inputTokens={usage.input_tokens}
            outputTokens={usage.output_tokens}
            cachedTokens={usage.cached_tokens}
            costCents={usage.session_total_cents}
            model={activeModel}
          />
        </div>
      )}

      {/* Main content area — single chat-first layout. The old centerpiece
          orb is gone: the mic lives inline in the composer row, voice state
          shows as a thin strip above the chat, and messages take the full
          viewport on mobile. */}
      <div className="flex-1 flex flex-col min-h-0">
        {conversationActive && (
          <div className="flex items-center justify-center gap-3 px-4 py-2 border-b border-surface-border bg-surface-raised/30 shrink-0">
            <span className={`text-xs font-medium transition-colors ${
              vadSpeaking
                ? 'text-amber-400'
                : voiceState === 'thinking'
                ? 'text-status-working'
                : voiceState === 'speaking'
                ? 'text-chief-light'
                : 'text-emerald-400'
            }`}>
              {getActiveLabel()}
            </span>
            <button
              onClick={handleEndConversation}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-red-600/25 border border-red-600/50 text-red-300 hover:bg-red-600/35 active:scale-95 transition-all text-xs font-semibold"
            >
              <PhoneOff size={12} />
              End call
            </button>
          </div>
        )}

        {/* Message history — full-height scroll area */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {messages.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
              <p className="text-white/40 text-sm font-medium">
                {conversationActive ? 'Speak to start a conversation' : 'Tap the mic to talk, or type a message'}
              </p>
              {!isConnected && (
                <p className="text-white/30 text-xs">Connecting to server…</p>
              )}
            </div>
          )}

          {messages.map((msg) => (
            <div
              key={msg.id}
              className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
            >
              <div
                className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${
                  msg.role === 'user'
                    ? 'bg-chief text-white rounded-br-md'
                    : 'bg-surface-raised text-white/90 rounded-bl-md'
                }`}
              >
                <p className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                <p className={`text-[10px] mt-1 ${msg.role === 'user' ? 'text-white/50' : 'text-white/30'}`}>
                  {formatTime(msg.timestamp)}
                </p>
              </div>
            </div>
          ))}

          {voiceState === 'thinking' && (
            <div className="flex justify-start">
              <div className="bg-surface-raised rounded-2xl rounded-bl-md px-4 py-3">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-white/30 rounded-full animate-bounce [animation-delay:0ms]" />
                  <div className="w-2 h-2 bg-white/30 rounded-full animate-bounce [animation-delay:150ms]" />
                  <div className="w-2 h-2 bg-white/30 rounded-full animate-bounce [animation-delay:300ms]" />
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Agent status strip */}
      {workingAgents.length > 0 && (
        <div className="px-4 py-2 border-t border-surface-border bg-surface-raised/50 space-y-1 overflow-x-auto">
          {workingAgents.map((agent) => (
            <div
              key={agent.id}
              className="flex items-center gap-2 text-xs text-white/60 animate-[fadeIn_0.3s_ease-out]"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-status-working animate-pulse" />
              <span className="font-medium text-white/80">{agent.name}:</span>
              <span className="truncate">{agent.task}</span>
            </div>
          ))}
        </div>
      )}

      {/* Bottom controls */}
      <div className="px-4 pb-2 pt-2 bg-surface space-y-2">
        {/* Camera + Screenshot + Speed on a single row — saves ~60px on mobile */}
        <div className="flex items-center justify-center gap-3">
          <button
            onClick={handleCamera}
            aria-label="Attach photo"
            className="w-9 h-9 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Camera size={16} />
          </button>

          <button
            onClick={handleScreenshot}
            aria-label="Capture screen"
            className="w-9 h-9 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Monitor size={16} />
          </button>

          <div className="w-px h-6 bg-surface-border" />

          {speeds.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-2.5 py-1 rounded-full text-xs font-medium transition-colors ${
                speed === s
                  ? 'bg-chief text-white'
                  : 'bg-surface-raised text-white/40 active:text-white/60'
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        {/* Composer: inline mic toggle + text input + send. The mic is the
            voice-mode entry point (replaces the old centerpiece orb) so the
            chat gets the full viewport. Visual state reflects the pipeline. */}
        <form onSubmit={handleTextSend} className="flex gap-2 items-center">
          <button
            type="button"
            onClick={handleToggleVoice}
            disabled={!isConnected}
            aria-label={conversationActive ? 'End voice conversation' : 'Start voice conversation'}
            className={`w-11 h-11 shrink-0 flex items-center justify-center rounded-xl border transition-all active:scale-95 disabled:opacity-30 ${
              !conversationActive
                ? 'bg-surface-raised border-surface-border text-white/60 hover:text-white'
                : vadSpeaking
                ? 'bg-amber-500/30 border-amber-400 text-amber-200 animate-pulse'
                : voiceState === 'speaking'
                ? 'bg-chief-light/30 border-chief-light text-chief-light animate-pulse'
                : voiceState === 'thinking'
                ? 'bg-amber-600/20 border-amber-500/60 text-amber-300'
                : 'bg-chief/30 border-chief text-white'
            }`}
          >
            <Mic size={16} />
          </button>
          <input
            type="text"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            placeholder={conversationActive ? 'Or type a message…' : 'Type a message…'}
            className="flex-1 h-11 px-4 rounded-xl bg-surface-raised border border-surface-border text-white placeholder-white/30 text-sm focus:outline-none focus:border-chief transition-colors"
          />
          <button
            type="submit"
            disabled={!textInput.trim() || voiceState === 'thinking'}
            className="w-11 h-11 shrink-0 flex items-center justify-center rounded-xl bg-chief text-white disabled:opacity-30 active:scale-95 transition-all"
          >
            <Send size={16} />
          </button>
        </form>

        {/* Collapsible VAD debug strip — hidden by default so it doesn't eat
            ~90px of iPhone viewport. Tiny one-line status header always shows
            a colored dot + 'VAD' so you can tell if it's broken at a glance;
            tap to expand full debug details. */}
        <div className="rounded-xl bg-surface-raised border border-surface-border text-[11px] font-mono text-white/50">
          <button
            type="button"
            onClick={() => setShowVadDebug((v) => !v)}
            className="w-full flex items-center justify-between px-3 py-1.5"
          >
            <span className="flex items-center gap-2">
              <span
                className={`w-1.5 h-1.5 rounded-full ${
                  vadStatus === 'error'
                    ? 'bg-red-400'
                    : vadStatus === 'listening'
                    ? 'bg-emerald-400'
                    : 'bg-white/30'
                }`}
              />
              <span>VAD</span>
              <span className={vadStatus === 'error' ? 'text-red-400' : 'text-white/50'}>
                {vadStatus}
              </span>
            </span>
            {showVadDebug ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          </button>
          {showVadDebug && (
            <div className="px-3 pb-2 space-y-0.5 border-t border-surface-border pt-1.5">
              {vadError && <div className="flex justify-between"><span>Error</span><span className="text-red-400 truncate max-w-[16rem]">{vadError}</span></div>}
              <div className="flex justify-between"><span>Frames processed</span><span>{vadFrames}</span></div>
              <div className="flex justify-between"><span>Speech events</span><span>{vadStarts} start / {vadEnds} end</span></div>
              <div className="flex justify-between"><span>Last audio samples</span><span>{vadLastSamples}</span></div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
