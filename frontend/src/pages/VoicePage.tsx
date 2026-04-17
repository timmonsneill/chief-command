import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, MicOff, Send, Camera, Monitor, ChevronDown } from 'lucide-react'
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

  const STATE_LABELS: Record<VoiceState, string> = {
    idle: 'Ready',
    listening: 'Listening...',
    speaking: 'Speaking...',
    thinking: 'Thinking...',
  }

  const STATE_COLORS: Record<VoiceState, string> = {
    idle: 'text-white/30',
    listening: 'text-emerald-400',
    speaking: 'text-chief',
    thinking: 'text-status-working',
  }

  const speeds = [0.75, 1, 1.25, 1.5]

  function formatTime(iso: string) {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  }

  const workingAgents = agents.filter((a) => a.status === 'working')

  return (
    <div className="h-full flex flex-col">
      {/* Header strip */}
      <div className="flex items-center justify-between px-4 py-2 border-b border-surface-border bg-surface">
        <div className="flex items-center gap-3">
          <span className={`text-xs font-medium ${STATE_COLORS[voiceState]}`}>
            {STATE_LABELS[voiceState]}
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

      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center space-y-2">
              <div className="text-white/20 text-sm">
                {conversationActive
                  ? 'Speak to start a conversation'
                  : 'Tap "Start conversation" to begin'}
              </div>
              {vadError && (
                <div className="text-destructive text-xs">{vadError}</div>
              )}
            </div>
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

      {/* Controls area */}
      <div className="px-4 pb-2 pt-3 bg-surface space-y-3">
        {/* Main voice control row */}
        <div className="flex items-center justify-center gap-4">
          {/* Camera */}
          <button
            onClick={handleCamera}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Camera size={18} />
          </button>

          {/* Start/End conversation button */}
          {!conversationActive ? (
            <button
              onClick={handleStartConversation}
              disabled={!isConnected}
              className="relative w-20 h-20 rounded-full flex items-center justify-center transition-all active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed bg-chief hover:bg-chief-dark"
            >
              <Mic size={28} className="text-white relative z-10" />
            </button>
          ) : (
            <button
              onClick={handleEndConversation}
              className={`relative w-20 h-20 rounded-full flex items-center justify-center transition-all active:scale-95 ${
                voiceState === 'speaking'
                  ? 'bg-red-500'
                  : voiceState === 'listening'
                  ? 'bg-emerald-600'
                  : 'bg-surface-raised border-2 border-surface-border'
              }`}
            >
              {voiceState === 'listening' && (
                <>
                  <span className="absolute inset-0 rounded-full bg-emerald-500/30 animate-ping" />
                  <span className="absolute -inset-2 rounded-full border-2 border-emerald-500/20 animate-pulse" />
                </>
              )}
              <MicOff size={28} className="text-white relative z-10" />
            </button>
          )}

          {/* Screenshot */}
          <button
            onClick={handleScreenshot}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Monitor size={18} />
          </button>
        </div>

        {!conversationActive && (
          <div className="text-center text-white/30 text-xs">
            Tap mic to start always-listening conversation
          </div>
        )}

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
