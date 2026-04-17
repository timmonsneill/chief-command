# VAD Static Assets

NOTE: `silero_vad.onnx` is excluded from git by `.gitignore` (`*.onnx` rule).
After cloning or when the onnx file is missing, run the copy commands below before building.


These files are served at /vad/ and loaded at runtime by @ricky0123/vad-web.

## Files and origins

- `vad.worklet.bundle.min.js` — copied from `node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js`
- `silero_vad.onnx` — copied from `node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx` (renamed)
- `ort-wasm-simd-threaded.wasm` — copied from `node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm`
- `ort-wasm-simd-threaded.asyncify.wasm` — copied from `node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm`

## When to re-copy

Re-copy these files whenever you upgrade `@ricky0123/vad-web` or `onnxruntime-web` in package.json.

Commands:
```
cp node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js public/vad/
cp node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx public/vad/silero_vad.onnx
cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm public/vad/
cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm public/vad/
```
