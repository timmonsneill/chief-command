import { useRef, useState, useCallback } from 'react'
import { MicVAD } from '@ricky0123/vad-web'

interface UseVadOptions {
  onSpeechEnd: (audio: Float32Array) => void
  onSpeechStart?: () => void
  enabled: boolean
}

interface UseVadReturn {
  start: () => Promise<void>
  stop: () => void
  speaking: boolean
  error: string | null
}

export function useVad({ onSpeechEnd, onSpeechStart, enabled }: UseVadOptions): UseVadReturn {
  const vadRef = useRef<MicVAD | null>(null)
  const [speaking, setSpeaking] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const onSpeechEndRef = useRef(onSpeechEnd)
  onSpeechEndRef.current = onSpeechEnd
  const onSpeechStartRef = useRef(onSpeechStart)
  onSpeechStartRef.current = onSpeechStart

  const start = useCallback(async () => {
    if (!enabled) return
    if (vadRef.current) return

    setError(null)
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
          onSpeechStartRef.current?.()
        },
        onSpeechEnd: (audio: Float32Array) => {
          setSpeaking(false)
          onSpeechEndRef.current(audio)
        },
        onVADMisfire: () => {
          setSpeaking(false)
        },
        onFrameProcessed: () => {},
        onSpeechRealStart: () => {},
      })
      vadRef.current = vad
      vad.start()
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'VAD init failed'
      setError(msg)
    }
  }, [enabled])

  const stop = useCallback(() => {
    if (vadRef.current) {
      vadRef.current.destroy()
      vadRef.current = null
    }
    setSpeaking(false)
  }, [])

  return { start, stop, speaking, error }
}
