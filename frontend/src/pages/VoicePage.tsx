import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, PhoneOff, ChevronDown } from 'lucide-react'
import Composer from './voice/Composer'
import { toast } from 'sonner'
import { useWebSocket } from '../hooks/useWebSocket'
import { useLiveAudio } from '../hooks/useLiveAudio'
import { useProjectContext } from '../hooks/useProjectContext'
import { UsageMeter } from '../components/UsageMeter'
import { SessionBadge } from '../components/SessionBadge'
import { type TaskBubbleStatus } from '../components/TaskBubble'
import { InlineTaskActivity } from '../components/InlineTaskActivity'
import { ToolCallChip, type ToolCallStatus } from '../components/ToolCallChip'
import { ThinkingDots } from '../components/ThinkingDots'
import type { VoiceMessage, Agent, ActiveModel, WsUsageEvent } from '../lib/api'

// VoiceState in Stage-2 is a thin derived label — `useLiveAudio` owns the
// real audio state (isMicActive / isSpeaking). We only synthesize an
// "awaiting" pseudo-state for the gap between owner-speech-end and the
// first inbound audio chunk so the thinking dots have a place to live.
type VoiceLabel = 'idle' | 'listening' | 'speaking' | 'thinking'

// Transient STT failures the backend used to surface verbatim ("Could
// not transcribe audio" when wind/noise tripped VAD). Live's server-side
// VAD eliminates most of these but we keep the dedup belt-and-braces in
// case the backend ever leaks one through during reconnect / fallback.
const TRANSIENT_ERROR_PATTERN = /could not transcribe/i

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

// Gemini brain tool calls. Backend emits one frame on tool dispatch
// (status='running') and one on return (status terminal); the frames are
// NOT correlated by id. We synthesize a stable FE id at running-time and
// match terminal frames by name + JSON-shape of args, falling back to the
// most-recent-open chip with the same name. `argsKey` is cached on the
// running record so we don't re-stringify per terminal frame.
interface ToolCallState {
  id: string
  startedAt: string                  // ISO; controls timeline interleave
  name: string
  // Persona alias surfaced by the backend (Glass for code_review, etc.).
  // When present, ToolCallChip renders this instead of the raw tool ID.
  displayName?: string
  args?: Record<string, unknown>
  argsKey: string                    // JSON.stringify(args ?? {})
  status: ToolCallStatus
  durationMs?: number
  preview?: string
}

// ─── State label ──────────────────────────────────────────────────────────────

const STATE_LABELS: Record<VoiceLabel, string> = {
  idle: 'Ready',
  listening: 'Listening...',
  speaking: 'Chief is speaking...',
  thinking: 'Thinking...',
}

const STATE_COLORS: Record<VoiceLabel, string> = {
  idle: 'text-ink/40',
  listening: 'text-emerald-600',
  speaking: 'text-primary',
  thinking: 'text-status-working',
}

// ─── Stable bubble ids for streaming transcripts ──────────────────────────────
//
// We reuse a single id per role across an in-flight turn so message-list
// updates land on the same bubble instead of stacking. `generation_complete`
// finalizes by re-keying to a real uuid; `interrupted` discards any pending
// tail (replaces with a final uuid'd snapshot of what's been said so far).

const STREAMING_USER_ID = 'streaming-user'
const STREAMING_ASSISTANT_ID = 'streaming-assistant'

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function VoicePage() {
  const [messages, setMessages] = useState<VoiceMessage[]>([])
  const [tasks, setTasks] = useState<Record<string, TaskState>>({})
  const [toolCalls, setToolCalls] = useState<ToolCallState[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [textInput, setTextInput] = useState('')
  const [speed, setSpeed] = useState(1)
  const [conversationActive, setConversationActive] = useState(false)
  const [showUsage, setShowUsage] = useState(false)

  // True from owner-speech-end (input_transcript final) until the first
  // inbound audio chunk decodes (liveAudio.isSpeaking flips). Drives the
  // thinking dots — a Live-aware replacement for the old `thinkingState`
  // which keyed off token streams.
  const [awaitingReply, setAwaitingReply] = useState(false)
  // Reconnect hint surfaced from `go_away` frames (Stage 4 will implement
  // the actual session resume — for now we just show subtle UI feedback).
  const [reconnecting, setReconnecting] = useState(false)

  const [activeModel, setActiveModel] = useState<ActiveModel | null>(null)
  const [usage, setUsage] = useState<WsUsageEvent | null>(null)
  const [turnCount, setTurnCount] = useState(0)

  const { current: currentProject, setContext: setProjectContext } = useProjectContext()

  const scrollRef = useRef<HTMLDivElement>(null)
  // Tracks the id of the most recently-started task so the Cancel button on
  // the bubble UI can issue a {type:'cancel'} frame without dragging the
  // task_id through component props. Event routing itself uses parsed.task_id
  // from the frame — NOT this ref — so a late output from an older task can't
  // be misattributed to a newer one.
  const activeTaskIdRef = useRef<string | null>(null)
  // Monotonic counter for synthesizing tool-call chip ids. Backend doesn't
  // emit one — we generate `${name}#${seq}` at running-time and match terminal
  // frames by (name, argsKey) against the most-recent-open chip.
  const toolCallSeqRef = useRef(0)
  // Last-seen transient error timestamp (ms epoch). Caps visible
  // "Could not transcribe" chips at 1/min in case backend ever leaks one
  // through. Mostly defensive.
  const lastErrorAtRef = useRef<{ text: string; at: number } | null>(null)

  // ─── useLiveAudio wiring ───────────────────────────────────────────────
  // Outbound: every ~20ms PCM window posts to the WS as a binary frame.
  // Inbound (server -> playback): handled in useWebSocket onBinary below.
  // sendRef avoids re-creating useLiveAudio on every render of `send`.
  const sendRef = useRef<((data: string | ArrayBuffer | Blob) => boolean) | null>(null)

  const liveAudio = useLiveAudio({
    onPcmChunk: useCallback((pcm: ArrayBuffer) => {
      const send = sendRef.current
      if (!send) return
      send(pcm)
    }, []),
    onError: useCallback((err: Error) => {
      console.error('[voice] live audio error:', err)
      toast.error(`Mic error: ${err.message}`)
    }, []),
  })

  // ─── WebSocket ─────────────────────────────────────────────────────────

  const { send, isConnected } = useWebSocket({
    path: '/ws/voice',
    onBinary: useCallback((data: ArrayBuffer) => {
      // Inbound 24kHz Int16 PCM straight from the Live API. Push into
      // the playback worklet's ring buffer; isSpeaking flips inside
      // useLiveAudio when samples actually start coming out the speaker.
      liveAudio.playPcmChunk(data)
    }, [liveAudio]),
    onMessage: useCallback((data: string) => {
      let parsed: { type: string; [k: string]: unknown }
      try {
        parsed = JSON.parse(data)
      } catch {
        return
      }

      switch (parsed.type) {
        case 'context_switched': {
          const project = String(parsed.project ?? '')
          setProjectContext(project)
          toast.success(`Switched to ${project}`)
          break
        }

        case 'input_transcript': {
          // Live owner speech transcription. `is_final` flips once the
          // model commits the segment. We keep it in a single bubble
          // (id=STREAMING_USER_ID) so partials don't stack, then re-key
          // to a uuid on final so the next turn opens a fresh bubble.
          const text = String(parsed.text ?? '')
          const isFinal = Boolean(parsed.is_final)
          if (!text && !isFinal) break

          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'user' && last.id === STREAMING_USER_ID) {
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  content: text,
                  ...(isFinal ? { id: crypto.randomUUID() } : {}),
                },
              ]
            }
            return [
              ...prev,
              {
                id: isFinal ? crypto.randomUUID() : STREAMING_USER_ID,
                role: 'user',
                content: text,
                timestamp: new Date().toISOString(),
              },
            ]
          })

          // Owner finished an utterance — arm the dots until Chief starts
          // speaking (or replies via output_transcript first; both clear).
          if (isFinal) {
            setAwaitingReply(true)
          }
          break
        }

        case 'output_transcript': {
          // Live transcript of Chief's spoken reply. Same single-bubble
          // pattern as input_transcript.
          const text = String(parsed.text ?? '')
          const isFinal = Boolean(parsed.is_final)
          if (!text && !isFinal) break

          // Either path counts as "Chief is now responding" — clear the
          // dots even if the audio chunks haven't started flowing yet.
          setAwaitingReply(false)

          setMessages((prev) => {
            const last = prev[prev.length - 1]
            if (last && last.role === 'assistant' && last.id === STREAMING_ASSISTANT_ID) {
              return [
                ...prev.slice(0, -1),
                {
                  ...last,
                  content: text,
                  ...(isFinal ? { id: crypto.randomUUID() } : {}),
                },
              ]
            }
            return [
              ...prev,
              {
                id: isFinal ? crypto.randomUUID() : STREAMING_ASSISTANT_ID,
                role: 'assistant',
                content: text,
                timestamp: new Date().toISOString(),
              },
            ]
          })
          break
        }

        case 'interrupted': {
          // Owner barged in — drop any audio still queued for playback so
          // Chief stops mid-sentence. Re-key any in-flight bubbles so the
          // partial transcript lands as a permanent message rather than
          // disappearing into the next turn's stream.
          liveAudio.flushPlayback()
          setAwaitingReply(false)
          setMessages((prev) =>
            prev.map((m) =>
              m.id === STREAMING_USER_ID || m.id === STREAMING_ASSISTANT_ID
                ? { ...m, id: crypto.randomUUID() }
                : m
            )
          )
          // Race guard: backend may not emit a terminal tool_call frame for
          // an in-flight chip on interrupt. Mark any still-running chips
          // as cancelled so the UI doesn't spin forever.
          setToolCalls((prev) =>
            prev.map((tc) => (tc.status === 'running' ? { ...tc, status: 'cancelled' } : tc))
          )
          break
        }

        case 'generation_complete': {
          // Chief's turn is fully done (audio + text). Finalize any
          // streaming bubbles by giving them permanent uuids so the next
          // turn starts with a clean slate.
          setMessages((prev) =>
            prev.map((m) =>
              m.id === STREAMING_USER_ID || m.id === STREAMING_ASSISTANT_ID
                ? { ...m, id: crypto.randomUUID() }
                : m
            )
          )
          setAwaitingReply(false)
          break
        }

        case 'session_resumed': {
          // Stage 4 wires actual reconnect; for now just clear the hint
          // and log. Frame includes a `handle` we'll persist later.
          console.info('[voice] session resumed', parsed.handle)
          setReconnecting(false)
          break
        }

        case 'go_away': {
          // Server is about to close this socket (Live session quota /
          // server-side rotation). Stage 4 will implement actual resume;
          // for now show a subtle reconnect hint.
          console.info('[voice] go_away', parsed.time_left)
          setReconnecting(true)
          break
        }

        case 'usage': {
          setUsage(parsed as unknown as WsUsageEvent)
          setTurnCount((n) => n + 1)
          break
        }

        case 'active_model': {
          // Optional Live frame — backend may still emit it for the
          // session badge. Leave as-is; if it's dropped, badge keeps last.
          const model = (parsed.model as ActiveModel | undefined) ?? null
          if (model) setActiveModel(model)
          break
        }

        case 'agent_status': {
          const list = (parsed.agents as Record<string, string>[] | undefined) ?? []
          setAgents(
            list.map((a, i) => ({
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
          break
        }

        // ─── Dispatch Bridge frames (unchanged contract) ─────────────────
        // Always carry task_id; route by id, never by "currently active".

        case 'task_started': {
          const id = String(parsed.task_id)
          activeTaskIdRef.current = id
          setTasks((prev) => ({
            ...prev,
            [id]: {
              id,
              taskSpec: String(parsed.task_spec ?? ''),
              repo: String(parsed.repo ?? ''),
              startedAt: String(parsed.started_at ?? ''),
              status: 'running',
              stdoutLines: [],
            },
          }))
          break
        }

        case 'task_output': {
          const id = String(parsed.task_id)
          const text = String(parsed.text ?? '')
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                stdoutLines: [...t.stdoutLines, text.replace(/\n$/, '')],
              },
            }
          })
          break
        }

        case 'task_complete': {
          const id = String(parsed.task_id)
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                status: 'complete',
                exitCode: Number(parsed.exit_code),
                durationSeconds: Number(parsed.duration_seconds),
                summary: String(parsed.summary ?? ''),
              },
            }
          })
          if (activeTaskIdRef.current === id) {
            activeTaskIdRef.current = null
          }
          break
        }

        case 'task_cancelled': {
          const id = String(parsed.task_id)
          setTasks((prev) => {
            const t = prev[id]
            if (!t) return prev
            return {
              ...prev,
              [id]: {
                ...t,
                status: 'cancelled',
                cancelReason: String(parsed.reason ?? ''),
              },
            }
          })
          if (activeTaskIdRef.current === id) {
            activeTaskIdRef.current = null
          }
          break
        }

        // ─── Gemini brain tool-call frames (Phase 2 / Stage 3) ───────────
        case 'tool_call': {
          const name = String(parsed.name)
          const displayName =
            typeof parsed.display_name === 'string' && parsed.display_name
              ? parsed.display_name
              : undefined
          const args = parsed.args as Record<string, unknown> | undefined
          const status = parsed.status as ToolCallStatus
          const argsKey = JSON.stringify(args ?? {})

          if (status === 'running') {
            const seq = ++toolCallSeqRef.current
            const id = `${name}#${seq}`
            const startedAt = new Date().toISOString()
            setToolCalls((prev) => [
              ...prev,
              { id, startedAt, name, displayName, args, argsKey, status: 'running' },
            ])
          } else {
            setToolCalls((prev) => {
              let matchIdx = -1
              for (let i = prev.length - 1; i >= 0; i--) {
                const c = prev[i]
                if (c.status !== 'running' || c.name !== name) continue
                if (c.argsKey === argsKey) {
                  matchIdx = i
                  break
                }
              }
              if (matchIdx === -1) {
                for (let i = prev.length - 1; i >= 0; i--) {
                  const c = prev[i]
                  if (c.status === 'running' && c.name === name) {
                    matchIdx = i
                    break
                  }
                }
              }
              const durationMs = parsed.duration_ms as number | undefined
              const preview = parsed.preview as string | undefined
              if (matchIdx === -1) {
                const seq = ++toolCallSeqRef.current
                return [
                  ...prev,
                  {
                    id: `${name}#${seq}`,
                    startedAt: new Date().toISOString(),
                    name,
                    displayName,
                    args,
                    argsKey,
                    status,
                    durationMs,
                    preview,
                  },
                ]
              }
              const next = prev.slice()
              // Prefer terminal frame's displayName if backend includes it,
              // otherwise keep what we recorded on the running frame.
              next[matchIdx] = {
                ...next[matchIdx],
                displayName: displayName ?? next[matchIdx].displayName,
                status,
                durationMs,
                preview,
              }
              return next
            })
          }
          break
        }

        case 'error': {
          const text = String(parsed.message ?? '')
          if (TRANSIENT_ERROR_PATTERN.test(text)) {
            console.warn('[voice] suppressed transient STT error:', text)
            setAwaitingReply(false)
            return
          }
          const now = Date.now()
          const last = lastErrorAtRef.current
          if (last && last.text === text && now - last.at < 60_000) {
            console.warn('[voice] deduped repeat error within 60s:', text)
            setAwaitingReply(false)
            return
          }
          lastErrorAtRef.current = { text, at: now }
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: `Error: ${text}`,
              timestamp: new Date().toISOString(),
            },
          ])
          setAwaitingReply(false)
          break
        }

        default:
          // Unknown frame types are ignored — Stage 3/4 will add more.
          break
      }
    }, [liveAudio, setProjectContext]),
  })

  // Keep the latest send fn in a ref so onPcmChunk (defined before the
  // hook returns `send`) can reach it without recreating the hook.
  useEffect(() => {
    sendRef.current = send
  }, [send])

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // WS dropped (network blip / backend restart). Flush playback so a
  // half-finished Chief reply doesn't keep playing into the auto-
  // reconnected session, and clear the awaiting-reply hint.
  useEffect(() => {
    if (isConnected) return
    liveAudio.flushPlayback()
    setAwaitingReply(false)
  }, [isConnected, liveAudio])

  // Send project context frame to backend whenever the context changes or
  // WS connects. Backend uses it to scope the system prompt.
  useEffect(() => {
    if (!isConnected) return
    send(JSON.stringify({ type: 'context', project: currentProject }))
  }, [isConnected, currentProject, send])

  // Speed control is a no-op on Live API (no TTS speed parameter), but
  // we still emit the frame for back-compat — server ignores cleanly.
  // Owner can flip a future "Use Google TTS" toggle to make it active.
  useEffect(() => {
    if (!isConnected) return
    send(JSON.stringify({ type: 'speed', value: speed }))
  }, [isConnected, speed, send])

  // ─── Conversation lifecycle ────────────────────────────────────────────

  async function handleStartConversation() {
    // CRITICAL on iOS Safari: useLiveAudio.start() MUST run inside the
    // click handler stack. Mounting on useEffect leaves the playback
    // AudioContext suspended and no inbound audio ever decodes.
    try {
      await liveAudio.start()
    } catch (err) {
      // Permission denied / no device / etc. — already toasted via onError.
      console.error('[voice] failed to start live audio:', err)
      return
    }
    setConversationActive(true)
  }

  function handleEndConversation() {
    liveAudio.stop()
    setConversationActive(false)
    setAwaitingReply(false)
    // Tell the backend to wrap up its Live session for this connection.
    // Server-side close also flushes any half-buffered transcript.
    send(JSON.stringify({ type: 'interrupt' }))
  }

  async function handleToggleVoice() {
    if (conversationActive) handleEndConversation()
    else await handleStartConversation()
  }

  function handleTextSend(e: FormEvent) {
    e.preventDefault()
    const trimmed = textInput.trim()
    if (!trimmed) return

    send(JSON.stringify({ type: 'text', content: trimmed }))

    setMessages((prev) => [
      ...prev,
      {
        id: crypto.randomUUID(),
        role: 'user',
        content: trimmed,
        timestamp: new Date().toISOString(),
      },
    ])
    setTextInput('')
    setAwaitingReply(true)
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
        setAwaitingReply(true)
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
      setAwaitingReply(true)
    } catch {
      // User cancelled or not supported
    }
  }

  function formatTime(iso: string) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const workingAgents = agents.filter((a) => a.status === 'working')

  // Interleave messages, task bubbles, and tool-call chips by timestamp for
  // a single chronological timeline. task.id === task_id which is the
  // started_at ISO timestamp key. Tool-call startedAt is FE-stamped at
  // running-time (or terminal-time if a stray late frame).
  type TimelineItem =
    | { kind: 'message'; ts: string; msg: VoiceMessage }
    | { kind: 'task'; ts: string; task: TaskState }
    | { kind: 'tool'; ts: string; tool: ToolCallState }
  const timeline: TimelineItem[] = [
    ...messages.map((m): TimelineItem => ({ kind: 'message', ts: m.timestamp, msg: m })),
    ...Object.values(tasks).map((t): TimelineItem => ({ kind: 'task', ts: t.startedAt, task: t })),
    ...toolCalls.map((tc): TimelineItem => ({ kind: 'tool', ts: tc.startedAt, tool: tc })),
  ].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())

  const handleCancelTask = () => {
    send(JSON.stringify({ type: 'cancel', action: 'task' }))
  }

  // ─── Derived label / state ─────────────────────────────────────────────
  // Live owns the source of truth — `isMicActive`, `isSpeaking`, and the
  // owner-derived `awaitingReply` flag combine into a single label for the
  // header strip + orb tint. Order of precedence:
  //   1. Chief speaking (audio actually flowing)
  //   2. Awaiting reply (you spoke, Chief hasn't started)
  //   3. Listening (mic open, room is quiet)
  //   4. Idle
  const voiceLabel: VoiceLabel = !conversationActive
    ? 'idle'
    : liveAudio.isSpeaking
      ? 'speaking'
      : awaitingReply
        ? 'thinking'
        : 'listening'

  // Active label sits under the orb when a call is in progress. We use
  // audioLevel as a soft "you're talking right now" hint without needing a
  // VAD — Live handles real turn detection server-side.
  const youAreTalking = conversationActive && liveAudio.audioLevel > 0.04 && !liveAudio.isSpeaking
  function getActiveLabel(): string {
    if (youAreTalking) return 'Listening to you...'
    if (voiceLabel === 'thinking') return 'Thinking...'
    if (voiceLabel === 'speaking') return 'Chief is speaking'
    return 'Listening...'
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header strip */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-surface">
        <div className="flex items-center gap-3">
          <span className={`text-xs font-medium ${STATE_COLORS[voiceLabel]}`}>
            {STATE_LABELS[voiceLabel]}
          </span>
          {!isConnected && (
            <span className="text-xs text-ink/30">Connecting...</span>
          )}
          {reconnecting && isConnected && (
            <span className="text-xs text-ink/40">Reconnecting...</span>
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

      {/* Main content area */}
      <div className="flex-1 flex flex-col min-h-0">
        {conversationActive && (
          <div className="flex items-center justify-center gap-3 px-4 py-2 border-b border-surface-border bg-surface-overlay shrink-0">
            <span className={`text-xs font-medium transition-colors ${
              youAreTalking
                ? 'text-accent-dark'
                : voiceLabel === 'thinking'
                ? 'text-status-working'
                : voiceLabel === 'speaking'
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

        {/* Message history */}
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
                <InlineTaskActivity
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
            if (item.kind === 'tool') {
              const tc = item.tool
              return (
                <ToolCallChip
                  key={`tool-${tc.id}`}
                  name={tc.name}
                  displayName={tc.displayName}
                  args={tc.args}
                  status={tc.status}
                  durationMs={tc.durationMs}
                  preview={tc.preview}
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

          {/* Thinking dots — armed when the owner just finished speaking
              (or sent text) and Chief hasn't started speaking back yet.
              Cleared the moment output_transcript or audio chunks land. */}
          {awaitingReply && !liveAudio.isSpeaking && <ThinkingDots />}
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

      {/* Bottom controls */}
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
                : youAreTalking
                ? 'bg-accent/15 border-accent text-accent-dark animate-pulse'
                : voiceLabel === 'speaking'
                ? 'bg-primary/15 border-primary text-primary animate-pulse'
                : voiceLabel === 'thinking'
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
              disabled={false}
              speed={speed}
              onSpeedChange={setSpeed}
            />
          </div>
        </div>

      </div>
    </div>
  )
}
