// playback.js — AudioWorkletProcessor for raw 24 kHz PCM playback.
//
// Stage 1 of the Gemini Live API pivot.
//
// Strategy: a Float32 ring buffer fed by the main thread, drained at the
// native audio-context rate by process(). Main thread posts:
//   - { type: 'append', samples: Float32Array }  // 24 kHz mono
//   - { type: 'flush' }                          // instant cutoff for barge-in
//
// We post buffer-state telemetry back so the hook can compute isSpeaking.
//
// Capacity: 5 s @ 24 kHz = 120000 samples. Stored as Float32 = 480 KB.
// Plenty of headroom; old samples are overwritten if the main thread
// somehow outpaces playback (defensive — should never happen in practice
// since we only append what the server sends).

const RING_SAMPLES = 120000 // 5 s @ 24 kHz
const TELEMETRY_INTERVAL_FRAMES = 24 // ~50 Hz at 128-sample blocks @ 24 kHz, fine

class PlaybackProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._ring = new Float32Array(RING_SAMPLES)
    this._writeIdx = 0
    this._readIdx = 0
    this._available = 0 // samples currently buffered
    this._frameCount = 0

    this.port.onmessage = (e) => {
      const msg = e.data
      if (msg && msg.type === 'append' && msg.samples) {
        this._append(msg.samples)
      } else if (msg && msg.type === 'flush') {
        // Wipe everything — barge-in cutoff.
        this._writeIdx = 0
        this._readIdx = 0
        this._available = 0
        // Silence the ring so no stale samples can sneak through if read
        // somehow gets ahead before the next append.
        this._ring.fill(0)
        this.port.postMessage({ type: 'buffer-state', samples: 0 })
      }
    }
  }

  _append(samples) {
    for (let i = 0; i < samples.length; i++) {
      this._ring[this._writeIdx] = samples[i]
      this._writeIdx = (this._writeIdx + 1) % RING_SAMPLES
      if (this._available < RING_SAMPLES) {
        this._available++
      } else {
        // Ring is full — advance the read pointer too (overwrite oldest).
        this._readIdx = (this._readIdx + 1) % RING_SAMPLES
      }
    }
  }

  process(_inputs, outputs) {
    const output = outputs[0]
    if (!output || output.length === 0) return true
    const channel = output[0]
    const blockSize = channel.length

    for (let i = 0; i < blockSize; i++) {
      if (this._available > 0) {
        channel[i] = this._ring[this._readIdx]
        this._readIdx = (this._readIdx + 1) % RING_SAMPLES
        this._available--
      } else {
        channel[i] = 0
      }
    }

    // Mirror to any other channels (stereo etc.) — defensive; we expect mono out.
    for (let ch = 1; ch < output.length; ch++) {
      output[ch].set(channel)
    }

    this._frameCount++
    if (this._frameCount >= TELEMETRY_INTERVAL_FRAMES) {
      this._frameCount = 0
      this.port.postMessage({ type: 'buffer-state', samples: this._available })
    }

    return true
  }
}

registerProcessor('pcm-playback', PlaybackProcessor)
