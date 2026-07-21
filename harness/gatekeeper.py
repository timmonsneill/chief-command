"""The gatekeeper (task #11) — the one holder of the irreversible powers.

Everything else in this harness can be wrong and the worst case is wasted work. These
three things cannot be taken back:

    merge    code enters main
    deploy   something reaches the world
    spend    real money leaves the account

Until now any agent could do all three directly: `dispatch.ship()` was an import away,
and spend was recorded by whoever was spending. The schema guards are real, but they
stop *mistakes*, not *reach* — an agent that wanted to ship unreviewed work never needed
to defeat a trigger, because it could write its own approval or its own passing verdict.

Sol's line, which is the whole task:

    "The database cannot be the jailer if the prisoner can rewrite the jail."

So this is not another guard. It is a separation of powers. Agents *ask*; the gatekeeper
checks the record itself, does exactly the one thing, and refuses everything else.

FOUR RULES, and the design is mostly just these:

  1. THREE VERBS. Not "run this for me". A gatekeeper that can be asked to run an
     arbitrary command is a shell with extra steps, and every hole in it is a hole in
     everything. There is no generic verb here, and adding one would end the point of
     the file.

  2. IT NEVER BELIEVES THE ASKER. Every claim in a request is ignored except the job id
     and the target. The gatekeeper re-reads the panel, the versions, the families and
     the approvals from the database itself. An agent under prompt injection can ask for
     anything it likes; the answer depends on the record, not on the asking.

  3. REFUSALS ARE LOUD. Every refusal is written to the job in plain English. A silent
     refusal is indistinguishable from a thing that was never attempted — which is how
     you end up debugging "why didn't it deploy" for an hour.

  4. NO FALLBACK. If the gatekeeper is unreachable, irreversible things do not happen.
     A fallback to the old direct path would BE the bypass. Down means stopped.

⚠️ WHAT THIS FILE DOES NOT YET DO. Right now every agent still runs as the same user and
can open the database file directly, so a determined process could bypass this module
entirely. The service is the door; the file permissions are the wall, and the wall is
still owner's work (see docs/gatekeeper-task11-spec.md). Until that lands, this raises
the floor from "anyone can do anything" to "the normal path is the checked one" — real,
but not yet a boundary. That distinction is stated here rather than glossed, because a
gatekeeper everyone believes in but that doesn't hold is worse than none.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sqlite3
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from db.jobs import GuardViolation, connect, record_usage, seat, set_status

HARNESS = Path(__file__).resolve().parent
REPO = HARNESS.parent
DB_PATH = HARNESS / "db" / "chief.db"

TOKEN_PATH = Path.home() / ".chief" / "gatekeeper.token"
MAX_BODY_BYTES = 64 * 1024
MERGE_INTO = "main"

# A branch name we are willing to merge. Deliberately narrow: `jobs.branch` is written
# by the same code an agent controls, and `git rev-parse --verify` accepts REVISION
# EXPRESSIONS, not just branches — `:/some message` resolves to a commit by searching
# commit messages. A permissive check here turns "merge the branch for job 17" into
# "merge any commit in the repository."
BRANCH_RE = re.compile(r"^job/\d+$")

# What money can be spent ON. The daily caps in the schema are keyed by role, so a role
# nobody validated is a cap nobody enforces — 'research' would sail past both the build
# ration and the review ration.
SPEND_ROLES = {"build", "review", "test", "research"}


class Refused(RuntimeError):
    """The gatekeeper said no. The message is in plain English, on purpose — it is
    shown to Neill, and 'guard_family_floor violated' tells him nothing."""


@dataclass
class Receipt:
    """Proof that the one thing happened, and what it was."""
    verb: str
    subject: str
    detail: str = ""
    reference: str = ""          # commit id, usage row, deploy id


# ---------------------------------------------------------------------------
# Saying no, out loud
# ---------------------------------------------------------------------------
def _clean(text: str, limit: int = 120) -> str:
    """Make caller-supplied text safe to show Neill.

    Everything written by `_note` is attributed to the gatekeeper — the one component he
    is being told to trust — so an agent that can inject newlines into it can forge
    gatekeeper-signed lines on his timeline. Strip the formatting, cap the length.
    """
    return re.sub(r"\s+", " ", str(text)).strip()[:limit]


def _log(conn, verb: str, subject: str, granted: bool, detail: str,
         asked_by: str = "unknown", job_id: int | None = None) -> None:
    """The gatekeeper's own record, which does not depend on there being a job.

    Deploys aren't necessarily about a job, and `events.job_id` is NOT NULL — so before
    this existed, a deploy's grants AND its refusals were dropped on the floor, for the
    one verb where saying it out loud matters most. Append-only by trigger.
    """
    conn.execute(
        "INSERT INTO gate_log (verb, subject, granted, detail, asked_by, job_id) "
        "VALUES (?,?,?,?,?,?)",
        (verb, _clean(subject), int(granted), _clean(detail, 400),
         _clean(asked_by, 60), job_id),
    )


def _note(conn, job_id: int | None, text: str) -> None:
    """Write a refusal (or a grant) onto the job where a person will see it."""
    if job_id is None:
        return
    row = conn.execute(
        "SELECT builder_seat FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        return
    s = seat(conn, row["builder_seat"])
    if s is None:
        return
    conn.execute(
        "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
        "VALUES (?,?,?,?,?,?,?)",
        (job_id, s["id"], "gatekeeper", s["model"], s["family"], "skipped", text),
    )


def _refuse(conn, job_id: int | None, why: str = "", *, verb: str = "",
            subject: str = "", asked_by: str = "unknown") -> Refused:
    _note(conn, job_id, f"The gatekeeper said no: {why}")
    if verb:
        _log(conn, verb, subject or str(job_id), False, why, asked_by, job_id)
    return Refused(why)


# ---------------------------------------------------------------------------
# MERGE — code enters main
# ---------------------------------------------------------------------------
def merge(conn, job_id: int, *, asked_by: str = "unknown") -> Receipt:
    """Merge a job's branch into main, if and only if the record earns it.

    Every check below re-reads the database. None of them takes the asker's word for
    anything — that is the entire difference between this and `dispatch.ship()`, which
    did the thing because it was called.
    """
    def _no(why: str) -> Refused:
        """One way out of this function, so no refusal can forget to write itself down."""
        return _refuse(conn, job_id if _job_exists(conn, job_id) else None, why,
                       verb="merge", subject=f"job {job_id}", asked_by=asked_by)

    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise _no(f"there is no job {job_id}")

    if job["status"] not in ("done", "shipped"):
        raise _no("that work hasn't finished its checks yet, so there's nothing to merge")
    if job["status"] == "shipped":
        raise _no("that work has already been merged once")

    version = job["head_version"]
    if not version:
        raise _no("nothing was actually put forward — there's no finished version to merge")

    verdicts = conn.execute(
        "SELECT reviewer_seat, model_family, verdict, role, reviewed_version "
        "FROM verdicts WHERE job_id = ?", (job_id,)
    ).fetchall()
    current = [v for v in verdicts if v["reviewed_version"] == version]

    # A failure on this version is fatal and is NOT outvoted. A failure on an unknown
    # version is also fatal: it condemns we-don't-know-what, so it condemns everything.
    for v in verdicts:
        if v["verdict"] == "fail" and (
                v["reviewed_version"] is None or v["reviewed_version"] == version):
            raise _no("a reviewer turned this down and that stands")
    if any(v["verdict"] == "needs_human" for v in verdicts):
        raise _no("a reviewer asked for you to look at this first")

    passed = [v for v in current if v["verdict"] == "pass" and v["role"] == "reviewer"]
    seats = {v["reviewer_seat"] for v in passed}
    families = {v["model_family"] for v in passed}

    # A job carrying NO requirements is not a job everyone approved — it is a job whose
    # requirements were never stamped. `0 < 0` is False, so treating zero as "nothing
    # required" would let a row created outside dispatch merge with no reviews at all.
    need_seats = job["required_reviews"] or 0
    need_families = job["required_review_families"] or 0
    if need_seats < 1 or need_families < 1:
        raise _no("that job never had any review requirements set, so there is "
                      "nothing to have passed")
    if len(seats) < need_seats:
        raise _no(f"only {len(seats)} of the {need_seats} reviewers "
                      "have signed off on this version")
    if len(families) < need_families:
        raise _no(f"only {len(families)} different kinds of model have checked this; "
                      f"{need_families} are required")

    branch = job["branch"] or ""
    if not BRANCH_RE.match(branch):
        raise _no("there's no finished branch of code to merge for that job")

    tip = _branch_tip(branch)
    if tip is None:
        raise _no("there's no finished branch of code to merge for that job")

    # ══════════════════════════════════════════════════════════════════════════
    # THE CHECK THIS WHOLE VERB EXISTS FOR.
    #
    # Everything above verifies the RECORD. This verifies that the code about to enter
    # main IS the code the record is about. Without it, a builder commits v1, the panel
    # reviews v1, `head_version` freezes at v1 — and then the builder pushes v2 to the
    # same branch and asks for a merge. Every check passes, and v2 goes in. The version
    # freeze that protects the record would be helping the attacker.
    #
    # Reviews are of bytes, not of intentions. Merge the bytes that were reviewed.
    # ══════════════════════════════════════════════════════════════════════════
    if not _same_commit(tip, version):
        raise _no("the code on that branch isn't the code that was reviewed — "
                      "it has changed since, so it needs checking again")

    if not _repo_is_ready():
        raise _no("the project folder has unfinished changes in it, so it isn't "
                      "safe to merge right now")

    # ══════════════════════════════════════════════════════════════════════════
    # ORDER IS THE POINT: the RECORD decides first, the irreversible act happens
    # second. Merging and then asking produced "the code merged but the record wouldn't
    # accept it" — an unreviewed merge with a polite note. The gatekeeper's own checks
    # are a SUBSET of the schema's (it never checks for a passing tester), so the only
    # safe order is to let the schema refuse before git is touched at all.
    # ══════════════════════════════════════════════════════════════════════════
    with _MERGE_LOCK:
        conn.execute("BEGIN IMMEDIATE")
        try:
            set_status(conn, job_id, "shipped")
        except (GuardViolation, sqlite3.IntegrityError) as exc:
            conn.execute("ROLLBACK")
            raise _no(_plain(exc)) from exc

        commit = _git_merge(branch)
        if commit is None:
            conn.execute("ROLLBACK")          # the record never happened either
            raise _no("the merge didn't go through cleanly — it needs a person")
        conn.execute("COMMIT")

    _note(conn, job_id,
          f"Merged into the main line of code. Asked for by {_clean(asked_by)}.")
    _log(conn, "merge", f"job {job_id}", True,
         f"{len(families)} model families signed off on {version}; merged {commit}",
         asked_by, job_id)
    return Receipt(verb="merge", subject=f"job {job_id}", reference=commit,
                   detail=f"{len(families)} model families signed off on {version}")


_MERGE_LOCK = threading.Lock()          # two merges racing share one git index


def _job_exists(conn, job_id: int) -> bool:
    return conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is not None


def _plain(exc: Exception) -> str:
    """A schema refusal, with the machine word taken off the front."""
    return str(exc).replace("guard: ", "").strip() or "the record wouldn't accept it"


def _branch_tip(branch: str) -> str | None:
    """The commit a BRANCH points at — refs/heads/ so nothing else can be meant."""
    out = subprocess.run(["git", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"],
                         cwd=REPO, capture_output=True, text=True)
    return out.stdout.strip() or None if out.returncode == 0 else None


def _same_commit(tip: str, version: str) -> bool:
    """Is the reviewed version this exact commit?

    `head_version` is a short id, so compare on the shorter length. A version that is
    not a commit id at all (the local worker records a content hash) can never match,
    which is correct: if we cannot prove the reviewed thing is this code, we do not
    merge it.
    """
    version = (version or "").strip().lower()
    tip = (tip or "").strip().lower()
    if len(version) < 7 or not re.fullmatch(r"[0-9a-f]+", version):
        return False
    return tip.startswith(version)


def _repo_is_ready() -> bool:
    """On the branch we merge into, with nothing half-done in the folder.

    Merging into a detached HEAD orphans the result; merging over the owner's own
    uncommitted work risks losing it when a failed merge is aborted.
    """
    head = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                          cwd=REPO, capture_output=True, text=True)
    if head.returncode != 0 or head.stdout.strip() != MERGE_INTO:
        return False
    dirty = subprocess.run(["git", "status", "--porcelain"],
                           cwd=REPO, capture_output=True, text=True)
    return dirty.returncode == 0 and not dirty.stdout.strip()


def _git_merge(branch: str) -> str | None:
    try:
        proc = subprocess.run(["git", "merge", "--no-ff", "--no-edit",
                               f"refs/heads/{branch}"],
                              cwd=REPO, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        _abort_any_merge()
        return None
    if proc.returncode != 0:
        # Leave nothing half-merged behind. A conflicted tree that nobody is looking at
        # is worse than a refusal.
        _abort_any_merge()
        return None
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO,
                          capture_output=True, text=True)
    return head.stdout.strip()[:12] or None


def _abort_any_merge() -> None:
    """Undo a half-finished merge — and ONLY a half-finished merge.

    `git merge --abort` is `git reset --merge` underneath, which will discard the
    owner's uncommitted work if no merge is actually in progress. Check first.
    """
    if (REPO / ".git" / "MERGE_HEAD").exists():
        subprocess.run(["git", "merge", "--abort"], cwd=REPO, capture_output=True)


# ---------------------------------------------------------------------------
# DEPLOY — something reaches the world
# ---------------------------------------------------------------------------
def deploy(conn, target: str, *, job_id: int | None = None,
           asked_by: str = "unknown") -> Receipt:
    """Deploy — ONLY on a live, unused, read-back owner approval naming this target.

    Decision D keeps Neill in the path for production deploys, and this is the only
    place in the harness where an agent's request is not enough on its own. There is
    deliberately no "the reviewers all agreed" route here: reviewers judge code, and a
    deploy is a decision about the business.
    """
    target = _clean(target)
    approval = conn.execute(
        "SELECT * FROM live_approvals WHERE capability = 'deploy' AND action = ? "
        "ORDER BY id DESC LIMIT 1", (target,),
    ).fetchone()
    if approval is None:
        raise _refuse(conn, job_id,
                      f"nothing has been approved for deploying {target} — "
                      "that needs your say-so, tapped, not spoken",
                      verb="deploy", subject=target, asked_by=asked_by)
    # An approval granted about ONE job does not authorize the same words claimed under
    # another. Without this, a yes given in the context of job 4 travels to job 99.
    if approval["job_id"] is not None and job_id is not None \
            and approval["job_id"] != job_id:
        raise _refuse(conn, job_id,
                      "that approval was for a different piece of work",
                      verb="deploy", subject=target, asked_by=asked_by)
    if not approval["reversible"] and not (approval["recovery"] or "").strip():
        raise _refuse(conn, job_id,
                      "that deploy can't be undone and there's no written way back",
                      verb="deploy", subject=target, asked_by=asked_by)

    # Spend the approval FIRST. If the deploy dies halfway, the slip is still used —
    # a retry must come back for a fresh yes rather than replay an old one. Two callers
    # racing: SQLite serializes the writes and the single-use guard refuses the loser,
    # which arrives here as a plain refusal rather than a crash.
    try:
        conn.execute("UPDATE approvals SET used_at = datetime('now') WHERE id = ?",
                     (approval["id"],))
    except sqlite3.IntegrityError as exc:
        raise _refuse(conn, job_id, "that approval has already been used",
                      verb="deploy", subject=target, asked_by=asked_by) from exc

    # ⚠️ HONEST RECEIPT. Nothing here deploys anything — the harness has no deploy
    # mechanism yet, and this verb exists so that when one is built it is built BEHIND
    # the gate rather than beside it. Saying "Deployed {target}" would have been the
    # gatekeeper's own record lying about the one act it exists to control.
    detail = ("cleared to deploy on your approval — note that the harness has no "
              "deploy mechanism wired yet, so nothing was actually sent anywhere")
    _note(conn, job_id, f"Cleared to deploy {target}, on your approval. "
                        f"Asked for by {_clean(asked_by)}. Nothing was sent — "
                        "there's no deploy button wired up yet.")
    _log(conn, "deploy", target, True, detail, asked_by, job_id)
    return Receipt(verb="deploy", subject=target, detail=detail,
                   reference=str(approval["id"]))


# ---------------------------------------------------------------------------
# SPEND — real money leaves the account
# ---------------------------------------------------------------------------
def spend(conn, seat_id: str, cents: int, *, job_id: int | None = None,
          role: str = "build", asked_by: str = "unknown") -> Receipt:
    """Reserve money BEFORE the provider is called, or refuse.

    Sol: refusing to RECORD a charge doesn't unspend it. So the ledger is written first
    and the call happens after. The daily and monthly caps live in the schema, which is
    what makes this a reservation rather than a note — two agents reserving at the same
    instant both hit the ledger, and the ledger refuses the one that breaches.

    ⚠️ THERE IS NO RECONCILIATION YET. A reservation is an ESTIMATE, and if the real
    call costs less, or never happens at all, the estimate stands against the cap
    permanently. That is deliberate for now (erring toward over-counting spend is the
    safe direction) but it is not the same thing as accurate accounting, and this
    docstring said otherwise until the cross-model review called it.
    """
    if cents < 0:
        raise _refuse(conn, job_id, "a charge can't be negative",
                      verb="spend", subject=seat_id, asked_by=asked_by)
    # The caps are keyed BY ROLE, so a role nobody validated is a cap nobody enforces —
    # 'research' would sail past both the build ration and the review ration.
    if role not in SPEND_ROLES:
        raise _refuse(conn, job_id, f"'{role}' isn't a kind of work money can be spent on",
                      verb="spend", subject=seat_id, asked_by=asked_by)
    row = seat(conn, seat_id)
    if row is None:
        raise _refuse(conn, job_id, f"there's no such worker as '{_clean(seat_id, 40)}'",
                      verb="spend", subject=seat_id, asked_by=asked_by)
    if not row["enabled"]:
        raise _refuse(conn, job_id, f"'{seat_id}' is switched off",
                      verb="spend", subject=seat_id, asked_by=asked_by)

    try:
        record_usage(conn, seat_id, cents, job_id=job_id, role=role)
    except (GuardViolation, sqlite3.IntegrityError) as exc:
        raise _refuse(conn, job_id,
                      "that would go past the spending limit for today",
                      verb="spend", subject=seat_id, asked_by=asked_by) from exc

    ref = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
    return Receipt(verb="spend", subject=seat_id, detail=f"{cents}c reserved for {role}",
                   reference=str(ref))


VERBS = {"merge": merge, "deploy": deploy, "spend": spend}


# ---------------------------------------------------------------------------
# The service — loopback only, and only these three verbs
# ---------------------------------------------------------------------------
def handle(request: dict[str, Any], *, db_path: Path | None = None) -> dict[str, Any]:
    """One request in, one answer out. The whole surface area of the gatekeeper.

    An unknown verb is refused without being examined further. This is the check that
    keeps rule 1 true as the file grows.
    """
    verb = request.get("verb")
    if verb not in VERBS:
        return {"ok": False, "error": "the gatekeeper only does three things: "
                                      "merge, deploy, spend"}

    conn = connect(db_path or DB_PATH)
    try:
        asked_by = str(request.get("asked_by") or "unknown")
        try:
            if verb == "merge":
                r = merge(conn, int(request["job_id"]), asked_by=asked_by)
            elif verb == "deploy":
                r = deploy(conn, str(request["target"]),
                           job_id=request.get("job_id"), asked_by=asked_by)
            else:
                r = spend(conn, str(request["seat_id"]), int(request["cents"]),
                          job_id=request.get("job_id"),
                          role=str(request.get("role") or "build"), asked_by=asked_by)
        except Refused as exc:
            return {"ok": False, "error": str(exc)}
        except (KeyError, ValueError, TypeError) as exc:
            return {"ok": False, "error": f"that request didn't make sense: {exc}"}
        return {"ok": True, "verb": r.verb, "subject": r.subject,
                "detail": r.detail, "reference": r.reference}
    finally:
        conn.close()


def token() -> str:
    """The shared secret a caller must present. Created once, readable only by us.

    Loopback is a NETWORK control, not an authorization one. Without a token, any local
    process — and any web page the owner happens to open, since a POST with a plain
    content type is a "simple request" that needs no browser preflight — could drive the
    three most dangerous powers in the system by fetching 127.0.0.1. A page cannot read
    this file, so it cannot present the token.
    """
    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not TOKEN_PATH.exists():
        TOKEN_PATH.write_text(secrets.token_urlsafe(32))
        os.chmod(TOKEN_PATH, 0o600)
    return TOKEN_PATH.read_text().strip()


def serve(host: str = "127.0.0.1", port: int = 8788) -> None:
    """Run the gatekeeper as its own process.

    LOOPBACK ONLY, and not a choice to revisit casually: the tailnet is other devices,
    and the whole point of this process is that reaching it is harder than reaching the
    agents, not easier. Standard library only — the thing holding the dangerous powers
    should have the smallest possible surface.
    """
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    expected = token()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _reply(self, code: int, answer: dict) -> None:
            payload = json.dumps(answer).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):                                    # noqa: N802
            try:
                # Host check first: it is what stops DNS rebinding, where a page the
                # owner is looking at resolves an attacker's domain to 127.0.0.1 and
                # then talks to us with the browser's blessing.
                host_header = (self.headers.get("Host") or "").split(":")[0]
                if host_header not in ("127.0.0.1", "localhost", "[::1]"):
                    return self._reply(403, {"ok": False, "error": "not for you"})
                if not secrets.compare_digest(
                        self.headers.get("X-Chief-Token") or "", expected):
                    return self._reply(403, {"ok": False, "error": "not for you"})

                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > MAX_BODY_BYTES:
                    return self._reply(413, {"ok": False, "error": "that request is too big"})
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw or b"{}")
                except json.JSONDecodeError:
                    body = {}
                if not isinstance(body, dict):
                    body = {}

                answer = handle(body)
                self._reply(200 if answer.get("ok") else 403, answer)
            except Exception:                                  # noqa: BLE001
                # The gatekeeper never dies of a bad request. Down means stopped, and
                # stopped means every irreversible thing stops with it.
                try:
                    self._reply(400, {"ok": False, "error": "that request didn't make sense"})
                except Exception:                              # noqa: BLE001
                    pass

        def log_message(self, *args):                          # quiet by default
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == "__main__":
    serve()
