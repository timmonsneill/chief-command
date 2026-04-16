import { useState, useRef, useEffect, useCallback, type FormEvent } from 'react'
import { Mic, Camera, Monitor, Send } from 'lucide-react'
import { useWebSocket } from '../hooks/useWebSocket'
import type { VoiceMessage, Agent } from '../lib/api'

export default function VoicePage() {
  const [messages, setMessages] = useState<VoiceMessage[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [isRecording, setIsRecording] = useState(false)
  const [isProcessing, setIsProcessing] = useState(false)
  const [textInput, setTextInput] = useState('')
  const [speed, setSpeed] = useState(1)
  const scrollRef = useRef<HTMLDivElement>(null)
  const mediaRecorderRef = useRef<MediaRecorder | null>(null)
  const chunksRef = useRef<Blob[]>([])

  // Accumulate streaming response chunks into a single message
  const responseBuffer = useRef('')

  // Audio playback — accumulate TTS chunks and play them
  const audioChunksRef = useRef<ArrayBuffer[]>([])
  const isPlayingAudioRef = useRef(false)

  const playAudioChunks = useCallback(async () => {
    if (isPlayingAudioRef.current || audioChunksRef.current.length === 0) return
    isPlayingAudioRef.current = true

    try {
      const combined = new Blob(audioChunksRef.current, { type: 'audio/wav' })
      audioChunksRef.current = []
      const url = URL.createObjectURL(combined)
      const audio = new Audio(url)
      audio.playbackRate = speed
      audio.onended = () => {
        URL.revokeObjectURL(url)
        isPlayingAudioRef.current = false
        // Play next batch if more arrived while playing
        if (audioChunksRef.current.length > 0) playAudioChunks()
      }
      audio.onerror = () => {
        URL.revokeObjectURL(url)
        isPlayingAudioRef.current = false
      }
      await audio.play()
    } catch {
      isPlayingAudioRef.current = false
    }
  }, [speed])

  const { send, isConnected } = useWebSocket({
    path: '/ws/voice',
    onBinary: useCallback((data: ArrayBuffer) => {
      // TTS audio chunk from backend
      audioChunksRef.current.push(data)
    }, []),
    onMessage: useCallback((data: string) => {
      try {
        const parsed = JSON.parse(data)

        if (parsed.type === 'transcript') {
          // Our speech was transcribed — show it as a user message
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

        if (parsed.type === 'response') {
          // Streaming chunk from Claude — accumulate
          responseBuffer.current += parsed.content
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

        if (parsed.type === 'tts_start') {
          // Response complete, finalize the streaming message
          setMessages((prev) =>
            prev.map((m) =>
              m.id === 'streaming' ? { ...m, id: crypto.randomUUID() } : m
            )
          )
          responseBuffer.current = ''
          setIsProcessing(false)
        }

        if (parsed.type === 'tts_end') {
          // All TTS audio received — play it
          playAudioChunks()
          setIsProcessing(false)
          responseBuffer.current = ''
        }

        if (parsed.type === 'agent_status') {
          setAgents(
            (parsed.agents || []).map((a: Record<string, string>, i: number) => ({
              id: `agent-${i}`,
              name: a.name || 'Agent',
              task: a.last_output || a.role || '',
              status: a.status === 'running' ? 'working' : a.status,
            }))
          )
        }

        if (parsed.type === 'error') {
          setMessages((prev) => [
            ...prev,
            {
              id: crypto.randomUUID(),
              role: 'assistant',
              content: `Error: ${parsed.message}`,
              timestamp: new Date().toISOString(),
            },
          ])
          setIsProcessing(false)
        }
      } catch {
        // ignore non-JSON (binary audio frames handled separately)
      }
    }, []),
  })

  // Auto-scroll on new messages
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [messages])

  // --- Persistent mic stream — request once, reuse ---
  const streamRef = useRef<MediaStream | null>(null)

  async function getMicStream(): Promise<MediaStream> {
    if (streamRef.current && streamRef.current.active) {
      return streamRef.current
    }
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
    streamRef.current = stream
    return stream
  }

  // --- Recording ---
  async function toggleRecording() {
    if (isRecording) {
      // Stop recording
      mediaRecorderRef.current?.stop()
      setIsRecording(false)
      return
    }

    try {
      const stream = await getMicStream()

      // Safari doesn't support audio/webm — fall back to default
      const mimeType = MediaRecorder.isTypeSupported('audio/webm')
        ? 'audio/webm'
        : ''
      const recorder = mimeType
        ? new MediaRecorder(stream, { mimeType })
        : new MediaRecorder(stream)
      mediaRecorderRef.current = recorder
      chunksRef.current = []

      recorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data)
      }

      recorder.onstop = async () => {
        const blob = new Blob(chunksRef.current, { type: recorder.mimeType })

        // Send audio as binary blob via WebSocket
        const arrayBuffer = await blob.arrayBuffer()
        if (arrayBuffer.byteLength > 0) {
          send(arrayBuffer)
          setIsProcessing(true)

          // Safety timeout — reset processing after 30s if no response
          setTimeout(() => setIsProcessing(false), 30000)
        }
      }

      recorder.start()
      setIsRecording(true)
    } catch (err) {
      console.error('Mic access error:', err)
      // Don't leave in processing state
      setIsProcessing(false)
    }
  }

  // --- Text send ---
  function handleTextSend(e: FormEvent) {
    e.preventDefault()
    if (!textInput.trim() || isProcessing) return

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
    setIsProcessing(true)
  }

  // --- Camera ---
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
        send(
          JSON.stringify({
            type: 'image',
            data: reader.result as string,
          })
        )
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: 'user',
            content: '[Photo attached]',
            timestamp: new Date().toISOString(),
          },
        ])
        setIsProcessing(true)
      }
      reader.readAsDataURL(file)
    }
    input.click()
  }

  // --- Screenshot ---
  async function handleScreenshot() {
    try {
      const stream = await navigator.mediaDevices.getDisplayMedia({
        video: true,
      })
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
      setIsProcessing(true)
    } catch {
      // User cancelled or not supported
    }
  }

  // --- Speed labels ---
  const speeds = [0.75, 1, 1.25, 1.5]

  function formatTime(iso: string) {
    return new Date(iso).toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })
  }

  const workingAgents = agents.filter((a) => a.status === 'working')

  return (
    <div className="h-full flex flex-col">
      {/* Messages */}
      <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-3 space-y-3">
        {messages.length === 0 && (
          <div className="flex items-center justify-center h-full">
            <div className="text-center">
              <div className="text-white/20 text-sm">
                {isConnected
                  ? 'Tap the mic or type to start'
                  : 'Connecting...'}
              </div>
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
              <p className="text-sm whitespace-pre-wrap leading-relaxed">
                {msg.content}
              </p>
              <p
                className={`text-[10px] mt-1 ${
                  msg.role === 'user' ? 'text-white/50' : 'text-white/30'
                }`}
              >
                {formatTime(msg.timestamp)}
              </p>
            </div>
          </div>
        ))}

        {isProcessing && (
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
        {/* Mic row */}
        <div className="flex items-center justify-center gap-4">
          {/* Camera */}
          <button
            onClick={handleCamera}
            className="w-11 h-11 flex items-center justify-center rounded-full bg-surface-raised border border-surface-border text-white/50 active:text-white transition-colors"
          >
            <Camera size={18} />
          </button>

          {/* Mic button */}
          <button
            onClick={toggleRecording}
            disabled={isProcessing || !isConnected}
            className={`relative w-20 h-20 rounded-full flex items-center justify-center transition-all active:scale-95 disabled:opacity-40 disabled:cursor-not-allowed ${
              isRecording
                ? 'bg-red-500'
                : 'bg-chief hover:bg-chief-dark'
            }`}
          >
            {/* Pulse rings when recording */}
            {isRecording && (
              <>
                <span className="absolute inset-0 rounded-full bg-red-500/30 animate-ping" />
                <span className="absolute -inset-2 rounded-full border-2 border-red-500/20 animate-pulse" />
              </>
            )}
            <Mic size={28} className="text-white relative z-10" />
          </button>

          {/* Screenshot */}
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
            disabled={!textInput.trim() || isProcessing}
            className="w-11 h-11 flex items-center justify-center rounded-xl bg-chief text-white disabled:opacity-30 active:scale-95 transition-all"
          >
            <Send size={16} />
          </button>
        </form>
      </div>
    </div>
  )
}
