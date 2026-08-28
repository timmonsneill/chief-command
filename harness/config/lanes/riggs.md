# Lane memory — Riggs (shared fallback)

No project-specific conventions file exists for this project yet (there is no
`lanes/<project_id>/riggs.md`). Generic defaults until one does:

- Python: follow the standard library's own style (PEP 8), prefer explicit over
  implicit, keep functions small enough to read in one screen.
- This project's own AGENTS.md / CLAUDE.md / README, if the repo has one, is the
  real source of truth — read it first and follow IT over anything generic here.
- Comments should explain WHY a check exists, not restate the line under it.
- Never invent a database migration, schema change, or destructive operation on
  your own judgment — that is exactly the kind of thing a project's own review
  process exists to catch, and skipping it is not a shortcut, it's a different job.
- Fail closed and say so in plain English. Never guess silently and hope it's right.
