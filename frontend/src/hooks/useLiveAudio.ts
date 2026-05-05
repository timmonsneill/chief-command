// useLiveAudio — Stage 1 of the Gemini Live API pivot.
//
// Captures raw 16 kHz mono Int16 PCM from the mic via AudioWorklet and plays
// back raw 24 kHz mono Int16 PCM from the server via a separate AudioWorklet
// with a ring buffer. Designed to slot into VoicePage in Stage 2; for now it
// powers the standalone /live-audio-test page.
//
// Why two AudioContexts: capture and playback need different sample rates
// (16 vs 24 kHz). One context per rate keeps each pipeline simple. The
// playback context MUST be resumed inside a user-gesture handler — start()
// is the natural place; consumers should always call it from a click.
//
// Why AudioWorklet (not ScriptProcessorNode): SPN is deprecated and broken
// on iOS Safari 17+. AudioWorklet is the only viable path on iOS.
//
// Sample-rate quirk: iOS Safari has historically ignored the requested
// sampleRate on AudioContext and given you 48000. We detect this and
// downsample with linear interpolation before posting to the consumer.

import { useCallback, useEffect, useRef, useState } from 'react'

const TARGET_CAPTURE_RATE = 16000
const TARGET_PLAYBACK_RATE = 24000
const CAPTURE_WINDOW_SAMPLES = 320 // 20 ms @ 16 kHz
const MIC_WORKLET_URL = '/audio-worklets/mic-capture.js'
const PLAYBACK_WORKLET_URL = '/audio-worklets/playback.js'

export type UseLiveAudioOptions = {
  /**
   * Called for every ~20 ms window of mic audio. The ArrayBuffer holds
   * 16 kHz mono Int16 PCM (little-endian, 640 bytes for a 320-sample window).
   * Stage 2 forwards this over the WebSocket.
   */
  onPcmChunk: (pcm: ArrayBuffer) => void
  onError?: (err: Error) => void
}

export type UseLiveAudioApi = {
  start: () => Promise<void>
  stop: () => void
  /**
   * Push a chunk of 24 kHz mono Int16 PCM (ArrayBuffer) into the playback
   * ring buffer. Latency is bounded by ring contents — keep chunks small.
   */
  playPcmChunk: (pcm: ArrayBuffer) => void
  /** Drop everything currently queued for playback (barge-in cutoff). */
  flushPlayback: () => void
  isMicActive: boolean
  isSpeaking: boolean
  /** 0–1 RMS of the most recent mic window, for level meters. */
  audioLevel: number
}

// --- helpers ----------------------------------------------------------------

function int16FromFloat32(input: Float32Array): ArrayBuffer {
  const out = new Int16Array(input.length)
  for (let i = 0; i < input.length; i++) {
    let s = input[i]
    if (s > 1) s = 1
    else if (s < -1) s = -1
    // Asymmetric scale: positive uses 32767, negative uses 32768.
    out[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767)
  }
  return out.buffer
}

function float32FromInt16(input: ArrayBuffer): Float32Array {
  const view = new Int16Array(input)
  const out = new Float32Array(view.length)
  for (let i = 0; i < view.length; i++) {
    out[i] = view[i] / 32768
  }
  return out
}

/**
 * Linear-interpolation resample. Voice quality is fine — we don't need a
 * proper polyphase filter. Used only when the platform refuses to give us
 * a 16 kHz capture context (iOS Safari forces 48 kHz).
 */
function resampleLinear(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate) return input
  const ratio = fromRate / toRate
  const outLen = Math.floor(input.length / ratio)
  const out = new Float32Array(outLen)
  for (let i = 0; i < outLen; i++) {
    const srcIndex = i * ratio
    const i0 = Math.floor(srcIndex)
    const i1 = Math.min(i0 + 1, input.length - 1)
    const frac = srcIndex - i0
    out[i] = input[i0] * (1 - frac) + input[i1] * frac
  }
  return out
}

function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0
  let sum = 0
  for (let i = 0; i < samples.length; i++) {
    sum += samples[i] * samples[i]
  }
  return Math.sqrt(sum / samples.length)
}

// --- hook -------------------------------------------------------------------

export function useLiveAudio(opts: UseLiveAudioOptions): UseLiveAudioApi {
  const [isMicActive, setIsMicActive] = useState(false)
  const [isSpeaking, setIsSpeaking] = useState(false)
  const [audioLevel, setAudioLevel] = useState(0)

  // Keep callbacks in refs so internal handlers always see the latest version
  // without forcing the worklets to re-register.
  const onPcmChunkRef = useRef(opts.onPcmChunk)
  onPcmChunkRef.current = opts.onPcmChunk
  const onErrorRef = useRef(opts.onError)
  onErrorRef.current = opts.onError

  // Capture-side resources.
  const captureCtxRef = useRef<AudioContext | null>(null)
  const captureStreamRef = useRef<MediaStream | null>(null)
  const captureSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const captureWorkletRef = useRef<AudioWorkletNode | null>(null)

  // Playback-side resources.
  const playbackCtxRef = useRef<AudioContext | null>(null)
  const playbackWorkletRef = useRef<AudioWorkletNode | null>(null)

  // Track started-ness so stop() is idempotent and re-entrant.
  const startedRef = useRef(false)

  // ---------- start ---------------------------------------------------------

  const start = useCallback(async () => {
    if (startedRef.current) return
    startedRef.current = true

    try {
      // 1) Mic stream. Echo cancellation + noise suppression on, AGC off
      //    (Gemini Live handles its own gain; AGC fights with model VAD).
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          sampleRate: TARGET_CAPTURE_RATE,
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: false,
        },
      })
      captureStreamRef.current = stream

      // 2) Capture AudioContext. Try to force 16 kHz; iOS Safari may ignore
      //    the request and force 48 kHz, in which case we downsample.
      // Cross-browser AudioContext (webkit fallback retired but kept defensive).
      const AudioContextCtor =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
      if (!AudioContextCtor) {
        throw new Error('AudioContext is not supported in this browser')
      }

      let captureCtx: AudioContext
      try {
        captureCtx = new AudioContextCtor({ sampleRate: TARGET_CAPTURE_RATE })
      } catch {
        // Some browsers throw when the requested rate is unsupported.
        captureCtx = new AudioContextCtor()
      }
      captureCtxRef.current = captureCtx

      const captureRate = captureCtx.sampleRate
      // eslint-disable-next-line no-console
      console.info(`[useLiveAudio] capture context running at ${captureRate} Hz`)

      // 3) Load + instantiate the mic worklet.
      await captureCtx.audioWorklet.addModule(MIC_WORKLET_URL)
      const micNode = new AudioWorkletNode(captureCtx, 'mic-capture', {
        numberOfInputs: 1,
        numberOfOutputs: 0,
        channelCount: 1,
      })
      captureWorkletRef.current = micNode

      micNode.port.onmessage = (e) => {
        const msg = e.data as { type: string; samples?: Float32Array }
        if (msg.type !== 'pcm' || !msg.samples) return

        // The worklet always posts CAPTURE_WINDOW_SAMPLES samples at the
        // context's native rate. Downsample if the platform forced > 16 kHz.
        const downsampled =
          captureRate === TARGET_CAPTURE_RATE
            ? msg.samples
            : resampleLinear(msg.samples, captureRate, TARGET_CAPTURE_RATE)

        // Update level meter (RMS on the post-resample window).
        const level = rms(downsampled)
        setAudioLevel(level)

        const pcm = int16FromFloat32(downsampled)
        try {
          onPcmChunkRef.current(pcm)
        } catch (err) {
          // Swallow consumer errors so we don't kill the audio graph.
          // eslint-disable-next-line no-console
          console.error('[useLiveAudio] onPcmChunk threw:', err)
        }
      }

      const source = captureCtx.createMediaStreamSource(stream)
      captureSourceRef.current = source
      source.connect(micNode)
      // Note: micNode has 0 outputs — connecting to destination would be
      // both unnecessary and would cause a feedback loop. We rely on the
      // graph staying alive via the source connection alone.

      // 4) Playback context. 24 kHz, separate from capture.
      let playbackCtx: AudioContext
      try {
        playbackCtx = new AudioContextCtor({ sampleRate: TARGET_PLAYBACK_RATE })
      } catch {
        playbackCtx = new AudioContextCtor()
      }
      playbackCtxRef.current = playbackCtx
      // eslint-disable-next-line no-console
      console.info(`[useLiveAudio] playback context running at ${playbackCtx.sampleRate} Hz`)

      await playbackCtx.audioWorklet.addModule(PLAYBACK_WORKLET_URL)
      const playbackNode = new AudioWorkletNode(playbackCtx, 'pcm-playback', {
        numberOfInputs: 0,
        numberOfOutputs: 1,
        outputChannelCount: [1],
      })
      playbackWorkletRef.current = playbackNode

      playbackNode.port.onmessage = (e) => {
        const msg = e.data as { type: string; samples?: number }
        if (msg.type === 'buffer-state') {
          setIsSpeaking((msg.samples ?? 0) > 0)
        }
      }

      playbackNode.connect(playbackCtx.destination)

      // iOS Safari: even after construction, the context may be 'suspended'
      // until resume() is called from a gesture. start() is invoked from
      // the consumer's click handler, so this should succeed.
      if (captureCtx.state === 'suspended') {
        await captureCtx.resume()
      }
      if (playbackCtx.state === 'suspended') {
        await playbackCtx.resume()
      }

      setIsMicActive(true)
    } catch (err) {
      startedRef.current = false
      const error = err instanceof Error ? err : new Error(String(err))
      // eslint-disable-next-line no-console
      console.error('[useLiveAudio] start failed:', error)
      onErrorRef.current?.(error)
      // Best-effort cleanup of anything we created before the throw.
      teardown()
      throw error
    }
    // teardown is defined below; safe to reference because it's stable via useCallback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ---------- teardown / stop ----------------------------------------------

  const teardown = useCallback(() => {
    // Capture side.
    try {
      captureWorkletRef.current?.port.close()
    } catch {
      /* ignore */
    }
    try {
      captureWorkletRef.current?.disconnect()
    } catch {
      /* ignore */
    }
    captureWorkletRef.current = null

    try {
      captureSourceRef.current?.disconnect()
    } catch {
      /* ignore */
    }
    captureSourceRef.current = null

    captureStreamRef.current?.getTracks().forEach((t) => {
      try {
        t.stop()
      } catch {
        /* ignore */
      }
    })
    captureStreamRef.current = null

    if (captureCtxRef.current) {
      const ctx = captureCtxRef.current
      captureCtxRef.current = null
      ctx.close().catch(() => {
        /* ignore — context may already be closed */
      })
    }

    // Playback side.
    try {
      playbackWorkletRef.current?.port.close()
    } catch {
      /* ignore */
    }
    try {
      playbackWorkletRef.current?.disconnect()
    } catch {
      /* ignore */
    }
    playbackWorkletRef.current = null

    if (playbackCtxRef.current) {
      const ctx = playbackCtxRef.current
      playbackCtxRef.current = null
      ctx.close().catch(() => {
        /* ignore */
      })
    }

    setIsMicActive(false)
    setIsSpeaking(false)
    setAudioLevel(0)
  }, [])

  const stop = useCallback(() => {
    if (!startedRef.current) {
      // Still wipe state in case start() partially failed.
      teardown()
      return
    }
    startedRef.current = false
    teardown()
  }, [teardown])

  // ---------- playback API --------------------------------------------------

  const playPcmChunk = useCallback((pcm: ArrayBuffer) => {
    const node = playbackWorkletRef.current
    if (!node) {
      // Silently drop chunks that arrive before start() — Stage 2 should
      // never call playPcmChunk before start() resolves, but we don't want
      // a stray chunk to throw and kill the WS handler.
      return
    }
    const samples = float32FromInt16(pcm)
    // Transfer the underlying buffer to the worklet to avoid a copy.
    node.port.postMessage({ type: 'append', samples }, [samples.buffer])
  }, [])

  const flushPlayback = useCallback(() => {
    const node = playbackWorkletRef.current
    if (!node) return
    node.port.postMessage({ type: 'flush' })
    setIsSpeaking(false)
  }, [])

  // Cleanup on unmount.
  useEffect(() => {
    return () => {
      if (startedRef.current) {
        startedRef.current = false
        teardown()
      }
    }
  }, [teardown])

  return {
    start,
    stop,
    playPcmChunk,
    flushPlayback,
    isMicActive,
    isSpeaking,
    audioLevel,
  }
}

// Constants exported for the test page (sanity check on chunk size).
export const LIVE_AUDIO_CONSTANTS = {
  TARGET_CAPTURE_RATE,
  TARGET_PLAYBACK_RATE,
  CAPTURE_WINDOW_SAMPLES,
  /** Bytes per outbound mic chunk: 320 samples × 2 bytes/sample. */
  CAPTURE_CHUNK_BYTES: CAPTURE_WINDOW_SAMPLES * 2,
} as const
