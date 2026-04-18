import { useRef, useState, useCallback } from 'react'
import { MicVAD } from '@ricky0123/vad-web'

interface UseVadOptions {
  onSpeechEnd: (audio: Float32Array) => void
  onSpeechStart?: () => void
}

type VadStatus = 'idle' | 'starting' | 'listening' | 'error'

interface UseVadReturn {
  start: () => Promise<void>
  stop: () => void
  speaking: boolean
  error: string | null
  status: VadStatus
  frameCount: number
  speechStartCount: number
  speechEndCount: number
  lastAudioSamples: number
}

export function useVad({ onSpeechEnd, onSpeechStart }: UseVadOptions): UseVadReturn {
  const vadRef = useRef<MicVAD | null>(null)
  const [speaking, setSpeaking] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [status, setStatus] = useState<VadStatus>('idle')
  const [frameCount, setFrameCount] = useState(0)
  const [speechStartCount, setSpeechStartCount] = useState(0)
  const [speechEndCount, setSpeechEndCount] = useState(0)
  const [lastAudioSamples, setLastAudioSamples] = useState(0)

  const onSpeechEndRef = useRef(onSpeechEnd)
  onSpeechEndRef.current = onSpeechEnd
  const onSpeechStartRef = useRef(onSpeechStart)
  onSpeechStartRef.current = onSpeechStart

  const start = useCallback(async () => {
    // NB: don't early-return on `enabled` — the caller decides when to start,
    // and closing over `enabled` causes a stale-closure race where the first
    // tap runs with the pre-setConversationActive value and silently no-ops.
    if (vadRef.current) return

    setError(null)
    setStatus('starting')
    try {
      const vad = await MicVAD.new({
        baseAssetPath: '/vad/',
        onnxWASMBasePath: '/vad/',
        model: 'legacy',
        positiveSpeechThreshold: 0.5,
        negativeSpeechThreshold: 0.35,
        minSpeechFrames: 3,
        redemptionFrames: 6,
        additionalAudioConstraints: {
          sampleRate: { ideal: 16000 },
          sampleSize: { ideal: 16 },
        },
        onSpeechStart: () => {
          setSpeaking(true)
          setSpeechStartCount((n) => n + 1)
          onSpeechStartRef.current?.()
        },
        onSpeechEnd: (audio: Float32Array) => {
          setSpeaking(false)
          setSpeechEndCount((n) => n + 1)
          setLastAudioSamples(audio.length)
          onSpeechEndRef.current(audio)
        },
        onVADMisfire: () => {
          setSpeaking(false)
        },
        onFrameProcessed: () => {
          setFrameCount((n) => n + 1)
        },
        onSpeechRealStart: () => {},
      })
      vadRef.current = vad
      vad.start()
      setStatus('listening')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'VAD init failed'
      setError(msg)
      setStatus('error')
    }
  }, [])

  const stop = useCallback(() => {
    if (vadRef.current) {
      vadRef.current.destroy()
      vadRef.current = null
    }
    setSpeaking(false)
    setStatus('idle')
  }, [])

  return {
    start,
    stop,
    speaking,
    error,
    status,
    frameCount,
    speechStartCount,
    speechEndCount,
    lastAudioSamples,
  }
}
