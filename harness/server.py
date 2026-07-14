"""Chief Command — the web app. Served on the tailnet, reachable from the phone.

This is what Neill actually opens. Everything else in the harness exists so that this
can tell him the truth.

Design rules, all downstream of one fact — HE CANNOT READ CODE:
  - Plain English everywhere. No filenames in the summary views.
  - Model family is the color. You should see a Claude build was tested by a Grok
    before you read a single word.
  - Blocked things are loud. A job stuck at a gate is the most important thing on
    the screen, because it's the only thing that needs him.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from db.jobs import connect, init_db

app = FastAPI(title="Chief Command")

DB = Path(__file__).resolve().parent / "db" / "chief.db"

FAMILY_COLOR = {"claude": "#FF8A3D", "gpt": "#00E5A0", "grok": "#B06CFF", "qwen": "#2E9BFF"}


def db():
    c = connect(DB)
    init_db(c)
    return c


# ── Plain-English status. He should never see a machine word. ────────────────
STATUS_WORDS = {
    "todo":        ("Queued",      "waiting"),
    "in_progress": ("Building",    "running"),
    "review":      ("Being checked", "running"),
    "done":        ("Checked, ready", "ok"),
    "shipped":     ("Done",        "ok"),
    "failed":      ("Broke",       "bad"),
    "cancelled":   ("Stopped",     "waiting"),
}


@app.get("/api/state")
def state():
    """Everything the dashboard needs, in one call."""
    c = db()

    seats = [dict(r) for r in c.execute(
        "SELECT id, model, family, tier, daily_cap_cents, build_cap_cents FROM seats WHERE enabled=1"
    )]
    for s in seats:
        spent = c.execute(
            "SELECT COALESCE(SUM(cost_cents),0) v FROM usage WHERE seat_id=? AND day=date('now')",
            (s["id"],)
        ).fetchone()["v"]
        s["spent_today_cents"] = int(spent)
        s["color"] = FAMILY_COLOR.get(s["family"], "#7E90A8")

    jobs = []
    for r in c.execute(
        """SELECT j.*, s.model AS builder_model
             FROM jobs j JOIN seats s ON s.id = j.builder_seat
            ORDER BY j.id DESC LIMIT 40"""
    ):
        j = dict(r)
        word, tone = STATUS_WORDS.get(j["status"], (j["status"], "waiting"))
        j["status_word"] = word
        j["tone"] = tone
        j["color"] = FAMILY_COLOR.get(j["builder_family"], "#7E90A8")
        j["verdicts"] = [dict(v) for v in c.execute(
            "SELECT reviewer_seat, model_family, role, verdict, summary FROM verdicts "
            "WHERE job_id=? ORDER BY id", (j["id"],)
        )]
        for v in j["verdicts"]:
            v["color"] = FAMILY_COLOR.get(v["model_family"], "#7E90A8")
        j["events"] = [dict(e) for e in c.execute(
            "SELECT kind, target, detail, lane, model FROM events WHERE job_id=? ORDER BY id",
            (j["id"],)
        )]
        # Why is this stuck? In words he can act on.
        j["blocked_because"] = _why_blocked(c, j)
        jobs.append(j)

    projects = [dict(r) for r in c.execute("SELECT * FROM projects WHERE archived=0")]
    for p in projects:
        p["memory"] = [dict(m) for m in c.execute(
            "SELECT kind, fact FROM project_memory WHERE project_id=? ORDER BY id DESC LIMIT 30",
            (p["id"],)
        )]
        p["plan"] = [dict(x) for x in c.execute(
            "SELECT * FROM plan_items WHERE project_id=? ORDER BY position, id", (p["id"],)
        )]
        p["job_count"] = c.execute(
            "SELECT COUNT(*) n FROM jobs WHERE project_id=?", (p["id"],)
        ).fetchone()["n"]

    return {"seats": seats, "jobs": jobs, "projects": projects}


def _why_blocked(c, j) -> str | None:
    """The single most useful string on the screen: what is this waiting for?"""
    if j["status"] in ("shipped", "failed", "cancelled"):
        return None

    if any(v["verdict"] == "needs_human" for v in j["verdicts"]):
        return "Someone needs to make a call on this — the reviewers disagreed."

    if j["status"] == "done":
        has_tester = any(v["role"] == "tester" and v["verdict"] == "pass" for v in j["verdicts"])
        if not has_tester:
            return "Nobody has actually opened it and used it yet."
        return None

    if j["status"] == "review":
        if j["builder_tier"] == "local":
            paid = any(v["reviewer_tier"] in ("subscription", "metered") and v["verdict"] == "pass"
                       for v in j["verdicts"])
            if not paid:
                return "Coal wrote this. It can't go anywhere until a better model checks it."
        need = j["required_reviews"] or 0
        got = len({v["reviewer_seat"] for v in j["verdicts"] if v["verdict"] == "pass"})
        if need and got < need:
            return f"Waiting on the others — {got} of {need} have looked at it."
    return None


@app.post("/api/say")
async def say(request: Request):
    """The text channel. Type at Chief; Chief decides what to do about it.

    This is the SAME brain the voice will use — mouth.py. Text and voice are two
    doors into one room, which is the whole point of building text first.
    """
    body = await request.json()
    utterance = (body.get("text") or "").strip()
    if not utterance:
        return JSONResponse({"reply": "Say something."})

    from mouth import needs_the_brain, is_pushback, cover_the_gap, dig_in, think, THINK_FAST, THINK_HARD

    last = body.get("last_answer") or ""

    if is_pushback(utterance) and last:
        answer, secs = think(
            body.get("last_question", utterance),
            context=f"You already answered this and he pushed back. Your last answer was: {last}",
            tier=THINK_HARD,
        )
        return {"reply": answer, "holding": dig_in(), "seconds": round(secs, 1), "tier": "deep"}

    if needs_the_brain(utterance):
        answer, secs = think(utterance, context=_project_context(), tier=THINK_FAST)
        return {"reply": answer, "holding": cover_the_gap(), "seconds": round(secs, 1), "tier": "think"}

    return {"reply": _handle_directly(utterance), "tier": "instant", "seconds": 0}


def _project_context() -> str:
    c = db()
    rows = c.execute("SELECT fact FROM project_memory ORDER BY id DESC LIMIT 12").fetchall()
    facts = " ".join(r["fact"] for r in rows)
    return f"What we know about this project: {facts}" if facts else ""


def _handle_directly(u: str) -> str:
    """The six things the mouth may answer alone. Everything else already went upstairs."""
    c = db()
    low = u.lower()

    if any(w in low for w in ("overnight", "last night", "what ran", "what shipped", "what happened")):
        rows = c.execute(
            "SELECT status, COUNT(*) n FROM jobs WHERE created_at >= datetime('now','-1 day') "
            "GROUP BY status"
        ).fetchall()
        if not rows:
            return "Nothing ran overnight."
        counts = {r["status"]: r["n"] for r in rows}
        bits = []
        if counts.get("shipped"): bits.append(f"{counts['shipped']} finished")
        if counts.get("review"):  bits.append(f"{counts['review']} waiting on the others")
        if counts.get("failed"):  bits.append(f"{counts['failed']} broke")
        return (", ".join(bits) + ".") if bits else "Nothing finished overnight."

    if any(w in low for w in ("status", "going", "doing", "update")):
        row = c.execute(
            "SELECT j.task_name, j.status, j.builder_seat FROM jobs j "
            "WHERE j.status IN ('in_progress','review') ORDER BY j.id DESC LIMIT 1"
        ).fetchone()
        if not row:
            return "Nothing's running right now."
        who = row["builder_seat"].title()
        thing = row["task_name"] or "it"
        return (f"{who} is on {thing}." if row["status"] == "in_progress"
                else f"{who} finished {thing}. The others are checking it.")

    return "Got it."


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "web" / "index.html").read_text()
