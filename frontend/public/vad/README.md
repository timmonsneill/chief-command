# VAD Static Assets

NOTE: `*.onnx` files are excluded from git by `.gitignore`.
After cloning or when the onnx file is missing, run the copy commands below before building.

These files are served at /vad/ and loaded at runtime by @ricky0123/vad-web.

## IMPORTANT — filename must match what the library fetches

`@ricky0123/vad-web` with `model: 'legacy'` fetches `${baseAssetPath}silero_vad_legacy.onnx`.
Do NOT rename it to `silero_vad.onnx` — the library will 404, fall through the SPA
catch-all, get HTML back, and ONNX runtime will fail to initialize with
"no available backend found" (wasm magic word mismatch because the HTML body is
being fed as wasm).

## Files and origins

- `vad.worklet.bundle.min.js` — copied from `node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js`
- `silero_vad_legacy.onnx` — copied from `node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx` (keep name)
- `ort-wasm-simd-threaded.wasm` — copied from `node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm`
- `ort-wasm-simd-threaded.asyncify.wasm` — copied from `node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm`

`silero_vad.onnx` (without `_legacy`) is kept only as a compat copy; it can be deleted once nothing else references it.

## When to re-copy

Re-copy these files whenever you upgrade `@ricky0123/vad-web` or `onnxruntime-web` in package.json.

Commands:
```
cp node_modules/@ricky0123/vad-web/dist/vad.worklet.bundle.min.js public/vad/
cp node_modules/@ricky0123/vad-web/dist/silero_vad_legacy.onnx public/vad/
cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.wasm public/vad/
cp node_modules/onnxruntime-web/dist/ort-wasm-simd-threaded.asyncify.wasm public/vad/
```
