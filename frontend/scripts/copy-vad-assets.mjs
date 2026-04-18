#!/usr/bin/env node
// Copies the VAD runtime assets from node_modules into public/vad/ so Vite
// serves them at /vad/*. Fixes the latent gitignore footgun: `*.onnx` is
// excluded from git, so a fresh clone won't have the model file. Without this
// script, MicVAD.new() fetches /vad/silero_vad_legacy.onnx, gets SPA HTML, and
// ONNX runtime chokes with "no available backend found" or a wasm magic-word
// mismatch.
//
// CRITICAL: ort-wasm-* binaries MUST come from the nested pinned copy inside
// @ricky0123/vad-web/node_modules/onnxruntime-web, not the top-level
// onnxruntime-web package. The library pins an older runtime; the top-level
// version produces incompatible wasm.

import { copyFileSync, existsSync, mkdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const frontend = resolve(here, '..')
const dest = join(frontend, 'public', 'vad')

const vadDist = join(frontend, 'node_modules', '@ricky0123', 'vad-web', 'dist')
const ortDist = join(frontend, 'node_modules', '@ricky0123', 'vad-web', 'node_modules', 'onnxruntime-web', 'dist')

const copies = [
  [join(vadDist, 'silero_vad_legacy.onnx'), join(dest, 'silero_vad_legacy.onnx')],
  [join(vadDist, 'silero_vad_v5.onnx'), join(dest, 'silero_vad_v5.onnx')],
  [join(vadDist, 'vad.worklet.bundle.min.js'), join(dest, 'vad.worklet.bundle.min.js')],
  [join(ortDist, 'ort-wasm.wasm'), join(dest, 'ort-wasm.wasm')],
  [join(ortDist, 'ort-wasm-simd.wasm'), join(dest, 'ort-wasm-simd.wasm')],
  [join(ortDist, 'ort-wasm-threaded.wasm'), join(dest, 'ort-wasm-threaded.wasm')],
  [join(ortDist, 'ort-wasm-simd-threaded.wasm'), join(dest, 'ort-wasm-simd-threaded.wasm')],
]

mkdirSync(dest, { recursive: true })

let copied = 0
let missing = []
for (const [src, dst] of copies) {
  if (!existsSync(src)) {
    missing.push(src)
    continue
  }
  copyFileSync(src, dst)
  copied++
}

if (missing.length > 0) {
  console.error('[copy-vad-assets] Missing source files (did `npm install` run?):')
  for (const m of missing) console.error('  -', m)
  process.exit(1)
}

console.log(`[copy-vad-assets] Copied ${copied} VAD asset(s) to public/vad/`)
