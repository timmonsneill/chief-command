# Chief Command v3 — Shared Build Spec

**Single source of truth for the v3 build.** All three builders (Riggs, Finn, Nova) reference this. Do not diverge. Do not duplicate data shapes or paths across builders. Contract mismatches break the app.

## What v3 ships

1. **Team tab** — the named roster (Chief, Atlas, Forge, Riggs, Finn, Nova, Vera, Hawke, Sable, Pax, Quill, Hip). Cards with role, lean, last-active, link into per-agent memory.
2. **Memory tab** — global memory, per-project memory, per-agent memory, and audit log. Read/search/edit.
3. **Project-context switching** — top-bar picker that scopes the UI (memory, sessions, voice/chat context) to one project. Default: Chief Command. Options: Chief Command, Arch, Archie.

## File ownership (no overlap, ever)

| File / module | Owner |
|---|---|
| `backend/app/main.py` (adding routes) | **Riggs** |
| `backend/services/team_service.py` (NEW) | **Riggs** |
| `backend/services/memory_service.py` (NEW) | **Riggs** |
| `backend/services/project_context.py` (NEW) | **Nova** |
| `frontend/src/lib/api.ts` (adding types + methods) | **Nova** (shared file — coordinate via this spec) |
| `frontend/src/pages/TeamPage.tsx` (NEW) | **Finn** |
| `frontend/src/pages/MemoryPage.tsx` (NEW) | **Finn** |
| `frontend/src/components/Layout.tsx` (adding tabs + project picker) | **Finn** |
| `frontend/src/components/ProjectPicker.tsx` (NEW) | **Finn** |
| `frontend/src/hooks/useProjectContext.ts` (NEW) | **Nova** |
| `frontend/src/App.tsx` (adding routes) | **Finn** |

Nova owns `api.ts` because the types are the contract between backend (Riggs) and frontend (Finn). Nova writes the types FIRST per this spec, then Riggs and Finn implement against them.

## API contracts (exact)

### Team

```
GET /api/team
→ { agents: AgentProfile[] }

interface AgentProfile {
  name: string                    // "Riggs"
  role: string                    // "Builder — backend"
  lean: string                    // "Systems, FastAPI, SQL, async, infra"
  model: "opus" | "sonnet"
  tier: "chief" | "opus" | "sonnet"
  memory_path: string             // "~/.claude/agents/memory/riggs.md" (absolute)
  last_active: string | null      // ISO timestamp or null
  invocations_total: number       // cumulative
  description: string             // one-sentence tagline
}

GET /api/team/{name}/memory
→ { name: string, content: string, updated_at: string }

PUT /api/team/{name}/memory
body: { content: string }
→ { name: string, content: string, updated_at: string }
```

Data source: **read from filesystem**. `~/.claude/agents/memory/<name>.md`. Fall back to empty string if file doesn't exist (agent hasn't accrued memory yet).

Roster itself is hardcoded in `backend/services/team_service.py` as a Python list of `AgentProfile` dicts. Canonical roster lives at `~/.claude/projects/-Users-user/memory/project_agent_roster.md` — mirror it, don't re-invent names.

Tracking `last_active` + `invocations_total`: Riggs — scan `~/.claude/projects/**/subagents/agent-*.jsonl` metadata. If that's too expensive per request, stub both (null / 0) and note it. Don't block the build on this.

### Memory

```
GET /api/memory
→ {
  global: MemoryEntry[]           // ~/.claude/projects/-Users-user/memory/*.md (excluding MEMORY.md, PROJECTS.json)
  per_project: ProjectMemory[]    // one per project, each with its entries
  per_agent: AgentMemory[]        // one per named agent
  audit_log: AuditEntry[]         // most recent first, from audit_log.md
}

interface MemoryEntry {
  filename: string
  title: string          // frontmatter `name` field, or filename
  type: "user" | "feedback" | "project" | "reference"
  description: string    // frontmatter `description`
  content: string        // raw markdown body
  updated_at: string     // file mtime ISO
}

interface ProjectMemory {
  project: string        // "Arch" | "Chief Command" | ...
  status: "active" | "done"
  entries: MemoryEntry[]
}

interface AgentMemory {
  name: string           // "Riggs"
  content: string
  updated_at: string | null
}

interface AuditEntry {
  timestamp: string
  action: "removed" | "updated" | "promoted" | "demoted" | "created"
  target: string         // filename
  reason: string
}

GET /api/memory/{filename}
→ MemoryEntry

PUT /api/memory/{filename}
body: { content: string }
→ MemoryEntry
```

Audit log file: `~/.claude/projects/-Users-user/memory/audit_log.md`. Create if missing. Monday hygiene task (not part of v3 scope — just read the file and display).

### Project context

```
GET /api/context
→ { current: string, available: string[] }    // e.g. { current: "Chief Command", available: ["Chief Command", "Arch", "Archie"] }

POST /api/context
body: { project: string }
→ { current: string }
```

State is **per-session**, stored server-side in memory (module-level dict keyed by JWT subject). No DB persistence in v3 — resets on restart. That's fine.

Frontend: `useProjectContext()` hook reads `/api/context` on mount, exposes `current` and `setContext(name)`. Other pages that care (Memory, Sessions, Voice) read from this hook.

**Scoping behavior:**
- "All" → no filter
- "Arch" → Memory tab shows only Arch project memory + user/feedback; Sessions shows only Arch sessions; Voice prepends "(Project: Arch)" to the system prompt
- Same pattern for other projects

Scope filter on Memory is a frontend-side filter using the `ProjectMemory.project` field. Sessions scope is frontend-side filter on a new `session.project` field → if the field doesn't exist yet, Riggs can add it as nullable (no backfill).

## Frontend details

**Nav:** 7 tabs now. Current order: Voice, Agents, Projects, Sessions, Terminal. New order:

`Voice · Team · Agents · Projects · Memory · Sessions · Terminal`

If 7 icons feels cramped on mobile, collapse Terminal + Sessions into a "more" menu — Finn's call. Voice stays default route.

**Project picker:** top status bar. Replaces or augments the "Chief" label. Small dropdown showing current project; tap to switch. Include "All" option.

**Team tab layout:**
- Card grid, 1 column mobile, 2 columns wider screens
- Each card: name (big), role (medium), lean (small), last-active (timestamp), model badge
- Chief card is highlighted / larger at top (orchestrator)
- Tap a card → modal or detail view with memory editor (textarea bound to PUT /api/team/{name}/memory)

**Memory tab layout:**
- Accordion or tabbed sections: Global · Per-project · Per-agent · Audit log
- Each entry: title, description, last-updated, expand to show content + edit
- Edit = simple textarea + Save button that hits PUT /api/memory/{filename}
- No inline markdown rendering in v3 — raw edit is fine. Mark as a v4 thing.

## Backend details

- Match existing route style in `backend/app/main.py` — all authed routes use `Depends(require_auth)`
- All new services go under `backend/services/`
- No DB migrations needed — v3 is filesystem-backed for memory/team/context
- Existing logger, config, etc. — reuse

## Testing expectations

Each builder verifies their slice works before reporting back:
- **Riggs:** `pytest` existing suite + manual curl of new routes (document the curl commands in commit message)
- **Finn:** `npm run build` succeeds + visual spot-check (describe what you saw in commit message)
- **Nova:** contract + types typecheck cleanly, `useProjectContext` returns expected shape in a quick manual test

Forge does the full integration pass afterward. Builders don't need to test each other's lanes — focus on your own.

## Critical rules

1. **Work in your worktree.** The builder agent frontmatter has `isolation: worktree`. Do not commit to `main` directly. A previous session's builders did that and broke ownership. If you're on main at any point, STOP and report.
2. **No scope creep.** The scope of v3 is: Team tab, Memory tab, project-context switching. Do not touch v1/v2 code unless your lane's task forces it. No cleanup, no refactors.
3. **Match existing patterns.** Read adjacent files before writing. Use existing hooks, components, and color tokens (`text-chief`, `bg-surface-raised`, etc.).
4. **Report back via the agent's standard output format.** Include: files changed, commit hash, worktree path, test evidence.
