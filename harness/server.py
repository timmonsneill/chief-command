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
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


# ---------------------------------------------------------------------------
# Keys live in ~/.chief/env (chmod 600, never in the repo). Load them here so the
# server can mint voice tokens and reach the seats however it was launched. This
# is the intended config path — the file, not a hardcode. Existing environment
# values win, so a key set in the shell is never clobbered.
# ---------------------------------------------------------------------------
def _load_chief_env() -> None:
    env_file = Path.home() / ".chief" / "env"
    if not env_file.exists():
        return
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_chief_env()

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from db.jobs import connect, init_db, month_spend

app = FastAPI(title="Chief Command")

DB = Path(__file__).resolve().parent / "db" / "chief.db"

FAMILY_COLOR = {"claude": "#FF8A3D", "gpt": "#00E5A0", "grok": "#B06CFF", "qwen": "#2E9BFF"}


_SEATS_SYNCED = False


def db():
    c = connect(DB)
    init_db(c)
    _sync_seats_once(c)
    return c


def _sync_seats_once(c) -> None:
    """Push seats.toml into the store on the first request after a restart.

    This was never wired up, so the seat definitions only reached the live database when
    somebody ran it by hand — meaning an edit to the config (a new reviewer, a seat
    switched off) could sit there looking applied and do nothing. It also runs the
    startup validation: a gauntlet naming a seat that doesn't exist, or a seat with no
    model family, is refused loudly here instead of failing later, mid-panel.
    """
    global _SEATS_SYNCED
    if _SEATS_SYNCED:
        return
    _SEATS_SYNCED = True          # set first: a broken config must not retry every request
    try:
        import dispatch
        dispatch.sync_seats(c, dispatch.load_config())
    except Exception as exc:      # noqa: BLE001 — the app must still serve the record
        print(f"[chief] seat config was not applied: {exc}")


# ── The plain-English layer. He must never see a machine word. ───────────────
#
# Sol caught the UI leaking filenames and shell commands straight into the detail view.
# This is the translation layer that stops it. The rule from AGENTS.md:
#
#     "If you cannot explain what you did without jargon, you do not yet understand
#      what you did."

_EVENT_ENGLISH = {
    "dispatched": "Picked it up",
    "read":       "Read through the existing code",
    "thinking":   "Worked out how to do it",
    "edit":       "Changed the code",
    "write":      "Wrote new code",
    "command":    "Ran something",
    "test_run":   "Ran the tests",
    "browse":     "Clicked through it like a user",
    "verdict":    "Gave a verdict",
    "done":       "Finished",
    "error":      "Hit a problem",
    # A reviewer that sat out MUST NOT read as one that worked. Without this line it
    # fell through to the default "Worked on it" — the record was honest about the
    # panel shrinking and the screen quietly contradicted it. (task #10)
    "skipped":    "Sat this one out",
}


def _in_plain_english(e: dict) -> dict:
    """Translate one line of machine activity into something a person can read.

    NOTE the deliberate asymmetry: we keep the OUTCOME of a test run ("6 passed") but
    drop the COMMAND that produced it ("pytest -q"). The outcome is a fact about the
    work. The command is a fact about the tooling, and Neill doesn't use the tooling.
    """
    kind = e.get("kind", "")
    what = _EVENT_ENGLISH.get(kind, "Worked on it")
    out = {"what": what, "kind": kind}

    detail = (e.get("detail") or "").strip()
    # Keep a detail ONLY if it reads like a RESULT, not a machine artifact — and not if
    # it's just restating the line it's attached to.
    looks_technical = any(c in detail for c in ("/", "\\", ".py", ".ts", ".sql", "()", "--"))
    is_redundant = detail.lower().strip(".") == what.lower().strip(".")
    if detail and not looks_technical and not is_redundant:
        out["note"] = detail
    return out


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
            "SELECT reviewer_seat, reviewer_tier, model_family, role, verdict, summary "
            "FROM verdicts WHERE job_id=? ORDER BY id", (j["id"],)
        )]
        for v in j["verdicts"]:
            v["color"] = FAMILY_COLOR.get(v["model_family"], "#7E90A8")
        # ⚠️ Sol's review: "The web app explicitly leaks filenames, commands, model
        # names, and jargon. The detailed work view displays raw event type, target,
        # and detail. Those fields are DESIGNED to contain filenames, commands, web
        # addresses, and machine output. The plain-English requirement is contradicted
        # by the interface itself."
        #
        # He's right, and it broke the one promise that matters most. So the API now
        # TRANSLATES events before they leave the server. The raw fields still exist in
        # the database — they're what an engineer would need — but they never reach
        # Neill's screen.
        j["events"] = [_in_plain_english(dict(e)) for e in c.execute(
            "SELECT kind, target, detail, lane, model FROM events WHERE job_id=? ORDER BY id",
            (j["id"],)
        )]
        # Why is this stuck? In words he can act on.
        j["blocked_because"] = _why_blocked(c, j)
        jobs.append(j)

    from db.planning import todos_for, attachments_for

    projects = [dict(r) for r in c.execute("SELECT * FROM projects WHERE archived=0")]
    for p in projects:
        p["memory"] = [dict(m) for m in c.execute(
            "SELECT kind, fact FROM project_memory WHERE project_id=? ORDER BY id DESC LIMIT 30",
            (p["id"],)
        )]
        p["plan"] = [dict(x) for x in c.execute(
            "SELECT * FROM plan_items WHERE project_id=? ORDER BY position, id", (p["id"],)
        )]
        p["todos"] = todos_for(c, p["id"])
        p["todo_open"] = c.execute(
            "SELECT COUNT(*) n FROM todos WHERE project_id=? AND done=0", (p["id"],)
        ).fetchone()["n"]
        p["attachments"] = attachments_for(c, p["id"])
        p["job_count"] = c.execute(
            "SELECT COUNT(*) n FROM jobs WHERE project_id=?", (p["id"],)
        ).fetchone()["n"]

    return {"seats": seats, "jobs": jobs, "projects": projects,
            "usage_week": _usage_week()}


# What the subscription seats have used this week, in words. Read from the tools' own
# session logs on disk (harness/usage_local.py) — those can be gigabytes, so this is
# computed at most once every few minutes, never on every dashboard poll.
_USAGE_CACHE: dict = {"at": 0.0, "value": None}
_USAGE_TTL_S = 300


def _usage_week() -> dict:
    import time
    now = time.monotonic()
    if _USAGE_CACHE["value"] is not None and now - _USAGE_CACHE["at"] < _USAGE_TTL_S:
        return _USAGE_CACHE["value"]
    try:
        from usage_local import in_plain_english, local_usage
        u = local_usage(days=7)
        value = {
            "summary": in_plain_english(u, 7).splitlines(),
            "families": {
                fam: {"sessions": x.sessions, "words_written": int(x.output_tokens * 0.75),
                      "allowance_used_percent": x.allowance_used_percent}
                for fam, x in u.items()
            },
        }
    except Exception as exc:  # noqa: BLE001 — the dashboard must never die on this
        value = {"summary": ["Couldn't read this week's usage right now."],
                 "families": {}, "error": str(exc)[:120]}
    _USAGE_CACHE.update(at=now, value=value)
    return value


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
        if any(v["verdict"] == "fail" for v in j["verdicts"]):
            return "A reviewer sent this back. It needs another go."
        need = j["required_reviews"] or 0
        got = len({v["reviewer_seat"] for v in j["verdicts"] if v["verdict"] == "pass"})
        if need and got < need:
            return f"Waiting on the others — {got} of {need} have looked at it."
        # The seat count can be satisfied while the FAMILY floor isn't — three seats
        # that are all the same underlying model is one mind in three hats. Say which
        # wall it's actually behind, or the page reads "2 of 2 done" next to a job that
        # is visibly not moving. (task #10)
        need_fams = j["required_review_families"] or 0
        fams = {v["model_family"] for v in j["verdicts"] if v["verdict"] == "pass"}
        if need_fams and len(fams) < need_fams:
            return (f"Only {len(fams)} of {need_fams} different models have checked it — "
                    "it needs a second opinion from a different kind of model.")
    return None


@app.get("/api/willthink")
async def will_think(q: str = ""):
    """Will this need thinking? Asked BEFORE we start, so the UI knows whether to show a
    holding line at all.

    Sol, round 2, #17: "The browser always says 'Let me think about that' before it knows
    whether thinking is needed, including for 'stop', 'cancel', greetings, and status
    checks. This makes instant commands feel delayed and falsely implies that
    cancellation is being processed."

    Dead right, and it's a nasty little bug — telling a man you're pondering his "stop"
    is exactly the wrong impression to give.
    """
    from mouth import needs_the_brain, cover_the_gap
    if needs_the_brain(q or ""):
        return {"thinking": True, "holding": cover_the_gap()}
    return {"thinking": False}


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
    """What Chief needs to know before answering: the REAL project list first (so it
    stops improvising what Neill is working on), then loose facts we've gathered."""
    from db.projects import projects_context

    c = db()
    parts = []
    real = projects_context(c)
    if real:
        parts.append(real)
    rows = c.execute("SELECT fact FROM project_memory ORDER BY id DESC LIMIT 12").fetchall()
    facts = " ".join(r["fact"] for r in rows)
    if facts:
        parts.append(f"What we know about this project: {facts}")
    return "\n\n".join(parts)


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


# ═══════════════════════════════════════════════════════════════════════════════
# DISPATCH — where "putting someone on it" stops being a figure of speech.
#
# This starts a REAL worker. Today it runs the free local coder, so the whole loop
# can be exercised with no money and no keys: you type a job, a real model builds it
# in its own isolated copy of the code, and a stronger model checks it before it can
# be called done. The paid coding seats will hang off this same path through OpenClaw.
# ═══════════════════════════════════════════════════════════════════════════════
def _pick_local_builder(c) -> str | None:
    """The free local coder, whatever it's named in this database."""
    row = c.execute(
        "SELECT id FROM seats WHERE provider = 'ollama' AND enabled = 1 ORDER BY id LIMIT 1"
    ).fetchone()
    return row["id"] if row else None


# Who reviews is NOT the server's decision any more (task #10). It used to hand-pick a
# single Claude seat here, which quietly made "one reviewer" the real policy no matter
# what the gauntlet config said. The panel now comes from the config, and the family
# floor decides whether it's enough.


@app.post("/api/dispatch")
async def dispatch_endpoint(request: Request):
    """Give the team a real job. Non-blocking: returns as soon as it's recorded and
    the worker has started, so the page stays live while the work grinds."""
    import dispatch as dispatch_mod

    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Say what to build."}, status_code=400)
    # The client stamps a nonce so a double-tap or a re-send can't start it twice.
    nonce = (body.get("nonce") or "").strip() or None

    c = db()
    builder = _pick_local_builder(c)
    if not builder:
        return JSONResponse(
            {"error": "The free local coder isn't available on this machine right now."},
            status_code=503,
        )
    try:
        d = dispatch_mod.dispatch_local(
            c, text, builder, origin="text", dispatch_key=nonce,
        )
    except dispatch_mod.DispatchRefused as exc:
        return JSONResponse({"error": str(exc)}, status_code=409)

    who = builder.title()
    if d.reused:
        reply = f"Already on it — {who} picked that up a moment ago."
    else:
        reply = (f"{who} is on it. A panel of different models will check the work "
                 "before it's called done.")
    return {"job_id": d.job_id, "reused": d.reused, "reply": reply, "builder": builder}


# ═══════════════════════════════════════════════════════════════════════════════
# TODOS — the checklist that lives in the command center, grouped by project and by
# the owner's own sections ("Now", "Later", "Post-launch"). No more jumping windows.
# ═══════════════════════════════════════════════════════════════════════════════
@app.post("/api/projects/{project_id}/todos")
async def add_todo_endpoint(project_id: str, request: Request):
    from db.planning import add_todo
    body = await request.json()
    text = (body.get("text") or "").strip()
    if not text:
        return JSONResponse({"error": "Say what the todo is."}, status_code=400)
    c = db()
    if c.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
        return JSONResponse({"error": "No such project."}, status_code=404)
    tid = add_todo(c, project_id, text,
                   section=body.get("section"), owner_only=bool(body.get("owner_only")))
    return {"id": tid}


@app.post("/api/todos/{todo_id}/toggle")
def toggle_todo_endpoint(todo_id: int):
    from db.planning import toggle_todo
    toggle_todo(db(), todo_id)
    return {"ok": True}


@app.post("/api/todos/{todo_id}/delete")
def delete_todo_endpoint(todo_id: int):
    from db.planning import delete_todo
    delete_todo(db(), todo_id)
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════════════════════
# PROJECT MEMORY — a project's own accumulated notes, readable inside Chief Command.
# We serve the curated MEMORY.md index (the agents ⭐-mark what matters) and any one
# named file from it. These are NOTES — conventions, rulings, architecture. A project's
# live data (e.g. Arch's patient records) is not, and must never be, reachable here.
# ═══════════════════════════════════════════════════════════════════════════════
@app.get("/api/projects/{project_id}/memory")
def project_memory_index(project_id: str):
    from db.projects import memory_index
    text = memory_index(db(), project_id)
    if text is None:
        return JSONResponse({"error": "This project has no readable memory."},
                            status_code=404)
    return {"project_id": project_id, "index": text}


@app.get("/api/projects/{project_id}/memory/{name}")
def project_memory_file(project_id: str, name: str):
    from db.projects import memory_file
    text = memory_file(db(), project_id, name)
    if text is None:
        return JSONResponse({"error": "No such memory."}, status_code=404)
    return {"project_id": project_id, "name": name, "text": text}


# ═══════════════════════════════════════════════════════════════════════════════
# UPLOADS — images and files the owner drops in, pinned to a project. Bytes live on
# disk (gitignored); a row in `attachments` is how the UI finds them.
# ═══════════════════════════════════════════════════════════════════════════════
UPLOADS = Path(__file__).resolve().parent / "uploads"
_IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".heic"}


@app.post("/api/upload")
async def upload_endpoint(
    file: "UploadFile" = File(...),
    project_id: str = Form(None),
    job_id: int = Form(None),
):
    from db.planning import add_attachment

    UPLOADS.mkdir(exist_ok=True)
    raw = await file.read()
    # A safe on-disk name — never trust the client's path. Keep the extension so the
    # browser renders images; prefix with the attachment id after insert for uniqueness.
    safe = "".join(ch for ch in (file.filename or "file")
                   if ch.isalnum() or ch in "._- ").strip() or "file"
    ext = Path(safe).suffix.lower()
    kind = "image" if ext in _IMAGE_EXT else "file"

    c = db()
    # Insert first to get an id, then write bytes under an unambiguous name.
    att_id = add_attachment(c, filename=safe, stored_path="", kind=kind,
                            project_id=project_id or None,
                            job_id=int(job_id) if job_id else None,
                            size_bytes=len(raw))
    dest = UPLOADS / f"{att_id}_{safe}"
    dest.write_bytes(raw)
    c.execute("UPDATE attachments SET stored_path=? WHERE id=?", (str(dest), att_id))
    return {"id": att_id, "filename": safe, "kind": kind, "size_bytes": len(raw)}


@app.get("/api/attachments/{att_id}")
def get_attachment(att_id: int):
    row = db().execute(
        "SELECT filename, stored_path, kind FROM attachments WHERE id=?", (att_id,)
    ).fetchone()
    if row is None or not row["stored_path"] or not Path(row["stored_path"]).exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(row["stored_path"], filename=row["filename"])


# ═══════════════════════════════════════════════════════════════════════════════
# VOICE
# ═══════════════════════════════════════════════════════════════════════════════
import os  # noqa: E402

import httpx  # noqa: E402

from chief import ASK_CHIEF_TOOL, MOUTH_INSTRUCTIONS, ask_chief  # noqa: E402

VOICE_MODEL = "gpt-realtime-2.1"   # the FULL model, NOT mini.
# Mini has an open, unresolved bug where it refuses to call function tools. For a
# telephone whose entire job is placing one call, that is fatal — you'd speak into
# the car and nothing would happen. ~$50/mo more to remove the single point of
# failure from the whole system. Cheapest insurance on the board.


@app.get("/api/voice/token")
async def voice_token():
    """Mint a SHORT-LIVED client token so the real API key never reaches the browser.

    The browser talks straight to OpenAI (that's what keeps the audio fast — it doesn't
    bounce through this Mac). But it must never hold the key that can spend money, so
    it gets a token that expires in a minute and can do nothing but start one session.
    """
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        return JSONResponse({"error": "no OpenAI key configured"}, status_code=503)

    # Refuse to start a session we can't afford. Sol: "a safe failure mode when Chief
    # is unavailable" — same principle for the money. Better to not start than to be
    # cut off mid-sentence by OpenAI's own cap.
    m = month_spend(db())
    if m["spent_cents"] >= m["cap_cents"]:
        return JSONResponse(
            {"error": f"You're at your ${m['cap_cents']/100:.0f} monthly limit. "
                      "Voice is off until you raise it."},
            status_code=402,
        )

    async with httpx.AsyncClient(timeout=20) as http:
        r = await http.post(
            "https://api.openai.com/v1/realtime/client_secrets",
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "session": {
                    "type": "realtime",
                    "model": VOICE_MODEL,
                    "instructions": MOUTH_INSTRUCTIONS,
                    "tools": [ASK_CHIEF_TOOL],
                    # FORCE the call (Sol's bug #1). With "auto" the mouth could decide a
                    # "yes" or a status question wasn't worth sending and answer it itself
                    # — which is exactly the judgment that leaked twice. Required means
                    # every single thing Neill says goes to Chief. The mouth cannot
                    # freelance.
                    "tool_choice": "required",
                    "audio": {
                        "input": {
                            # SEMANTIC turn detection, not plain silence detection.
                            # A driver pauses mid-sentence to merge; silence-based VAD
                            # cuts him off. Semantic judges whether he SOUNDED finished.
                            "turn_detection": {"type": "semantic_vad", "eagerness": "low",
                                               "interrupt_response": True},
                        },
                        "output": {"voice": "cedar"},
                    },
                }
            },
        )
    if r.status_code >= 400:
        return JSONResponse({"error": r.text[:300]}, status_code=r.status_code)
    return r.json()


# The live conversation behind the voice. ONE session, held for the whole conversation,
# so "yeah" / "no, the other one" mean something and there's no 3-5s process relaunch on
# every utterance (that relaunch was the whole reason talking felt like hanging up and
# redialling). See chief_live.py. Serialized with a lock: one mouth, one thread of
# thought at a time. Rebuilt if it dies so a bad turn can't wedge the conversation.
import asyncio  # noqa: E402

from chief import CHIEF_MODEL as _CHIEF_MODEL  # noqa: E402

_chief_session = None
_chief_lock = asyncio.Lock()


def _live_session():
    """Lazily build the live Chief, seeded with the real project list.

    The project list is baked in ONCE, when the session is born, because a held session
    IS the point — it's what makes "yeah" cost a second instead of eight. The trade: a
    project renamed mid-conversation won't reach Chief until the session next rebuilds
    (on error, or a fresh boot). That's the right call for a single owner talking; the
    list rarely changes inside one drive.
    """
    global _chief_session
    if _chief_session is None:
        from chief_live import ChiefSession
        from db.projects import projects_context
        _chief_session = ChiefSession(extra_context=projects_context(db()))
    return _chief_session


@app.post("/api/voice/ask")
async def voice_ask(request: Request):
    """The mouth's ONE tool. Everything Neill says arrives here and goes to Chief.

    The mouth decides nothing. It doesn't even decide whether something is worth
    sending — because "is this worth sending?" is a judgment, and judgments are where
    this leaked twice.
    """
    body = await request.json()
    said = (body.get("said") or "").strip()
    if not said:
        return {"spoken": "I didn't catch that."}

    from mouth import is_pushback
    pushed_back = is_pushback(said)

    # The fast path: the LIVE streaming session (metered API, but text is pennies, and
    # it's the difference between a one-second "yeah" and an eight-second one). Falls
    # back to the free-but-slow subprocess brain when there's no API key to think with.
    if os.environ.get("OPENAI_API_KEY"):
        async with _chief_lock:
            session = _live_session()
            try:
                pieces: list[str] = []
                async for sentence in session.say(said, deep=pushed_back):
                    pieces.append(sentence)
                answer = " ".join(pieces).strip()
            except Exception:
                # A wedged session must not wedge the conversation. Drop it; the next
                # utterance rebuilds a clean one. We do NOT silently guess an answer.
                global _chief_session
                _chief_session = None
                answer = ""
        if answer:
            from chief import _for_speech
            out = {"spoken": _for_speech(answer), "full": answer,
                   "model": _CHIEF_MODEL, "failed": False}
        else:
            out = {"spoken": "Something went wrong on my end. Nothing's started.",
                   "full": "Chief returned nothing. No work was dispatched.",
                   "model": _CHIEF_MODEL, "failed": True}
    else:
        # No live session to carry the thread, so the fallback must be handed BOTH the
        # real project list AND what they were just talking about — otherwise it loses the
        # per-turn continuity the streaming path keeps in session memory.
        talking_about = (body.get("context") or "").strip()
        context = "\n\n".join(p for p in (_project_context(), talking_about) if p)
        out = ask_chief(said, context=context, pushed_back=pushed_back)

    # Everything Chief says goes on the record, so the text channel always has the full
    # version of anything he only half-heard in the car.
    c = db()
    c.execute(
        "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
        "SELECT id, 'chief', 'chief', ?, 'gpt', 'thinking', ? FROM jobs ORDER BY id DESC LIMIT 1",
        (out.get("model", _CHIEF_MODEL), out["full"][:500]),
    )
    return out


@app.get("/voice", response_class=HTMLResponse)
def voice_page():
    return (Path(__file__).resolve().parent / "web" / "voice.html").read_text()


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).resolve().parent / "web" / "index.html").read_text()


# ---------------------------------------------------------------------------
# PWA plumbing — lets Neill "install" the command center as a desktop/phone app
# straight from the browser. No service worker on purpose: the app is useless
# without its server anyway, and a stale offline cache showing yesterday's fleet
# as if it were live would be a lie of exactly the kind this harness exists to
# prevent.
# ---------------------------------------------------------------------------
_WEB = Path(__file__).resolve().parent / "web"


@app.get("/manifest.json")
def manifest():
    return FileResponse(_WEB / "manifest.json", media_type="application/manifest+json")


@app.get("/icon.svg")
def icon_svg():
    return FileResponse(_WEB / "icon.svg", media_type="image/svg+xml")


@app.get("/icon-192.png")
def icon_192():
    return FileResponse(_WEB / "icon-192.png", media_type="image/png")


@app.get("/icon-512.png")
def icon_512():
    return FileResponse(_WEB / "icon-512.png", media_type="image/png")


if __name__ == "__main__":
    import uvicorn

    # Loopback only. Reaching this from the phone goes through Tailscale
    # (`tailscale serve`), never a public bind — AGENTS.md rule 3.
    uvicorn.run(app, host="127.0.0.1", port=8787)
