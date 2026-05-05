// LiveAudioTestPage — dev-only smoke test for useLiveAudio.
//
// Stage 1 verification surface. Not wired into production routing in
// Layout/sidebar — accessible only by typing /live-audio-test. Stage 2 will
// integrate the hook into VoicePage and this page can be deleted or left as
// a debug tool.

import { useCallback, useMemo, useRef, useState } from 'react'
import { LIVE_AUDIO_CONSTANTS, useLiveAudio } from '../hooks/useLiveAudio'

const MAX_LOG_LINES = 30

export default function LiveAudioTestPage() {
  const [log, setLog] = useState<string[]>([])
  const [chunkCount, setChunkCount] = useState(0)
  const startTimeRef = useRef<number | null>(null)

  const appendLog = useCallback((line: string) => {
    setLog((prev) => {
      const next = [...prev, `${new Date().toISOString().slice(11, 23)}  ${line}`]
      if (next.length > MAX_LOG_LINES) next.shift()
      return next
    })
  }, [])

  const handlePcmChunk = useCallback(
    (pcm: ArrayBuffer) => {
      // Cheap throttle — log every 25th chunk so the UI doesn't drown.
      setChunkCount((prev) => {
        const next = prev + 1
        if (next % 25 === 0) {
          const elapsed = startTimeRef.current ? (Date.now() - startTimeRef.current) / 1000 : 0
          const rate = elapsed > 0 ? (next / elapsed).toFixed(1) : '?'
          appendLog(`pcm chunk #${next}  bytes=${pcm.byteLength}  ~${rate}/sec`)
        }
        return next
      })
    },
    [appendLog]
  )

  const handleError = useCallback(
    (err: Error) => {
      appendLog(`ERROR: ${err.message}`)
    },
    [appendLog]
  )

  const audio = useLiveAudio({ onPcmChunk: handlePcmChunk, onError: handleError })

  const onStart = useCallback(async () => {
    appendLog('start() requested')
    setChunkCount(0)
    startTimeRef.current = Date.now()
    try {
      await audio.start()
      appendLog('start() resolved — mic active')
    } catch (err) {
      appendLog(`start() failed: ${(err as Error).message}`)
    }
  }, [audio, appendLog])

  const onStop = useCallback(() => {
    audio.stop()
    appendLog('stop() called')
  }, [audio, appendLog])

  const onInjectSine = useCallback(() => {
    if (!audio.isMicActive) {
      appendLog('cannot inject: start mic first (audio context needs gesture)')
      return
    }
    // 1 s of 440 Hz sine, 24 kHz mono Int16.
    const sampleRate = LIVE_AUDIO_CONSTANTS.TARGET_PLAYBACK_RATE
    const length = sampleRate
    const int16 = new Int16Array(length)
    const freq = 440
    const amp = 0.4
    for (let i = 0; i < length; i++) {
      const s = Math.sin((2 * Math.PI * freq * i) / sampleRate) * amp
      int16[i] = s < 0 ? Math.round(s * 32768) : Math.round(s * 32767)
    }
    appendLog(`injecting 1s 440Hz sine (${int16.length} samples)`)
    // Push in 10 chunks to mimic streaming.
    const chunkSize = Math.floor(length / 10)
    for (let c = 0; c < 10; c++) {
      const slice = int16.slice(c * chunkSize, (c + 1) * chunkSize)
      audio.playPcmChunk(slice.buffer)
    }
  }, [audio, appendLog])

  const onFlush = useCallback(() => {
    audio.flushPlayback()
    appendLog('flush() called')
  }, [audio, appendLog])

  // Dev-friendly status pill colors using existing tailwind tokens.
  const micPill = useMemo(
    () =>
      audio.isMicActive
        ? 'bg-status-online/15 text-status-online border-status-online/30'
        : 'bg-surface-raised text-ink/50 border-surface-border',
    [audio.isMicActive]
  )
  const speakingPill = useMemo(
    () =>
      audio.isSpeaking
        ? 'bg-accent/15 text-accent border-accent/30'
        : 'bg-surface-raised text-ink/50 border-surface-border',
    [audio.isSpeaking]
  )

  const levelPct = Math.min(100, Math.round(audio.audioLevel * 100 * 4)) // *4 because voice RMS is usually <0.25

  return (
    <div className="min-h-[100dvh] bg-surface px-6 py-10">
      <div className="max-w-2xl mx-auto">
        <h1 className="font-display text-3xl font-semibold text-ink tracking-tight">
          Live Audio test<span className="text-accent">.</span>
        </h1>
        <p className="text-sm text-ink/50 mt-1">
          Stage 1 smoke for <code>useLiveAudio</code>. Not wired into production routing.
        </p>

        {/* Status pills */}
        <div className="flex gap-2 mt-6">
          <span className={`px-3 py-1 rounded-full text-xs border ${micPill}`}>
            mic: {audio.isMicActive ? 'active' : 'idle'}
          </span>
          <span className={`px-3 py-1 rounded-full text-xs border ${speakingPill}`}>
            speaking: {audio.isSpeaking ? 'yes' : 'no'}
          </span>
          <span className="px-3 py-1 rounded-full text-xs border bg-surface-raised text-ink/60 border-surface-border">
            chunks: {chunkCount}
          </span>
        </div>

        {/* Mic level meter */}
        <div className="mt-4">
          <div className="text-[11px] uppercase tracking-wider text-ink/40 mb-1">mic level</div>
          <div className="h-3 w-full rounded-full bg-surface-raised border border-surface-border overflow-hidden">
            <div
              className="h-full bg-chief transition-[width] duration-75"
              style={{ width: `${levelPct}%` }}
            />
          </div>
          <div className="text-[11px] text-ink/40 mt-1">
            RMS: {audio.audioLevel.toFixed(4)} (×4 displayed)
          </div>
        </div>

        {/* Buttons */}
        <div className="grid grid-cols-2 gap-3 mt-6">
          <button
            onClick={onStart}
            disabled={audio.isMicActive}
            className="h-11 rounded-xl bg-chief text-white text-sm font-medium hover:bg-chief-dark active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            Start mic
          </button>
          <button
            onClick={onStop}
            disabled={!audio.isMicActive}
            className="h-11 rounded-xl bg-surface-raised border border-surface-border text-ink text-sm font-medium hover:bg-surface-border/40 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            Stop
          </button>
          <button
            onClick={onInjectSine}
            disabled={!audio.isMicActive}
            className="h-11 rounded-xl bg-accent text-white text-sm font-medium hover:bg-accent/90 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            Inject 1s sine
          </button>
          <button
            onClick={onFlush}
            disabled={!audio.isMicActive}
            className="h-11 rounded-xl bg-surface-raised border border-surface-border text-ink text-sm font-medium hover:bg-surface-border/40 active:scale-[0.98] disabled:opacity-40 disabled:cursor-not-allowed transition-all"
          >
            Flush playback
          </button>
        </div>

        {/* Log */}
        <div className="mt-6">
          <div className="text-[11px] uppercase tracking-wider text-ink/40 mb-1">log</div>
          <div className="rounded-xl bg-surface-raised border border-surface-border p-3 font-mono text-[11px] text-ink/80 h-64 overflow-y-auto whitespace-pre">
            {log.length === 0 ? (
              <span className="text-ink/30">no events yet</span>
            ) : (
              log.map((line, i) => <div key={i}>{line}</div>)
            )}
          </div>
        </div>

        <div className="mt-6 text-xs text-ink/40">
          expected: ~50 chunks/sec at {LIVE_AUDIO_CONSTANTS.CAPTURE_CHUNK_BYTES} B each (
          {LIVE_AUDIO_CONSTANTS.CAPTURE_WINDOW_SAMPLES} samples × 2 B Int16).
        </div>
      </div>
    </div>
  )
}
