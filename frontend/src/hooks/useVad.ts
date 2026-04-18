import { useRef, useState, useCallback } from 'react'
import { MicVAD } from '@ricky0123/vad-web'

interface UseVadOptions {
  onSpeechEnd: (audio: Float32Array) => void
  onSpeechStart?: () => void
  enabled: boolean
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

export function useVad({ onSpeechEnd, onSpeechStart, enabled }: UseVadOptions): UseVadReturn {
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
    if (!enabled) return
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
        redemptionFrames: 8,
        additionalAudioConstraints: {
          sampleRate: { ideal: 16000 },
          sampleSize: { ideal: 16 },
        },
        onSpeechStart: () => {
          setSpeaking(true)
          setSpeechStartCount((n) => n + 1)
          console.log('[VAD] speech start')
          onSpeechStartRef.current?.()
        },
        onSpeechEnd: (audio: Float32Array) => {
          setSpeaking(false)
          setSpeechEndCount((n) => n + 1)
          setLastAudioSamples(audio.length)
          console.log('[VAD] speech end, samples=', audio.length)
          onSpeechEndRef.current(audio)
        },
        onVADMisfire: () => {
          setSpeaking(false)
          console.log('[VAD] misfire')
        },
        onFrameProcessed: () => {
          setFrameCount((n) => n + 1)
        },
        onSpeechRealStart: () => {},
      })
      vadRef.current = vad
      vad.start()
      setStatus('listening')
      console.log('[VAD] initialized and listening')
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'VAD init failed'
      console.error('[VAD] init error:', msg, err)
      setError(msg)
      setStatus('error')
    }
  }, [enabled])

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
