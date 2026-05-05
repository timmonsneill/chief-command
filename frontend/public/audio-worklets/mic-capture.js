// mic-capture.js — AudioWorkletProcessor for raw mic capture.
//
// Stage 1 of the Gemini Live API pivot.
//
// Input  : Float32 mono samples from the mic graph at the AudioContext rate
//          (target 16000 Hz; if the platform forces 48000 the main thread
//          will downsample after receiving these chunks).
// Output : posts ~20 ms windows of Float32 samples back to the main thread
//          via this.port.postMessage. Main thread converts to Int16 PCM
//          and pushes over the WebSocket.
//
// Why a worklet (not ScriptProcessorNode): SPN is deprecated and broken on
// iOS Safari 17+. AudioWorklet is the only path that actually works on iOS
// for low-latency capture.

// Window size in samples at 16 kHz mono = 20 ms = 320 samples.
// At higher AudioContext sample rates this still posts every 320 samples;
// the main thread downsamples afterwards so the on-the-wire chunk size is
// consistent.
const WINDOW_SAMPLES = 320

class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super()
    this._buffer = new Float32Array(WINDOW_SAMPLES)
    this._bufferIndex = 0
  }

  process(inputs) {
    const input = inputs[0]
    if (!input || input.length === 0) {
      // No input connected yet — keep the processor alive.
      return true
    }
    const channel = input[0]
    if (!channel) return true

    // Append into the rolling buffer; flush on every full window.
    for (let i = 0; i < channel.length; i++) {
      this._buffer[this._bufferIndex++] = channel[i]
      if (this._bufferIndex >= WINDOW_SAMPLES) {
        // Copy out the window so the next process() can keep filling fresh storage.
        const out = new Float32Array(WINDOW_SAMPLES)
        out.set(this._buffer)
        this.port.postMessage({ type: 'pcm', samples: out }, [out.buffer])
        this._bufferIndex = 0
      }
    }
    return true
  }
}

registerProcessor('mic-capture', MicCaptureProcessor)
