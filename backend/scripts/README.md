# backend/scripts

Standalone utility scripts for Chief Command. These are not FastAPI endpoints —
they run directly with the venv Python interpreter.

---

## audit_runner.py

**What it does:**

Scans `~/.claude/projects/-Users-user/memory/` for memory hygiene issues and
appends a dated entry to `audit_log.md` in that directory. Creates `audit_log.md`
if it doesn't exist.

**Hygiene checks:**

| Check | Threshold | Flag |
|-------|-----------|------|
| File not modified recently | >90 days | `[stale]` |
| File size too large | >5 KB | `[bloated]` |
| Duplicate frontmatter `name:` or `description:` | any | `[duplicate]` |
| MEMORY.md references a file that doesn't exist | — | `[broken-link]` |

**How to run:**

```bash
# From repo root
backend/.venv/bin/python3 backend/scripts/audit_runner.py
```

Output is printed to stdout and also appended to `audit_log.md`.

**Schedule (optional):**

See `com.chief.audit-weekly.plist` below for automatic weekly scheduling on macOS.

---

## com.chief.audit-weekly.plist

**What it does:**

A macOS launchd plist template that schedules `audit_runner.py` to run every
Monday at 8:00 AM.

**How to activate:**

```bash
# 1. Copy to LaunchAgents
cp backend/scripts/com.chief.audit-weekly.plist ~/Library/LaunchAgents/

# 2. Load it
launchctl load ~/Library/LaunchAgents/com.chief.audit-weekly.plist

# 3. Optional: run immediately to verify it works
launchctl start com.chief.audit-weekly

# 4. Check output
cat /tmp/chief-audit.log
cat /tmp/chief-audit-error.log
```

**How to deactivate:**

```bash
launchctl unload ~/Library/LaunchAgents/com.chief.audit-weekly.plist
```

**Note:** The plist uses absolute paths to the repo at
`/Users/user/Desktop/chief-command/`. If you move the repo, update
`ProgramArguments` and `WorkingDirectory` in the plist before loading it.
