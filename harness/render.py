"""Two renderings of one truth.

The single most important thing here: the voice and the text are NOT two systems.
They read the same event stream at different verbosity. Anything the voice says is
something you could have read; anything you can read, the voice can summarize.

    VOICE  →  a sentence. Spoken-length. "Riggs is on it — two minutes."
    TEXT   →  the terminal. Every tool call, every file, every result.

The voice can ESCALATE into the text stream on request ("what's it actually doing?").
That's the whole interaction model: fast by default, deep on demand — the same
principle that put a quick model at the mouth and a slow one at the brain.

Why voice must stay short (this is not a style preference):
audio output is 55-70% of the realtime API bill and costs ~16x more than the same
content as text. A chatty voice is an expensive voice. Short spoken summaries with
full detail in the log is the single best cost decision in the design — and it was
already in the spec (§5.3), written for latency reasons. It pays off twice.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# How an agent's activity reads out loud vs. on screen.
_KIND_SPOKEN = {
    "dispatched": "started",
    "thinking":   "working through it",
    "read":       "reading",
    "edit":       "editing",
    "write":      "writing",
    "command":    "running",
    "test_run":   "running tests",
    "browse":     "clicking through it",
    "verdict":    "reviewed",
    "done":       "finished",
    "error":      "hit a problem",
}

_KIND_GLYPH = {
    "dispatched": "→", "thinking": "·", "read": "◇", "edit": "✎", "write": "+",
    "command": "$", "test_run": "⚑", "browse": "👁", "verdict": "⚖",
    "done": "✓", "error": "✗",
}


def _seat_label(lane: str, model: str) -> str:
    """Always lane AND model. You must be able to see who's in the chair.

    'Riggs' alone doesn't tell you whether your auth module was built by your best
    coder or your cheapest one.
    """
    return f"{lane.title()} · {model}"


# ---------------------------------------------------------------------------
# TEXT — the terminal. This is where the detail lives.
# ---------------------------------------------------------------------------
def render_text(conn: sqlite3.Connection, job_id: int) -> str:
    job = conn.execute(
        "SELECT j.*, s.model, s.family FROM jobs j JOIN seats s ON s.id = j.builder_seat "
        "WHERE j.id = ?", (job_id,)
    ).fetchone()
    if job is None:
        return f"no such job: {job_id}"

    events = conn.execute(
        "SELECT * FROM events WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()

    lines = [
        f"job #{job['id']}  [{job['status']}]",
        f"  asked: {job['request']}",
        f"  built: {_seat_label(job['builder_seat'], job['model'])}",
        f"  branch: {job['branch'] or '—'}",
        "",
    ]

    for e in events:
        glyph = _KIND_GLYPH.get(e["kind"], "·")
        who = _seat_label(e["lane"], e["model"])
        target = f" {e['target']}" if e["target"] else ""
        detail = f"  — {e['detail']}" if e["detail"] else ""
        lines.append(f"  {glyph} [{who}]{target}{detail}")

    verdicts = conn.execute(
        "SELECT * FROM verdicts WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()
    if verdicts:
        lines.append("")
        lines.append("  panel:")
        for v in verdicts:
            mark = {"pass": "✓", "fail": "✗", "needs_human": "?"}[v["verdict"]]
            sev = f" {v['severity']}" if v["severity"] else ""
            lines.append(
                f"    {mark} {v['reviewer_seat']} ({v['model_family']}, {v['role']}){sev}"
                f"  {v['summary'] or ''}"
            )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# VOICE — a colleague, not a log reader.
#
# THE REGISTER (owner, 2026-07-13):
#     "I don't want to hear bash blah blah blah. But 'putting Riggs on the X,
#      I'll report back when he's done, and review is happening.'"
#
# So the voice NEVER speaks in tool calls. No "read", no "edit", no "bash", no file
# paths. Those are the text channel's job.
#
# The voice speaks in three things a person would actually say:
#     INTENT      who you put on it, and on what
#     COMMITMENT  when you'll hear back
#     WHAT'S NEXT the pipeline, not the keystroke
#
# Tool activity collapses into PHASES, because "he's writing the code" is what a
# colleague says and "read auth.py, then edited routes.py" is what a log says.
# ---------------------------------------------------------------------------

# ═══════════════════════════════════════════════════════════════════════════
# THE HARD RULE: NEILL IS NOT A CODER.
#
# His words (2026-07-13): "I never ever wanna hear that type of granular info.
# Editing dispatch.py, writing test_ratelimit.py. I don't know what that means…
# Think of it like I know Spanish 101, but that's it."
#
# So the voice NEVER emits:
#   - a filename or path    (dispatch.py, backend/app/routes.py)
#   - a tool name           (bash, read, edit, grep)
#   - code jargon           (429s, middleware, async, regex)
#
# Every phase below is something a smart person who has never programmed would
# understand. They all refer back to THE THING (the task name) — never the files
# underneath it. "He's building it." "Now he's testing it." That's the ceiling.
#
# The filenames STILL EXIST — in the text channel, which he can scroll past. This
# is not dumbing down. It is putting the detail in the channel that can carry it.
# ═══════════════════════════════════════════════════════════════════════════
_PHASE = {
    "dispatched": "just getting started",
    "read":       "getting his head round it",
    "thinking":   "working out how to do it",
    "edit":       "building it",
    "write":      "building it",
    "command":    "building it",
    "test_run":   "testing it",
    "browse":     "clicking through it like a user",
    "verdict":    "waiting on the others",
    "error":      "stuck",
}


def _phase_of(conn: sqlite3.Connection, job_id: int) -> str:
    row = conn.execute(
        "SELECT kind FROM events WHERE job_id = ? ORDER BY id DESC LIMIT 1", (job_id,)
    ).fetchone()
    return _PHASE.get(row["kind"], "working on it") if row else "just getting started"


def announce_dispatch(lane: str, task_name: str, reviewers: int = 0) -> str:
    """What the mouth says the MOMENT you ask for something.

    Must land in under a second, while the real work is still spinning up. Intent,
    commitment, what's next — one breath. The model is NOT named here: at dispatch
    you want to know it's moving, not which vendor is billing. Ask and it'll tell you.
    """
    line = f"Putting {lane.title()} on {task_name}. I'll come back when he's done"
    if reviewers:
        line += ", and the panel reviews it after"
    return line + "."


def render_voice(conn: sqlite3.Connection, job_id: int, depth: str = "brief") -> str:
    """depth: 'brief' (default) | 'normal' | 'detailed'

    All three speak like a person. The difference is how much they tell you — never
    whether they descend into tool calls. They never do.
    """
    job = conn.execute(
        "SELECT j.*, s.model, s.family FROM jobs j JOIN seats s ON s.id = j.builder_seat "
        "WHERE j.id = ?", (job_id,)
    ).fetchone()
    if job is None:
        return "I don't have a job by that number."

    lane = job["builder_seat"].title()
    # "Riggs, on Claude" — the comma matters. "Riggs on Claude on the rate limiter"
    # is not English.
    who = f"{lane}, on {_spoken_model(job['model'])},"
    task = job["task_name"] or _shorten(job["request"])
    status = job["status"]

    verdicts = conn.execute(
        "SELECT reviewer_seat, model_family, role, verdict, summary FROM verdicts "
        "WHERE job_id = ? ORDER BY id", (job_id,)
    ).fetchall()

    # ── Bad news is volunteered at every depth. You never have to ask. ──
    if status == "failed":
        return f"{lane} is stuck on {task}. Want me to put someone else on it?"

    blocking = [v for v in verdicts if v["verdict"] in ("fail", "needs_human")]
    if blocking:
        v = blocking[0]
        tester = " when he tested it" if v["role"] == "tester" else ""
        note = (v["summary"] or "it needs a fix").strip()
        note = note[0].upper() + note[1:] if note else note
        base = (f"{_spoken_model_from_family(v['model_family'])} knocked {task} back"
                f"{tester}. {note}")
        return base.rstrip(".") + ("." if depth == "brief" else f". {lane} is on the fix.")

    # ── Shipped ──
    if status == "shipped":
        if depth == "brief":
            return job["spoken_summary"] or f"{lane} finished {task}. It's in."
        fams = {v["model_family"] for v in verdicts}
        tester = next((v for v in verdicts if v["role"] == "tester"), None)
        line = f"{who} finished {task}."
        if tester:
            line += f" {_spoken_model_from_family(tester['model_family'])} tested it and it held up."
        if len(fams) > 1:
            line += f" {len(fams)} different models signed off. It's in."
        return line

    # ── With the panel ──
    if status in ("review", "done"):
        if depth == "brief":
            return f"{lane} is done. Review's happening."
        passed = sum(1 for v in verdicts if v["verdict"] == "pass")
        total = job["required_reviews"] or len(verdicts)
        # Count MINDS, not chairs — the floor the job is actually waiting on is how many
        # different kinds of model signed off, and that is also the honest thing to say
        # out loud. "Two of two are in" next to a stalled job is a lie by omission.
        fams = {v["model_family"] for v in verdicts if v["verdict"] == "pass"}
        need_fams = job["required_review_families"] or 0
        if need_fams and len(fams) < need_fams:
            return (f"{who} finished {task}. It's with the panel — "
                    f"{len(fams)} of {need_fams} different models have signed off.")
        return (f"{who} finished {task}. "
                f"It's with the panel — {passed} of {total} in so far.")

    # ── Still building ──
    if depth == "brief":
        return f"{lane} is still on {task}."
    return f"{who} {_phase_of(conn, job_id)}. I'll tell you when he's through."


def _shorten(request: str, words: int = 8) -> str:
    """A spoken task name, not the whole prompt. Nobody wants their own paragraph read back."""
    w = request.strip().rstrip(".").split()
    return " ".join(w[:words]) + ("…" if len(w) > words else "")


def _spoken_model_from_family(family: str) -> str:
    return {"claude": "Claude", "gpt": "Sol", "grok": "Grok", "qwen": "Coal"}.get(family, family)


def _spoken_model(model: str) -> str:
    """Model IDs are unspeakable. 'claude-opus-4-8' should come out as 'Claude'."""
    m = model.lower()
    if "opus" in m or "claude" in m:  return "Claude"
    if "gpt" in m or "sol" in m:      return "Sol"
    if "grok" in m:                   return "Grok"
    if "qwen" in m:                   return "Coal"
    return model


# ---------------------------------------------------------------------------
# The morning briefing — §7's "what did the overnight run do?"
# ---------------------------------------------------------------------------
def render_morning_voice(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        """
        SELECT status, COUNT(*) n FROM jobs
         WHERE created_at >= datetime('now', '-1 day') GROUP BY status
        """
    ).fetchall()
    counts = {r["status"]: r["n"] for r in rows}
    if not counts:
        return "Nothing ran overnight."

    parts = []
    if counts.get("shipped"): parts.append(f"{counts['shipped']} shipped")
    if counts.get("review"):  parts.append(f"{counts['review']} waiting on the panel")
    if counts.get("failed"):  parts.append(f"{counts['failed']} failed")
    if counts.get("in_progress"): parts.append(f"{counts['in_progress']} still running")

    line = ", ".join(parts) + "."
    if counts.get("failed") or counts.get("review"):
        line += " Want me to walk you through them?"
    return line
