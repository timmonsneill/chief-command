# Phase 1.1 — Google Cloud Voice

Swaps the voice layer from local (`faster-whisper` + Kokoro) to Google Cloud
Speech-to-Text v2 streaming + Cloud Text-to-Speech Chirp 3 HD, keeping the
Claude Opus brain unchanged. Target: end-to-end roundtrip **~3–4 s → ~1.5–2 s**.

Architecture decision: Cloud Speech v2 / Cloud TTS Chirp3 (not Gemini Live —
over budget, brain-coupled). The brain stays Claude, voice is a straight
provider swap.

---

## Default behaviour

- `VOICE_PROVIDER=local` (**default**) — no Google credentials needed.
  Backend runs exactly like before: faster-whisper STT + Kokoro TTS.
- `VOICE_PROVIDER=google` — backend uses Cloud Speech v2 streaming + Cloud
  TTS Chirp3 HD. Requires a service account JSON.

If `VOICE_PROVIDER` is unset, blank, or set to something unrecognised, the
factory falls back to `local` and logs a warning.

---

## 3-step recipe to go live

### 1. Create a GCP service account with voice access

```
https://console.cloud.google.com → Project "chief-command" (create if new)
  → IAM & Admin → Service Accounts → Create
  → Name: chief-command-voice
  → Roles: "Cloud Speech Client" + "Cloud Text-to-Speech Client"
  → Keys → Add Key → JSON → download
```

Enable both APIs on the project (one-time):
```
APIs & Services → Library → enable these two:
  - Cloud Speech-to-Text API
  - Cloud Text-to-Speech API
```

### 2. Drop the JSON and configure `.env`

Put the downloaded file at `backend/.secrets/gcp-voice.json`:

```bash
mkdir -p backend/.secrets
mv ~/Downloads/chief-command-<id>.json backend/.secrets/gcp-voice.json
chmod 600 backend/.secrets/gcp-voice.json
```

Then in `backend/.env`:

```
VOICE_PROVIDER=google
GOOGLE_APPLICATION_CREDENTIALS=/Users/user/Desktop/chief-command/backend/.secrets/gcp-voice.json
# Optional overrides — both default to values below if unset
GOOGLE_TTS_VOICE=en-US-Chirp3-HD-Aoede
GOOGLE_STT_LANGUAGE=en-US
```

(Use the absolute path — the Google client library does not expand `~`.)

### 3. Install deps and restart

```bash
cd backend
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

On startup you should see:

```
INFO services.voice_provider: Voice provider: google (STT v2 streaming + Chirp3 HD TTS). Credentials: /Users/user/Desktop/chief-command/backend/.secrets/gcp-voice.json
```

Open the voice page and test a round-trip. First call may be ~500 ms slower
(cold client auth); subsequent calls should be noticeably faster than local.

---

## Flip back to local (if Google misbehaves)

Either of:

**A. Edit `backend/.env`:**
```
VOICE_PROVIDER=local
```

**B. Override at launch without touching `.env`:**
```bash
VOICE_PROVIDER=local .venv/bin/uvicorn app.main:app --reload
```

Restart uvicorn. Startup log will read `Voice provider: local (faster-whisper + Kokoro)`. No other changes needed — `.env` can retain the Google credentials path; they are ignored when `VOICE_PROVIDER=local`.

---

## Available voices (Chirp 3 HD, `en-US`)

| ID | Gender | Note |
|---|---|---|
| `en-US-Chirp3-HD-Aoede` | female | Warm, conversational (default) |
| `en-US-Chirp3-HD-Kore` | female | Clear, professional |
| `en-US-Chirp3-HD-Leda` | female | Bright, youthful |
| `en-US-Chirp3-HD-Charon` | male | Deep, confident |
| `en-US-Chirp3-HD-Fenrir` | male | Energetic |
| `en-US-Chirp3-HD-Puck` | male | Upbeat, expressive |

Any other Chirp 3 HD voice ID Google publishes will also work; the curated
list above is what appears in the voice picker.

---

## Env var contract (full list)

| Var | Default | Purpose |
|---|---|---|
| `VOICE_PROVIDER` | `local` | `local` or `google` |
| `GOOGLE_APPLICATION_CREDENTIALS` | unset | Path to service-account JSON |
| `GOOGLE_TTS_VOICE` | `en-US-Chirp3-HD-Aoede` | Chirp3 HD voice id |
| `GOOGLE_STT_LANGUAGE` | `en-US` | BCP-47 language code |

`GOOGLE_APPLICATION_CREDENTIALS` is also exported into `os.environ` at
startup (if the settings entry is set), so the Google client libraries pick
it up regardless of whether you set it in `.env` or the shell.

---

## Known verification needed on first live call

**The async streaming RPC call shape is SDK-version-dependent.** The code
uses `responses = await client.streaming_recognize(...)` and
`response_stream = await client.streaming_synthesize(...)`. Depending on the
installed `google-cloud-speech` / `google-cloud-texttospeech` version, those
async methods may return the stream **directly** (no `await` needed) instead
of a coroutine that resolves to the stream.

**If you see** `TypeError: object StreamingRecognizeAsyncIterator can't be
used in 'await' expression` (or similar) on the first real voice turn:
drop the `await` from the two call sites:

- `backend/services/stt_google.py:242` — `responses = client.streaming_recognize(...)`
- `backend/services/tts_google.py:303` — `response_stream = client.streaming_synthesize(...)`

Tests cover this with mocks so they pass either way. Real SDK behaviour is
only knowable after `pip install` + first streaming call.

---

## Troubleshooting

- **"Failed to init Google Speech client"** on first voice turn: the JSON
  path is wrong or the APIs aren't enabled on the project. Check the two
  API toggles and that the path in `.env` is absolute and readable.
- **403 PERMISSION_DENIED** from Google: the service account is missing
  the two roles (Cloud Speech Client, Cloud TTS Client).
- **Voice sounds clipped / cuts off**: Chirp3 streaming chunks are wrapped
  in a per-frame WAV container — the frontend plays them sequentially.
  Check the browser console for decode errors.
- **High latency on first call (~500 ms extra)**: normal — first call builds
  the async client. Subsequent calls reuse it.

---

## File map

- `backend/services/voice_provider.py` — factory, picks provider from env.
- `backend/services/stt_google.py` — Cloud Speech v2 streaming wrapper.
- `backend/services/tts_google.py` — Cloud TTS Chirp3 HD streaming wrapper.
- `backend/services/stt.py`, `backend/services/tts.py` — unchanged, still the `local` path.
- `backend/tests/test_voice_provider.py` — factory tests.
- `backend/tests/test_stt_google.py`, `backend/tests/test_tts_google.py` — mocked-client smoke tests.
