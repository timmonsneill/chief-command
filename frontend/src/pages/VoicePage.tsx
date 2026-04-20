import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, PhoneOff, ChevronDown } from 'lucide-react'
import Composer from './voice/Composer'
import { toast } from 'sonner'
import { useWebSocket } from '../hooks/useWebSocket'
import { useVad } from '../hooks/useVad'
import { useProjectContext } from '../hooks/useProjectContext'
import { UsageMeter } from '../components/UsageMeter'
import { SessionBadge } from '../components/SessionBadge'
import { TaskBubble, type TaskBubbleStatus } from '../components/TaskBubble'
import type { VoiceMessage, Agent, WsEvent, ActiveModel, WsUsageEvent } from '../lib/api'

type VoiceState = 'idle' | 'listening' | 'speaking' | 'thinking'

interface TaskState {
  id: string              // = task_id from backend (ISO timestamp, unique per dispatch)
  taskSpec: string
  repo: string
  startedAt: string
  status: TaskBubbleStatus
  stdoutLines: string[]
  exitCode?: number
  durationSeconds?: number
  summary?: string
  cancelReason?: string
}

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
  idle: 'text-ink/40',
  listening: 'text-emerald-600',
  speaking: 'text-primary',
  thinking: 'text-status-working',
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function VoicePage() {
  const [messages, setMessages] = useState<VoiceMessage[]>([])
  const [tasks, setTasks] = useState<Record<string, TaskState>>({})
  const [agents, setAgents] = useState<Agent[]>([])
  const [voiceState, setVoiceState] = useState<VoiceState>('idle')
  const [textInput, setTextInput] = useState('')
  const [speed, setSpeed] = useState(1)
  const [conversationActive, setConversationActive] = useState(false)
  const [showUsage, setShowUsage] = useState(false)

  const [activeModel, setActiveModel] = useState<ActiveModel | null>(null)
  const [usage, setUsage] = useState<WsUsageEvent | null>(null)
  const [turnCount, setTurnCount] = useState(0)

  const { current: currentProject, setContext: setProjectContext } = useProjectContext()

  const scrollRef = useRef<HTMLDivElement>(null)
  const responseBuffer = useRef('')
  // Tracks the id of the most recently-started task so the Cancel button on
  // the bubble UI can issue a {type:'cancel'} frame without dragging the
  // task_id through component props. Event routing itself uses parsed.task_id
  // from the frame — NOT this ref — so a late output from an older task can't
  // be misattributed to a newer one.
  const activeTaskIdRef = useRef<string | null>(null)

  const audioQueueRef = useRef<ArrayBuffer[]>([])
  const isPlayingAudioRef = useRef(false)
  // Tracks when Chief last started speaking — kept for future half-duplex
  // refinements (currently we block voice-barge-in entirely while audio plays).
  const ttsStartAtRef = useRef<number>(0)
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
      // Speed is applied server-side via Google TTS `speaking_rate` (proper
      // time-stretch, preserves pitch). Do NOT set playbackRate here —
      // that's what produced the chipmunk effect at > 1.0x.
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
  }, [])

  const { send, isConnected } = useWebSocket({
    path: '/ws/voice',
    onBinary: useCallback((data: ArrayBuffer) => {
      audioQueueRef.current.push(data)
    }, []),
    onMessage: useCallback((data: string) => {
      try {
        const parsed = JSON.parse(data) as WsEvent

        if (parsed.type === 'context_switched') {
          // Backend already updated scope — mirror it into the picker so the UI
          // matches and project-aware views re-fetch with the new scope.
          setProjectContext(parsed.project)
          toast.success(`Switched to ${parsed.project}`)
        }

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
          ttsStartAtRef.current = Date.now()
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

        // Dispatch Bridge: task_* frames always carry task_id. We key state
        // by id and route ALL subsequent events (output / complete / cancel)
        // by the id on the frame — never by "which task is currently active".
        // That way a late task_output from task A arriving after task B has
        // started doesn't get stamped onto B.
        if (parsed.type === 'task_started') {
          const id = parsed.task_id
          activeTaskIdRef.current = id
          setTasks((prev) => ({
            ...prev,
            [id]: {
              id,
              taskSpec: parsed.task_spec,
              repo: parsed.repo,
              startedAt: parsed.started_at,
              status: 'running',
              stdoutLines: [],
            },
          }))
          // While a task is running the voice orb should return to listening —
          // the task itself owns the "doing work" affordance via its bubble.
          setVoiceState(conversationActive ? 'listening' : 'idle')
        }

        if (parsed.type === 'task_output') {
          const id = parsed.task_id
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                stdoutLines: [...t.stdoutLines, parsed.text.replace(/\n$/, '')],
              },
            }
          })
        }

        if (parsed.type === 'task_complete') {
          const id = parsed.task_id
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                status: 'complete',
                exitCode: parsed.exit_code,
                durationSeconds: parsed.duration_seconds,
                summary: parsed.summary,
              },
            }
          })
          if (activeTaskIdRef.current === id) {
            activeTaskIdRef.current = null
          }
        }

        if (parsed.type === 'task_cancelled') {
          const id = parsed.task_id
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                status: 'cancelled',
                cancelReason: parsed.reason,
              },
            }
          })
          if (activeTaskIdRef.current === id) {
            activeTaskIdRef.current = null
          }
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
    }, [conversationActive, playNextChunk, stopAudioPlayback, setProjectContext]),
  })

  const { start: startVad, stop: stopVad, speaking: vadSpeaking } = useVad({
    onSpeechStart: useCallback(() => {
      // Real voice barge-in: if Chief is speaking, cut local audio AND tell
      // backend to stop generating. iOS/Chrome AEC on the mic (see useVad
      // constraints) filters Chief's own speaker audio so this fires on
      // real user speech, not echo. A 600ms grace period after TTS start
      // covers the initial speaker→mic priming before AEC converges.
      if (isPlayingAudioRef.current) {
        const sinceTtsMs = Date.now() - ttsStartAtRef.current
        if (sinceTtsMs < 600) return
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

  // Send TTS speed preference to backend whenever it changes (or on reconnect).
  // Applied server-side via Google TTS `speaking_rate` — proper time-stretch,
  // preserves pitch. Frontend no longer sets AudioBufferSource.playbackRate.
  useEffect(() => {
    if (!isConnected) return
    send(JSON.stringify({ type: 'speed', value: speed }))
  }, [isConnected, speed, send])

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


  function formatTime(iso: string) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const workingAgents = agents.filter((a) => a.status === 'working')

  // Interleave messages and task bubbles by timestamp for a single chronological
  // timeline. task.id === task_id which is the started_at ISO timestamp key.
  type TimelineItem =
    | { kind: 'message'; ts: string; msg: VoiceMessage }
    | { kind: 'task'; ts: string; task: TaskState }
  const timeline: TimelineItem[] = [
    ...messages.map((m): TimelineItem => ({ kind: 'message', ts: m.timestamp, msg: m })),
    ...Object.values(tasks).map((t): TimelineItem => ({ kind: 'task', ts: t.startedAt, task: t })),
  ].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())

  const handleCancelTask = () => {
    send(JSON.stringify({ type: 'cancel' }))
  }

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
            <span className="text-xs text-ink/30">Connecting...</span>
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
              className="p-1 rounded-md text-ink/30 hover:text-ink/60 transition-colors"
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
          <div className="flex items-center justify-center gap-3 px-4 py-2 border-b border-surface-border bg-surface-overlay shrink-0">
            <span className={`text-xs font-medium transition-colors ${
              vadSpeaking
                ? 'text-accent-dark'
                : voiceState === 'thinking'
                ? 'text-status-working'
                : voiceState === 'speaking'
                ? 'text-primary'
                : 'text-emerald-600'
            }`}>
              {getActiveLabel()}
            </span>
            <button
              onClick={handleEndConversation}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-red-50 border border-red-200 text-red-700 hover:bg-red-100 active:scale-95 transition-all text-xs font-semibold"
            >
              <PhoneOff size={12} />
              End call
            </button>
          </div>
        )}

        {/* Message history — full-height scroll area. Interleaves chat messages
            and dispatched-task bubbles by timestamp so the conversation shows
            tasks inline where they were issued. */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
          {timeline.length === 0 && (
            <div className="flex flex-col items-center justify-center h-full gap-2 text-center">
              <p className="text-ink/40 text-sm font-medium">
                {conversationActive ? 'Speak to start a conversation' : 'Tap the mic to talk, or type a message'}
              </p>
              {!isConnected && (
                <p className="text-ink/30 text-xs">Connecting to server…</p>
              )}
            </div>
          )}

          {timeline.map((item) => {
            if (item.kind === 'task') {
              const t = item.task
              return (
                <TaskBubble
                  key={`task-${t.id}`}
                  taskSpec={t.taskSpec}
                  startedAt={t.startedAt}
                  status={t.status}
                  repo={t.repo}
                  exitCode={t.exitCode}
                  durationSeconds={t.durationSeconds}
                  summary={t.summary}
                  cancelReason={t.cancelReason}
                  stdoutLines={t.stdoutLines}
                  onCancel={t.status === 'running' ? handleCancelTask : undefined}
                />
              )
            }
            const msg = item.msg
            return (
              <div
                key={msg.id}
                className={`flex ${msg.role === 'user' ? 'justify-end' : 'justify-start'}`}
              >
                <div
                  className={`max-w-[85%] rounded-2xl px-4 py-2.5 ${
                    msg.role === 'user'
                      ? 'bg-primary text-white rounded-br-md'
                      : 'bg-surface-raised border border-surface-border text-ink/90 rounded-bl-md'
                  }`}
                >
                  <p className="text-sm whitespace-pre-wrap leading-relaxed">{msg.content}</p>
                  <p className={`text-[10px] mt-1 ${msg.role === 'user' ? 'text-white/70' : 'text-ink/30'}`}>
                    {formatTime(msg.timestamp)}
                  </p>
                </div>
              </div>
            )
          })}

          {voiceState === 'thinking' && (
            <div className="flex justify-start">
              <div className="bg-surface-raised rounded-2xl rounded-bl-md px-4 py-3">
                <div className="flex gap-1">
                  <div className="w-2 h-2 bg-ink/30 rounded-full animate-bounce [animation-delay:0ms]" />
                  <div className="w-2 h-2 bg-ink/30 rounded-full animate-bounce [animation-delay:150ms]" />
                  <div className="w-2 h-2 bg-ink/30 rounded-full animate-bounce [animation-delay:300ms]" />
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
              className="flex items-center gap-2 text-xs text-ink/60 animate-[fadeIn_0.3s_ease-out]"
            >
              <div className="w-1.5 h-1.5 rounded-full bg-status-working animate-pulse" />
              <span className="font-medium text-ink/80">{agent.name}:</span>
              <span className="truncate">{agent.task}</span>
            </div>
          ))}
        </div>
      )}

      {/* Bottom controls — mic button outside Composer, camera/screenshot/speed
          live inside Composer's + menu so they're only visible when needed. */}
      <div className="px-4 pb-2 pt-2 bg-surface">
        <div className="flex gap-2 items-center">
          <button
            type="button"
            onClick={handleToggleVoice}
            disabled={!isConnected}
            aria-label={conversationActive ? 'End voice conversation' : 'Start voice conversation'}
            className={`w-12 h-12 shrink-0 flex items-center justify-center rounded-2xl border transition-all active:scale-95 disabled:opacity-30 ${
              !conversationActive
                ? 'bg-surface-raised border-ink/15 text-ink/70 hover:text-ink hover:border-primary/40'
                : vadSpeaking
                ? 'bg-accent/15 border-accent text-accent-dark animate-pulse'
                : voiceState === 'speaking'
                ? 'bg-primary/15 border-primary text-primary animate-pulse'
                : voiceState === 'thinking'
                ? 'bg-status-working/15 border-status-working text-status-working'
                : 'bg-primary text-white border-primary-dark shadow-card'
            }`}
          >
            <Mic size={20} />
          </button>
          <div className="flex-1 min-w-0">
            <Composer
              value={textInput}
              onChange={setTextInput}
              onSubmit={handleTextSend}
              onCamera={handleCamera}
              onScreenshot={handleScreenshot}
              disabled={voiceState === 'thinking'}
              speed={speed}
              onSpeedChange={setSpeed}
            />
          </div>
        </div>

      </div>
    </div>
  )
}
