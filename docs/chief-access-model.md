# Chief's Access Model

**Purpose:** Spell out exactly what Chief (voice + chat) can read, write,
execute, and touch — split by boundary. Written in response to Neill's
question *"what exactly will it have access to, my desktop and desktop files,
whatever else I wanna give it if it makes sense."*

**Last updated:** 2026-04-18 (Chief Context v1.1 + Dispatch Bridge v1 landing)

---

## The three boundaries

Chief is not one thing with one access level. He's **three layers**, each with
different permissions:

1. **Voice/chat LLM** — the thing that talks to you. Reads a system prompt
   you've seen (agent roster, memory files, project context). Runs on the
   Anthropic API. Has no hands.
2. **Voice/chat LLM with project-switch detection** — same LLM, but the
   server-side handler can update `current_project` based on your
   utterance. That's the most it can change.
3. **Dispatched `claude` subprocess** — when you give Chief an imperative task,
   he spawns the `claude` CLI on your Mac with your Max subscription. *This*
   is the layer with real hands. Full filesystem write within the scoped
   repo. Full git. Full `gh`. Full pytest. Full anything you have installed.

The security story is that **layers 1 and 2 are read-only + routing**, and
**layer 3 is scope-gated + env-restricted**. Everything interesting lives at
layer 3.

---

## Layer 1: Voice/chat LLM (read-only, always-on)

### Reads

- `~/.claude/projects/-Users-user/memory/*.md` (your global user + feedback
  memory, always loaded)
- `~/.claude/agents/memory/*.md` (Chief's own agent roster — Atlas, Forge,
  Riggs, Finn, Nova, Vera, Hawke, Sable, Pax, Quill, Hip, names + roles)
- `~/.claude/projects/-Users-user-Desktop-<scope>/memory/*.md` (**only** the
  currently-scoped project's memory, exact canonical match, no substring
  leaks)
- Conversation history within the current WebSocket session
- Project scope set via the picker or voice command

### Writes

- **Nothing directly.** The voice LLM has no tools wired that write files,
  create records, or mutate state. All writes flow through layer 3.

### Executes

- Anthropic API calls (API key billed to your account). Today that's Sonnet
  4.6 default, Opus 4.7 on deep-question bridge. After dispatch ships,
  defaults stay cheap because heavy lifting moves to layer 3.

### Risks

- **Prompt injection from memory files.** Memory files are concatenated into
  the system prompt. A compromised sub-agent writing to one could hijack
  Chief's behavior. Mitigation: identity-prompt guard text, provenance
  fences around each file (`<memory file="…" mtime="…">…</memory>`), and
  file-source tagging so odd instructions are visible.
- **Symlink read.** Someone dropping a symlink at `~/.claude/.../memory/`
  to `~/.ssh/id_rsa` would get it read into Chief's context and sent to
  Anthropic. Mitigation: symlink rejection at read time (implemented in
  `memory_paths.safe_md_files` + `team_service`/`memory_service`).

---

## Layer 2: Project-switch detection

### Reads

- The user's utterance (text or STT transcript)

### Writes

- The in-memory `current_project` variable for the WS session
- In-memory `_context_store` keyed by JWT subject (persists across tabs)

### Executes

- `detect_project_switch` regex match against the user text
- `set_context(subject, project)` with allowlist enforcement

### Scope

- Only 3 canonical projects recognized: `Chief Command` (default), `Arch`,
  `Archie`
- "All" was removed as a scope — Chief is always focused on exactly one
  project
- Explicit switch phrases only: "switch to X", "let's talk about X", "show
  me X" — with terminator guard so *"show me all the files"* doesn't fire

### Not yet (v1.1)

- Auto-suggest switch on implicit cross-project mention (e.g. "explain the
  Arch deduction rules" while scoped to Chief Command → Chief says
  *"Want me to switch to Arch for that?"* before answering)

---

## Layer 3: Dispatched `claude` subprocess (the hands)

This is where Chief can actually do things. Triggered by the classifier
labeling your turn as `task`.

### Subprocess invocation

```
asyncio.create_subprocess_exec(
    "claude", "--print", "--model", "claude-opus-4-7", task_spec,
    cwd=<scoped_repo_path>,
    env=<env_allowlist_only>,
    stdout=PIPE,
    stderr=PIPE,
)
```

- `task_spec` is passed as argv (no shell interpolation — `$()`, backticks,
  `;` etc. are literal bytes)
- `cwd` is locked to the current scope's repo root (resolved + validated
  against `~/Desktop/` allowlist)
- `env` is allowlisted (see below)

### `cwd` = the scope's repo root

| Scope | Repo path |
|---|---|
| Arch | `~/Desktop/arch-to-freedom-emr` |
| Chief Command | `~/Desktop/chief-command` |
| Archie | (not configured — dispatch refused with clarifying message) |

The subprocess starts in that directory. `claude` won't wander unless the
task explicitly tells it to `cd` somewhere else, and it can't `cd` outside
`~/Desktop/` without a second explicit instruction.

### Env allowlist (what the subprocess sees)

```python
_ENV_ALLOWLIST = {
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "LC_CTYPE",
    "TERM", "TMPDIR", "PWD",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME",
}
```

Everything else from your shell environment is stripped. Concretely:

| Variable | Stripped? | Why |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ stripped | Force `claude` onto Max OAuth |
| `ANTHROPIC_AUTH_TOKEN` | ✅ stripped | Same reason |
| `AWS_*` | ✅ stripped | Default deny |
| `GITHUB_TOKEN` / `GH_TOKEN` | ✅ stripped by default | See "GitHub access" below |
| `OPENAI_API_KEY` | ✅ stripped | Default deny |
| `DEEPGRAM_API_KEY` | ✅ stripped | Default deny |
| `STRIPE_*` | ✅ stripped | Default deny |
| `*_TOKEN` / `*_SECRET` / `*_PASSWORD` | ✅ stripped | Default deny |
| `PATH` / `HOME` / etc. | ❌ kept | CLI can't run without these |

### What `claude` can do inside the subprocess

A lot. It's a full Opus 4.7 agent with:
- File read/write anywhere under `cwd`
- Shell commands (anything in your `$PATH` — git, npm, pytest, ffmpeg,
  pandoc, etc.)
- Its own sub-agents (can spawn Riggs/Finn/Nova/reviewers/Forge internally)
- MCP servers if you have them configured (check `~/.claude/settings*.json`)

It runs as YOU. Same UID, same file permissions, same git credentials (if
those come from `~/.gitconfig` rather than env). Don't confuse "env
stripped" with "sandboxed" — this is a real subprocess with real power,
just with reduced ambient secret inheritance.

### GitHub access

- **Default: no GitHub access.** `GITHUB_TOKEN` / `GH_TOKEN` are stripped.
- **To enable:** add `GITHUB_TOKEN` to the allowlist. Once you do, every
  dispatched subprocess can `gh pr create`, `gh issue comment`, read
  private repo contents, etc.
- **Scope implication:** GitHub access is NOT scope-gated the way the
  filesystem is. A dispatched `claude` run in your Arch scope can still
  post to the Chief-Command repo if it decides to. This is a v1.1 design
  question: should we run `gh` through a wrapper that validates the repo
  matches the current scope?

### Concurrency and guardrails

- **One task per WS session.** Attempts to dispatch while a task is running
  raise `TaskAlreadyRunning`; Chief voice asks "still running the last
  one — cancel it?".
- **30-minute hard timeout** (configurable). Auto-kill with SIGKILL.
- **`task_spec` bounded to 8000 chars.** Pasting a megabyte of text doesn't
  trigger a massive subprocess.
- **User-text bounded to 4000 chars** before classifier call.
- **Cancel path:** Voice ("stop" / "cancel" / "never mind") OR the
  TaskBubble Cancel button. SIGTERM → 5s grace → SIGKILL.
- **WS disconnect cleanup:** if the browser drops mid-task, the
  subprocess is cancelled automatically (not orphaned).

---

## What Chief voice itself can write (future v1.1)

Two options we're evaluating for direct-write capability:

1. **`append_to_memory(scope, filename, content)` tool** — Chief voice has
   a bounded tool that appends to scope-matching `.md` files. Scope-gated
   (can't write Arch memory while scoped to Chief Command). No path
   traversal, no creation of new filenames outside a whitelist pattern.

2. **Dispatch everything.** Memory edits go through `claude` subprocess,
   meaning a full audit trail + ability to review edits before they land.

Trade-off: option 1 is instant, option 2 is safer and more powerful.
Leaning toward option 1 for lightweight notes ("remember I prefer bcrypt
for Arch auth"), option 2 for heavy work (specs, PDFs, builds).

**Not landed yet.** Flag for next build cycle.

---

## What Chief CANNOT do (today and v1.1)

- Read files outside `~/.claude/projects/…/memory/` and `~/.claude/agents/memory/` (layer 1)
- Write any file at layer 1 or 2
- Run shell commands at layer 1 or 2
- Access GitHub unless you explicitly add the token to the allowlist
- Access AWS / cloud providers
- Touch files outside the scope's repo (layer 3 is `cwd`-locked)
- Send emails, Slack messages, or post to third-party platforms
- Cross-project writes in one subprocess (cwd is scope-locked)

---

## What I'd ask Neill to confirm before we extend access

- **GitHub token:** do you want `GH_TOKEN` in the allowlist so dispatched
  tasks can push PRs? If yes, should we add a per-repo validation wrapper?
- **Desktop beyond-repo access:** any case where a task should be able to
  touch files in `~/Desktop/` outside its scoped repo? (Probably no, but
  worth explicit.)
- **Direct memory write:** should Chief voice be able to append to memory
  files directly (option 1 above), or only via dispatch (option 2)?
- **Auto-suggest scope switch on implicit cross-project mention:** ship
  in v1.1?

---

## Where this gets audited

- Scope changes: WS log `Voice WS project-switch intent detected`
- Dispatch: WS log `dispatcher: spawned claude pid=<n> session=<id> repo=<path>`
- Dispatch completion: WS log `dispatcher: task completed exit=<n> duration=<s>`
- Cancellation: WS log `dispatcher: cancelled session=<id> reason=<r>`

Tail them with:
```
tail -f /tmp/chief-uvicorn.log | grep -E "project-switch|dispatcher:"
```
