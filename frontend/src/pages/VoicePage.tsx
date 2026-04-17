import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, PhoneOff, Send, Camera, Monitor, ChevronDown } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import { useVad } from '../hooks/useVad'
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

// ─── Orb visual component ────────────────────────────────────────────────────

interface VoiceOrbProps {
  state: VoiceState
  userSpeaking: boolean
}

function VoiceOrb({ state, userSpeaking }: VoiceOrbProps) {
  const isListening = state === 'listening' && !userSpeaking
  const isSpeaking = userSpeaking || state === 'speaking'
  const isThinking = state === 'thinking'
  const isChiefSpeaking = state === 'speaking' && !userSpeaking

  // Outer ripple rings — shown when listening or user speaking
  const showRipples = isListening || isSpeaking

  // Orb color classes
  let orbGradient = 'from-chief via-chief-dark to-indigo-900'
  if (isSpeaking) orbGradient = 'from-amber-500 via-chief to-indigo-800'
  if (isChiefSpeaking) orbGradient = 'from-chief-light via-chief to-indigo-900'
  if (isThinking) orbGradient = 'from-surface-raised via-surface-overlay to-surface-raised'

  // Orb animation
  let orbAnim = 'animate-orb-breathe'
  if (isSpeaking) orbAnim = 'animate-orb-pulse-strong'
  if (isChiefSpeaking) orbAnim = 'animate-orb-breathe'
  if (isThinking) orbAnim = ''

  // Ring colors
  let rippleColor = 'bg-chief/20'
  if (isSpeaking) rippleColor = 'bg-amber-500/20'

  return (
    <div className="relative flex items-center justify-center" style={{ width: 192, height: 192 }}>
      {/* Slow outer ripple */}
      {showRipples && (
        <span
          className={`absolute rounded-full ${rippleColor} animate-orb-ripple-slow`}
          style={{ width: 192, height: 192 }}
        />
      )}
      {/* Fast inner ripple */}
      {showRipples && (
        <span
          className={`absolute rounded-full ${rippleColor} animate-orb-ripple`}
          style={{ width: 192, height: 192 }}
        />
      )}

      {/* Main orb */}
      <div
        className={`relative w-40 h-40 rounded-full bg-gradient-to-br ${orbGradient} ${orbAnim} flex items-center justify-center shadow-2xl transition-all duration-500`}
        style={{
          boxShadow: isSpeaking
            ? '0 0 60px rgba(245,158,11,0.35), 0 0 120px rgba(99,102,241,0.2)'
            : isChiefSpeaking
            ? '0 0 60px rgba(99,102,241,0.45), 0 0 120px rgba(99,102,241,0.15)'
            : isThinking
            ? 'none'
            : '0 0 40px rgba(99,102,241,0.3), 0 0 80px rgba(99,102,241,0.1)',
        }}
      >
        {/* Thinking indicator */}
        {isThinking && (
          <div className="flex gap-1.5">
            <div className="w-2.5 h-2.5 bg-white/60 rounded-full animate-bounce [animation-delay:0ms]" />
            <div className="w-2.5 h-2.5 bg-white/60 rounded-full animate-bounce [animation-delay:150ms]" />
            <div className="w-2.5 h-2.5 bg-white/60 rounded-full animate-bounce [animation-delay:300ms]" />
          </div>
        )}

        {/* Mic icon — only shown when listening (never MicOff) */}
        {isListening && (
          <Mic size={40} className="text-white/90" />
        )}

        {/* Sound bars — shown when Chief is speaking */}
        {isChiefSpeaking && (
          <div className="flex items-end gap-1 h-8">
            {[0, 150, 75, 225, 50].map((delay, i) => (
              <div
                key={i}
                className="w-1.5 bg-white/80 rounded-full animate-bounce"
                style={{
                  height: [20, 32, 24, 28, 16][i],
                  animationDelay: `${delay}ms`,
                  animationDuration: '0.7s',
                }}
              />
            ))}
          </div>
        )}

        {/* User speaking waveform dots */}
        {isSpeaking && !isChiefSpeaking && (
          <div className="flex items-end gap-1.5 h-8">
            {[0, 100, 50, 200, 125].map((delay, i) => (
              <div
                key={i}
                className="w-1.5 bg-white/90 rounded-full animate-bounce"
                style={{
                  height: [16, 28, 20, 32, 24][i],
                  animationDelay: `${delay}ms`,
                  animationDuration: '0.5s',
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ─── State label ──────────────────────────────────────────────────────────────

const STATE_LABELS: Record<VoiceState, string> = {
  idle: 'Ready',
  listening: 'Listening...',
  speaking: 'Thinking...',
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

  const [activeModel, setActiveModel] = useState<ActiveModel | null>(null)
  const [usage, setUsage] = useState<WsUsageEvent | null>(null)
  const [turnCount, setTurnCount] = useState(0)

  const scrollRef = useRef<HTMLDivElement>(null)
  const responseBuffer = useRef('')

  const audioQueueRef = useRef<ArrayBuffer[]>([])
  const isPlayingAudioRef = useRef(false)
  const currentAudioRef = useRef<HTMLAudioElement | null>(null)

  const stopAudioPlayback = useCallback(() => {
    audioQueueRef.current = []
    if (currentAudioRef.current) {
      currentAudioRef.current.pause()
      currentAudioRef.current = null
    }
    isPlayingAudioRef.current = false
  }, [])

  const playNextChunk = useCallback(async () => {
    if (isPlayingAudioRef.current || audioQueueRef.current.length === 0) return
    isPlayingAudioRef.current = true

    const chunk = audioQueueRef.current.shift()!
    try {
      const blob = new Blob([chunk], { type: 'audio/wav' })
      const url = URL.createObjectURL(blob)
      const audio = new Audio(url)
      currentAudioRef.current = audio
      audio.playbackRate = speed
      audio.onended = () => {
        URL.revokeObjectURL(url)
        currentAudioRef.current = null
        isPlayingAudioRef.current = false
        if (audioQueueRef.current.length > 0) playNextChunk()
        else setVoiceState('listening')
      }
      audio.onerror = () => {
        URL.revokeObjectURL(url)
        currentAudioRef.current = null
        isPlayingAudioRef.current = false
        if (audioQueueRef.current.length > 0) playNextChunk()
        else setVoiceState('listening')
      }
      await audio.play()
    } catch {
      currentAudioRef.current = null
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
    }, [conversationActive, playNextChunk]),
  })

  const { start: startVad, stop: stopVad, speaking: vadSpeaking, error: vadError } = useVad({
    enabled: conversationActive,
    onSpeechStart: useCallback(() => {
      // Barge-in: if Chief is speaking, cut audio immediately and switch to listening
      if (isPlayingAudioRef.current) {
        stopAudioPlayback()
      }
      setVoiceState('speaking')
    }, [stopAudioPlayback]),
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

  useEffect(() => {
    if (!conversationActive) {
      setVoiceState('idle')
    } else if (!vadSpeaking && voiceState === 'idle') {
      setVoiceState('listening')
    }
  }, [conversationActive, vadSpeaking, voiceState])

  async function handleStartConversation() {
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

  function handleTextSend(e: FormEvent) {
    e.preventDefault()
    if (!textInput.trim() || voiceState === 'thinking') return

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

      {/* Main content area */}
      {conversationActive ? (
        /* ── Active voice session layout ── */
        <div className="flex-1 flex flex-col min-h-0">
          {/* Orb area — fixed height so messages still scroll below */}
          <div className="flex flex-col items-center justify-center py-6 gap-3 shrink-0">
            <VoiceOrb state={voiceState} userSpeaking={vadSpeaking} />

            {/* State label under orb */}
            <p className={`text-sm font-medium transition-all duration-300 ${
              vadSpeaking
                ? 'text-amber-400'
                : voiceState === 'thinking'
                ? 'text-status-working'
                : voiceState === 'speaking'
                ? 'text-chief-light'
                : 'text-emerald-400'
            }`}>
              {getActiveLabel()}
            </p>

            {/* End call button — PhoneOff, clearly separate from orb */}
            <button
              onClick={handleEndConversation}
              className="mt-1 flex items-center gap-2 px-4 py-2 rounded-full bg-red-600/20 border border-red-600/40 text-red-400 hover:bg-red-600/30 active:scale-95 transition-all text-sm font-medium"
            >
              <PhoneOff size={15} />
              End call
            </button>
          </div>

          {/* Message history — scrollable below the orb */}
          <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-2 space-y-3">
            {messages.length === 0 && (
              <div className="flex items-center justify-center h-full">
                <p className="text-white/20 text-sm">Speak to start a conversation</p>
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
      ) : (
        /* ── Idle / inactive layout ── */
        <div className="flex-1 flex flex-col min-h-0">
          {/* Start voice affordance — center of screen */}
          <div className="flex flex-col items-center justify-center flex-1 gap-5">
            <button
              onClick={handleStartConversation}
              disabled={!isConnected}
              className="relative w-28 h-28 rounded-full flex items-center justify-center transition-all active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed bg-chief hover:bg-chief-dark shadow-lg"
              style={{ boxShadow: '0 0 40px rgba(99,102,241,0.25)' }}
            >
              <Mic size={36} className="text-white" />
            </button>
            <div className="text-center space-y-1">
              <p className="text-white/70 text-sm font-medium">Tap to start voice</p>
              <p className="text-white/30 text-xs">Always-listening conversation</p>
            </div>
            {vadError && (
              <p className="text-destructive text-xs">{vadError}</p>
            )}
            {!isConnected && (
              <p className="text-white/30 text-xs">Connecting to server...</p>
            )}

            {/* Past messages if any */}
            {messages.length > 0 && (
              <div ref={scrollRef} className="w-full max-h-40 overflow-y-auto px-4 space-y-2 mt-2">
                {messages.map((msg) => (
                  <div
                    key={msg.id}
                    className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
                  >
                    <div
                      className={`max-w-[85%] rounded-2xl px-3 py-2 ${
                        msg.role === 'user'
                          ? 'bg-chief text-white rounded-br-md'
                          : 'bg-surface-raised text-white/90 rounded-bl-md'
                      }`}
                    >
                      <p className="text-xs whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}

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
      <div className="px-4 pb-2 pt-3 bg-surface space-y-3">
        {/* Camera + Screenshot row — always visible */}
        <div className="flex items-center justify-center gap-4">
          <button
            onClick={handleCamera}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Camera size={18} />
          </button>

          <button
            onClick={handleScreenshot}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Monitor size={18} />
          </button>
        </div>

        {/* Speed control */}
        <div className="flex items-center justify-center gap-1">
          {speeds.map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className={`px-3 py-1 rounded-full text-xs font-medium transition-colors ${
                speed === s
                  ? 'bg-chief text-white'
                  : 'bg-surface-raised text-white/40 active:text-white/60'
              }`}
            >
              {s}x
            </button>
          ))}
        </div>

        {/* Text input */}
        <form onSubmit={handleTextSend} className="flex gap-2">
          <input
            type="text"
            value={textInput}
            onChange={(e) => setTextInput(e.target.value)}
            placeholder="Type a message..."
            className="flex-1 h-11 px-4 rounded-xl bg-surface-raised border border-surface-border text-white placeholder-white/30 text-sm focus:outline-none focus:border-chief transition-colors"
          />
          <button
            type="submit"
            disabled={!textInput.trim() || voiceState === 'thinking'}
            className="w-11 h-11 flex items-center justify-center rounded-xl bg-chief text-white disabled:opacity-30 active:scale-95 transition-all"
          >
            <Send size={16} />
          </button>
        </form>
      </div>
    </div>
  )
}
