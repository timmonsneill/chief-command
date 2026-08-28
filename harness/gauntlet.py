"""The review panel (spec §6, task #10) — several different minds check the same work,
at the same time, against one frozen version.

Before this file, dispatch ran ONE reviewer. That is enough to close the loop and it is
not the design: the property this project exists to guarantee is that *a different mind*
looked, because different model families miss different bugs. One reviewer cannot
provide it, and a panel of three seats that all run on the same model provides it no
better — which is why the floor counts FAMILIES, not seats.

Four things are load-bearing here:

  1. ONE FROZEN BUNDLE. Every reviewer is handed the identical version string and the
     identical code. Nothing re-reads the job row mid-panel. Two verdicts must never
     bind to two different versions of "the same" work.

  2. NOTHING IS ASSUMED — only recorded. A family counts when a verdict from that
     family is on the record for THIS version. A reviewer that was skipped, capped out,
     or crashed contributes nothing, and the panel says so out loud (see `runs`).

  3. FAIL CLOSED. Too few families, any failure, any unanswered escalation → the job
     stays parked. The panel never lowers the bar to reach a conclusion, and it never
     forces a status past the database guards. It feeds them.

  4. THE DATABASE IS THE BOUNDARY, NOT THIS FILE. Everything below is also enforced in
     schema.sql as triggers. If this module were wrong, or replaced by something
     careless, the guards would still refuse. That is deliberate: Python decides what to
     TRY; the schema decides what is ALLOWED.

Threads, not async: each reviewer is a subprocess call that blocks for tens of seconds,
and each thread opens its OWN sqlite connection (connections are not shareable across
threads).
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from db.jobs import (
    GuardViolation,
    connect,
    over_budget,
    record_verdict,
    seat,
    set_status,
)

DB_PATH = Path(__file__).resolve().parent / "db" / "chief.db"

REVIEW_TIMEOUT_S = 300          # a reviewer that hasn't answered by now has hung
JOIN_GRACE_S = 30               # how long past its own timeout we wait for a thread
DEFAULT_ESTIMATE_CENTS = 5      # what we reserve for a metered review before calling


# ---------------------------------------------------------------------------
# The reviewers themselves. One function per provider — adding a family to the
# panel is adding a function here and a seat in seats.toml. Nothing else.
#
# LOCKDOWN (task #10 hardening, 2026-08-27). Sol's design gate found the softest target
# in the whole panel: these calls ran on attacker-controllable text (a builder-written
# diff) using the caller's own default CLI settings — ambient tool access, MCP servers,
# extra directory grants, up to and including a medical-records repo grant that had
# nothing to do with this project. And on the way out, `_parse_verdict` (removed below)
# accepted any line starting with the word "PASS" — including one echoed straight out of
# our own prompt, or planted in a diff a builder wrote. Two fixes, together:
#
#   1. Every CLI call below runs with every lockdown flag its `--help` actually offers —
#      no ambient settings, no MCP, no tools, restricted mode. Checked against `claude
#      --help` / `codex exec --help` on this machine, not assumed from memory. See
#      test_reviewer_lockdown.py and assert_reviewers_locked_down() below, which fails
#      loud if a future refactor ever drops one of these flags.
#   2. Nobody scans free-text output for a magic word anymore. Every runner asks its
#      provider for a STRUCTURED answer (claude: --json-schema, codex: --output-schema,
#      xai: response_format=json_object) and reads only the structured field. Anything
#      else — an echo, a narration line, a diff that happens to contain the string
#      "PASS looks great" — is not JSON in the field we read, so it cannot become a
#      verdict. It becomes ReviewerBroke: a skip, never a pass.
# ---------------------------------------------------------------------------
REVIEW_PROMPT = (
    "You are reviewing another model's work. Judge it only by what is shown below: does "
    "it actually do what was asked, and is it correct?\n\n"
    "Answer with a JSON object with exactly these two fields:\n"
    '  "verdict" — the string "pass" (it is correct and does what was asked) or "fail" '
    "(it is wrong, incomplete, or unsafe)\n"
    '  "reason" — one sentence, at most 280 characters, saying why\n\n'
    "The task was: {request}\n\nThe work:\n{code}"
)


MAX_CODE_CHARS = 200_000        # past this we don't ask anyone to review it (see #7 below)

# The shared shape every reviewer's structured answer must match. codex's structured-
# output endpoint additionally refuses an object schema that doesn't say
# "additionalProperties": false explicitly (confirmed against a live call, 2026-08-27),
# so it gets its own copy rather than mutating this one.
VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"enum": ["pass", "fail"]},
        "reason": {"type": "string", "maxLength": 280},
    },
    "required": ["verdict", "reason"],
}
CODEX_VERDICT_SCHEMA: dict[str, Any] = {**VERDICT_SCHEMA, "additionalProperties": False}


class ReviewerBroke(RuntimeError):
    """The reviewer never rendered a judgement — the TOOL failed, not the work.

    This distinction is the whole reason the class exists. A CLI that exits non-zero
    (expired login, rate limit, renamed model), or one that exits 0 with no parseable
    structured answer, has not reviewed anything — so treating its output as an answer
    would record a FAIL against the build. Verdicts are permanent and bound to the
    version, so an infrastructure hiccup would condemn good work forever, and tell Neill
    a reviewer "found a problem" when nothing reviewed anything. A broken reviewer is a
    SKIP.
    """


def _prompt(request: str, code: str) -> str:
    return REVIEW_PROMPT.format(request=request, code=code)


def _run_cli_stdin(cmd: list[str], prompt: str) -> str:
    """Run a reviewer CLI with the prompt on stdin — never as a positional argument.

    Two reasons, both load-bearing: the lockdown flags below are variadic (`--tools
    <tools...>`, `--json-schema <schema>`) and a positional prompt placed after them
    would be swallowed as another value for the wrong flag rather than reaching the
    model. And argv is visible to anyone on the box who can list processes; stdin is not.
    """
    proc = subprocess.run(
        cmd, input=prompt, capture_output=True, text=True, timeout=REVIEW_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise ReviewerBroke(
            f"{cmd[0]} exited {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:200]}"
        )
    return proc.stdout


def _read_structured(structured: Any) -> tuple[str, str]:
    """The ONLY door a verdict can walk through, for all three providers.

    No stdout scan, no substring match, no trusting a free-text field — a provider that
    didn't hand back a dict shaped like VERDICT_SCHEMA has not answered, full stop. This
    is what makes a diff containing the literal line "PASS looks great" inert: that text
    has to land in a JSON field named "verdict" holding exactly "pass", or it changes
    nothing.
    """
    if not isinstance(structured, dict):
        raise ReviewerBroke("the reviewer did not answer in the structured form we required")
    verdict = structured.get("verdict")
    reason = structured.get("reason")
    if verdict not in ("pass", "fail"):
        raise ReviewerBroke("the reviewer's structured answer had no valid verdict field")
    if not isinstance(reason, str) or not reason.strip():
        raise ReviewerBroke("the reviewer's structured answer had no reason")
    return verdict, reason.strip()[:280]


def _last_json_object(text: str) -> Any:
    """Read backwards for the model's actual structured answer.

    codex prints the run's narration (banner, token count) around the answer it wrote to
    satisfy --output-schema; the same "read from the end" reasoning the old line-scan
    used still applies — the model's real answer comes last. Unlike the old scan, a line
    only counts here if it parses as JSON; free text (an echoed prompt, a narrated "PASS
    looks great") is never mistaken for one.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            return json.loads(stripped)
        except ValueError:
            continue
    raise ReviewerBroke("the reviewer did not answer in the structured form we required")


# --- Claude family --------------------------------------------------------------
def _claude_cmd(model: str) -> list[str]:
    """Every flag here was checked against `claude --help` on this machine, 2026-08-27 —
    none is assumed. See test_reviewer_lockdown.py for the flag-by-flag proof."""
    return [
        "claude", "-p",
        "--setting-sources", "",       # no user/project/local settings load at all
        "--strict-mcp-config",         # ...so no MCP server the caller happens to have
        "--restricted",                # ...no Bash/file-running tools, no reading outside
        "--safe-mode",                 #    the working dirs, no hooks/skills/plugins
        "--tools", "",                 # belt-and-braces on top of --restricted: no tools
        "--output-format", "json",
        "--json-schema", json.dumps(VERDICT_SCHEMA),
        "--model", model,
    ]


def _claude_review(request: str, code: str, model: str) -> tuple[str, str]:
    """The Claude-family reviewer — locked down against exactly what Sol's design gate
    flagged: a reviewer call running on attacker-controllable text (a builder-written
    diff) with the caller's own ambient tool access, MCP connections and directory
    grants. `--output-format json` puts the answer in a `structured_output` field
    (confirmed with a live call, 2026-08-27); that field is the only thing read below —
    never the free-text `result` string, which is a request-shaped answer echo, not a
    contract.
    """
    raw = _run_cli_stdin(_claude_cmd(model), _prompt(request, code))
    try:
        result = json.loads(raw)
    except ValueError as exc:
        raise ReviewerBroke("the reviewer's reply wasn't readable JSON") from exc
    if not isinstance(result, dict):
        raise ReviewerBroke("the reviewer's reply wasn't the JSON envelope we expected")
    if result.get("is_error"):
        raise ReviewerBroke(f"the reviewer errored: {str(result.get('result', ''))[:160]}")
    return _read_structured(result.get("structured_output"))


# --- Codex / GPT family ----------------------------------------------------------
def _codex_cmd(model: str, schema_path: Path) -> list[str]:
    """Every flag here was checked against `codex exec --help` on this machine,
    2026-08-27. codex has no `--json-schema <string>` like claude's; --output-schema
    only takes a file, so the schema is written to a short-lived temp file per call
    (see _codex_review) rather than kept as a long-lived shared path."""
    return [
        "codex", "exec",
        "--sandbox", "read-only",       # reads the bundle it was handed; writes nothing
        "--skip-git-repo-check",        # it is not operating on a checkout
        "--model", model,
        "--output-schema", str(schema_path),
        "-",                            # read the prompt from stdin, never argv
    ]


def _codex_review(request: str, code: str, model: str) -> tuple[str, str]:
    """The GPT-family reviewer, through the codex CLI."""
    with tempfile.TemporaryDirectory(prefix="chief-review-") as tmp:
        schema_path = Path(tmp) / "verdict_schema.json"
        schema_path.write_text(json.dumps(CODEX_VERDICT_SCHEMA))
        out = _run_cli_stdin(_codex_cmd(model, schema_path), _prompt(request, code))
    return _read_structured(_last_json_object(out))


# --- Grok / xai family -------------------------------------------------------------
XAI_CHAT_URL = "https://api.x.ai/v1/chat/completions"
XAI_KEY_VAR = "XAI_API_KEY"
MAX_REPLY_BYTES = 1_000_000     # a verdict is one small JSON object; anything bigger isn't


def _xai_review(request: str, code: str, model: str) -> tuple[str, str]:
    """The Grok-family reviewer, straight over HTTP.

    No CLI here on purpose: Grok's CLI has been alleged to upload whole checkouts, and
    the reviewer must only ever see the bundle it is handed. Plain urllib rather than an
    SDK so the seat adds no dependency — the API is OpenAI-shaped and one POST is all a
    review needs. `response_format: json_object` is xai's structured-output mode
    (confirmed against a live call, 2026-08-27); it guarantees valid JSON, not our exact
    keys, so the prompt still names them and _read_structured still checks them. The key
    comes from the environment the server loads (~/.chief/env), never from the repo. A
    missing key is the TOOL failing, so it is a skip, never a verdict — same rule as a
    CLI exiting non-zero.
    """
    key = os.environ.get(XAI_KEY_VAR, "").strip()
    if not key:
        raise ReviewerBroke("the paid reviewer has no sign-in key on this machine")
    body = json.dumps({
        "model": model,
        "temperature": 0,
        "response_format": {"type": "json_object"},
        "messages": [{"role": "user", "content": _prompt(request, code)}],
    }).encode()
    req = urllib.request.Request(
        XAI_CHAT_URL, data=body, method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=REVIEW_TIMEOUT_S) as resp:
            payload = json.loads(resp.read(MAX_REPLY_BYTES).decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        # Status code only. The body is vendor text and could echo anything — it never
        # reaches the record, which is what Neill reads.
        why = {401: "its sign-in key was refused", 403: "it isn't allowed to do that",
               429: "it is being rate-limited right now"}.get(exc.code,
               f"it answered with error {exc.code}")
        raise ReviewerBroke(f"the paid reviewer couldn't run — {why}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReviewerBroke("the paid reviewer couldn't be reached") from exc
    except ValueError as exc:                       # not JSON
        raise ReviewerBroke("the paid reviewer sent back something unreadable") from exc
    try:
        choice = payload["choices"][0]
        text = choice["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:
        raise ReviewerBroke("the paid reviewer sent back no answer") from exc
    # An empty answer, or one cut off before the closing brace, is the TOOL failing.
    if not text.strip():
        raise ReviewerBroke("the paid reviewer answered with nothing")
    if choice.get("finish_reason") not in (None, "stop"):
        raise ReviewerBroke("the paid reviewer was cut off before it finished")
    try:
        structured = json.loads(text)
    except ValueError as exc:
        raise ReviewerBroke("the paid reviewer's reply wasn't readable JSON") from exc
    return _read_structured(structured)


REVIEWERS: dict[str, Callable[[str, str, str], tuple[str, str]]] = {
    "claude-cli": _claude_review,
    "codex": _codex_review,
    "xai": _xai_review,
}


# ---------------------------------------------------------------------------
# Can the runner actually run HERE, now? A runner that exists but is logged out, or has
# no key, would be counted onto every new job's panel and then skip — parking the job
# forever with "3 required, 2 reported". Better one legible exclusion at the door.
# ---------------------------------------------------------------------------
def _claude_ready() -> bool:
    """`claude auth status` prints JSON with a `loggedIn` field and exits 0 whether or
    not it's logged in (confirmed with a live call, 2026-08-27) — so the exit code alone
    is not enough; the field has to say so. `~/.claude` missing entirely is checked first
    as a cheap short-circuit before spending a subprocess call."""
    if not (Path.home() / ".claude").exists():
        return False
    try:
        proc = subprocess.run(["claude", "auth", "status"], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    if proc.returncode != 0:
        return False
    try:
        return bool(json.loads(proc.stdout).get("loggedIn"))
    except ValueError:
        return False


def _codex_ready() -> bool:
    """`codex login status` exits non-zero when logged out (the nearest equivalent to
    claude's status field — codex has no machine-readable flag on this command)."""
    try:
        proc = subprocess.run(["codex", "login", "status"], capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


_READY: dict[str, Callable[[], bool]] = {
    "xai": lambda: bool(os.environ.get(XAI_KEY_VAR, "").strip()),
    "claude-cli": _claude_ready,
    "codex": _codex_ready,
}


def has_runner(provider: str) -> bool:
    return provider in REVIEWERS and _READY.get(provider, lambda: True)()


# ---------------------------------------------------------------------------
# The startup self-check. Every other guarantee in this module assumes the reviewer
# CLIs are actually running locked down — this is the one place that verifies it,
# rather than trusting that nobody ever edits _claude_cmd/_codex_cmd without noticing
# what they removed.
# ---------------------------------------------------------------------------
def _flag_value(argv: list[str], flag: str) -> str | None:
    try:
        i = argv.index(flag)
    except ValueError:
        return None
    return argv[i + 1] if i + 1 < len(argv) else None


def assert_reviewers_locked_down() -> None:
    """Inspect the argv the runners would actually build and refuse to run the panel if
    a lockdown flag is missing.

    This builds argv only — it never spawns a process — so it is cheap enough to call on
    every panel run. Called from run_panel(), which means a bad deploy (a refactor that
    drops --restricted, a codex upgrade that renames --sandbox) fails the FIRST review
    job loudly, instead of quietly running every review after it wide open.
    """
    claude_argv = _claude_cmd("probe-model")
    codex_argv = _codex_cmd("probe-model", Path("/tmp/chief-review-probe/verdict_schema.json"))

    failures: list[str] = []
    if _flag_value(claude_argv, "--setting-sources") != "":
        failures.append("claude: --setting-sources must be present and empty")
    if "--strict-mcp-config" not in claude_argv:
        failures.append("claude: --strict-mcp-config is missing")
    if "--restricted" not in claude_argv:
        failures.append("claude: --restricted is missing")
    if "--safe-mode" not in claude_argv:
        failures.append("claude: --safe-mode is missing")
    if _flag_value(claude_argv, "--tools") != "":
        failures.append("claude: --tools must be present and empty (no tool access)")
    if not _flag_value(claude_argv, "--json-schema"):
        failures.append("claude: --json-schema is missing")
    if _flag_value(claude_argv, "--output-format") != "json":
        failures.append("claude: --output-format json is missing")

    if _flag_value(codex_argv, "--sandbox") != "read-only":
        failures.append("codex: --sandbox read-only is missing")
    if "--skip-git-repo-check" not in codex_argv:
        failures.append("codex: --skip-git-repo-check is missing")
    if not _flag_value(codex_argv, "--output-schema"):
        failures.append("codex: --output-schema is missing")

    if failures:
        raise RuntimeError(
            "reviewer lockdown check failed — refusing to run the panel: "
            + "; ".join(failures)
        )


# ---------------------------------------------------------------------------
# What came back
# ---------------------------------------------------------------------------
@dataclass
class ReviewerRun:
    """One seat's participation — including the ones that did NOT participate.

    A skipped reviewer is part of the result, never an omission. "No silent caps": a
    panel that quietly shrank reads as a full panel to anyone looking at the verdicts.
    """
    seat_id: str
    family: str = ""
    verdict: str | None = None      # pass | fail | needs_human | None if it never ran
    summary: str = ""
    skipped: str = ""               # why it didn't run, in plain words

    @property
    def ran(self) -> bool:
        return self.verdict is not None


@dataclass
class PanelResult:
    job_id: int
    version: str
    required_families: int
    runs: list[ReviewerRun] = field(default_factory=list)
    certified: bool = False         # did the job actually reach 'done'
    parked_reason: str = ""         # if not, why — in plain words

    builder_family: str = ""        # the author is not a second opinion (migration 007)

    @property
    def families_passed(self) -> set[str]:
        """Counted the way the database counts: minds OTHER than the author's."""
        return {r.family for r in self.runs
                if r.verdict == "pass" and r.family != self.builder_family}

    @property
    def failed(self) -> list[ReviewerRun]:
        return [r for r in self.runs if r.verdict == "fail"]

    def as_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "version": self.version,
            "required_families": self.required_families,
            "families_passed": sorted(self.families_passed),
            "certified": self.certified,
            "parked_reason": self.parked_reason,
            "runs": [
                {"seat": r.seat_id, "family": r.family, "verdict": r.verdict,
                 "summary": r.summary, "skipped": r.skipped}
                for r in self.runs
            ],
        }

    def spoken(self) -> str:
        """One sentence for the voice. No seat names, no jargon."""
        n = len([r for r in self.runs if r.ran])
        minds = len(self.families_passed)
        if self.certified:
            return f"{n} different models checked it and it passed. Ready for you."
        if self.failed:
            return "It was checked and sent back — one of the reviewers found a problem."
        return f"Only {minds} of the {self.required_families} required models could check it, so it's waiting."


# ---------------------------------------------------------------------------
# The panel
# ---------------------------------------------------------------------------
def _event(conn, job_id: int, seat_row, kind: str, detail: str) -> None:
    conn.execute(
        "INSERT INTO events (job_id, seat_id, lane, model, family, kind, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (job_id, seat_row["id"], seat_row["id"], seat_row["model"],
         seat_row["family"], kind, detail),
    )


def _reserve_review_budget(conn, seat_id: str, job_id: int, cents: int) -> bool:
    """Take the money BEFORE the call, not after.

    Sol: "refusing to RECORD a charge doesn't unspend it." Two reviewers reserving at
    once both hit the ledger, and the ledger's own cap trigger refuses the one that
    would breach — so the race resolves in the database, not in a Python check that
    both threads passed a moment earlier. Uncapped seats reserve nothing.
    """
    row = seat(conn, seat_id)
    if row is None:
        return True
    # ALWAYS go through the ledger, even for a seat with no daily cap. Skipping the write
    # when daily_cap_cents was NULL meant the MONTHLY budget and the per-role review
    # ration never saw those calls at all — and today's two working reviewers are both
    # uncapped, so that was every review in production. A flat-rate seat records 0 and
    # still shows up in the account of what ran.
    if row["tier"] != "metered":
        cents = 0
    # ASK, don't take. Spending is the gatekeeper's power (task #11) — the panel is a
    # caller like any other, and gets no special path to the money because it happens
    # to live in the same process.
    import gatekeeper
    try:
        gatekeeper.spend(conn, seat_id, cents, job_id=job_id, role="review",
                         asked_by="the review panel")
        return True
    except gatekeeper.Refused:
        return False


def _review_one(
    job_id: int,
    seat_id: str,
    request: str,
    code: str,
    version: str,
    cfg: dict[str, Any],
    out: ReviewerRun,
    db_path: Path,
    decided: threading.Event,
) -> None:
    """One reviewer, in its own thread, on its own connection. Never raises.

    The outer try/except is not belt-and-braces. ANY escape from this function kills the
    thread with the run left blank — neither a verdict nor a stated reason — and a
    reviewer that is neither counted nor excused is precisely the silent shrink this
    panel exists to prevent. So every exit lands in `out`.
    """
    conn = None
    try:
        conn = connect(db_path)
        row = seat(conn, seat_id)
        if row is None:
            out.skipped = "that reviewer isn't set up"
            return
        out.family = row["family"]
        if not row["enabled"]:
            out.skipped = "that reviewer is turned off"
            _event(conn, job_id, row, "skipped", out.skipped)
            return
        runner = REVIEWERS.get(row["provider"])
        if runner is None:
            out.skipped = "we can't run that reviewer on this machine yet"
            _event(conn, job_id, row, "skipped", out.skipped)
            return

        # Already reviewed this exact version? Then this is a re-run, and repeating it
        # would spend the money twice and stack duplicate verdicts on the record. This
        # check runs BEFORE the budget check below, on purpose: a retry must not throw
        # away a reviewer's earlier PASS just because that seat is capped out today —
        # the verdict was already earned and paid for.
        already = conn.execute(
            "SELECT verdict, summary FROM verdicts WHERE job_id=? AND reviewer_seat=? "
            "AND reviewed_version IS ? ORDER BY id DESC LIMIT 1",
            (job_id, seat_id, version),
        ).fetchone()
        if already is not None:
            out.verdict, out.summary = already["verdict"], already["summary"] or ""
            return

        # Preflight size check, before any money moves or any provider is called. A
        # reviewer that silently judged part of the work would be worse than one that
        # didn't run — and truncating-then-asking would eventually FAIL the version
        # forever on a build that simply grew past what one review call can hold. Skip
        # plainly instead; skips can be retried, a truncated FAIL cannot be un-said.
        if len(code) > MAX_CODE_CHARS:
            out.skipped = "the work is too large to review in one go"
            _event(conn, job_id, row, "skipped", out.skipped)
            return

        if over_budget(conn, seat_id):
            out.skipped = "that reviewer is out of budget for today"
            _event(conn, job_id, row, "skipped", out.skipped)
            return

        estimate = int(
            cfg.get("seats", {}).get(seat_id, {}).get(
                "review_estimate_cents", DEFAULT_ESTIMATE_CENTS)
        )
        if not _reserve_review_budget(conn, seat_id, job_id, estimate):
            out.skipped = "that reviewer is out of budget for today"
            _event(conn, job_id, row, "skipped", out.skipped)
            return

        _event(conn, job_id, row, "thinking", "Checking the work.")
        try:
            verdict, summary = runner(request, code, row["model"])
        except subprocess.TimeoutExpired:
            out.skipped = "that reviewer took too long and was stopped"
            _event(conn, job_id, row, "error", out.skipped)
            return
        except ReviewerBroke as exc:
            # The TOOL failed, not the work. Never a verdict — see ReviewerBroke.
            out.skipped = "that reviewer couldn't run"
            _event(conn, job_id, row, "error", f"{out.skipped}: {str(exc)[:160]}")
            return
        except Exception as exc:                      # noqa: BLE001 — a dead reviewer
            out.skipped = "that reviewer couldn't finish"   # is a skip, never a pass
            _event(conn, job_id, row, "error", f"{out.skipped}: {str(exc)[:120]}")
            return

        # The panel has already decided and moved on without us. Recording now would
        # land a verdict on a job that was called finished a moment ago — and if it were
        # a FAIL, the completion guards could no longer act on it, because they only fire
        # on the way INTO done. A late answer is a non-answer.
        if decided.is_set():
            out.skipped = "that reviewer answered after the panel had already decided"
            _event(conn, job_id, row, "skipped", out.skipped)
            return

        # reviewed_version is passed EXPLICITLY, never defaulted. The default reads the
        # job's current head — which is only correct if nothing moved, and the whole
        # point of the frozen bundle is not to depend on that being true.
        try:
            record_verdict(conn, job_id, seat_id, verdict=verdict,
                           summary=summary, role="reviewer", reviewed_version=version)
        except GuardViolation as exc:
            out.skipped = "the record refused that verdict"
            _event(conn, job_id, row, "error", str(exc)[:160])
            return

        # The `decided` check above and this write are not one step: the panel can
        # decide and certify in between. A FAIL that lands after 'done' must un-certify
        # from THIS side too — the writer is the only one who knows it just happened.
        # `decided` is THIS panel's flag. Another panel on the same job (a re-run, a
        # second dispatch) may have certified it already — so the check is on the
        # record, not on our own bookkeeping.
        if verdict == "fail":
            status = conn.execute("SELECT status FROM jobs WHERE id = ?",
                                  (job_id,)).fetchone()["status"]
            if status == "done":
                set_status(conn, job_id, "review",
                           spoken_summary="Sent back — a reviewer found a problem.")
            elif status == "shipped":
                # Too late to un-merge. The one thing we can still do is refuse to be
                # quiet about it: a merged objection is Neill's to see, not a footnote.
                set_status(conn, job_id, "shipped",
                           spoken_summary="⚠ A reviewer objected AFTER this was merged — "
                                          "it needs your eyes.")
                _event(conn, job_id, row, "error",
                       "objection recorded after merge — needs a person")

        out.summary = summary
        out.verdict = verdict          # set LAST: `verdict` is what marks the run as real
        _event(conn, job_id, row, "verdict",
               "Passed the check." if verdict == "pass" else "Sent it back with notes.")
    except Exception as exc:                          # noqa: BLE001
        out.skipped = out.skipped or f"that reviewer couldn't finish: {str(exc)[:120]}"
    finally:
        if conn is not None:
            conn.close()


def run_panel(
    conn,
    job_id: int,
    request: str,
    code: str,
    version: str,
    cfg: dict[str, Any],
    *,
    roster: list[str] | None = None,
    db_path: Path | None = None,
) -> PanelResult:
    """Run the whole panel in parallel against one frozen bundle, then decide.

    `conn` is the caller's connection, used only for the final decision. Each reviewer
    gets its own. Returns what actually happened — including who didn't run and why.
    """
    # Fail loud, first, before anyone is dispatched: a lockdown flag missing from a
    # runner's argv is a hole in the exact place task #10's design gate found one.
    assert_reviewers_locked_down()

    import dispatch                      # local: dispatch imports this module

    gauntlet = cfg.get("gauntlet", {})
    # Ask the SAME question dispatch asked when it stamped the panel size on the job:
    # who can actually sit? Falling back to the raw config list would put the panel and
    # the job's own requirements at odds — the job needing 2 seats while the panel tried
    # 3, one of which cannot run.
    roster = roster if roster is not None else dispatch.panel_roster(conn, cfg)[0]
    floor = int(gauntlet.get("min_model_families", 0))
    db_path = db_path or DB_PATH

    built_by = conn.execute("SELECT builder_family FROM jobs WHERE id = ?",
                            (job_id,)).fetchone()
    result = PanelResult(job_id=job_id, version=version, required_families=floor,
                         builder_family=(built_by["builder_family"] if built_by else "") or "")

    # A floor of zero is not "no requirement" — it is an UNCONFIGURED panel, and
    # `0 < 0` is False, so every later check would wave it through and certify work
    # nobody looked at. dispatch refuses this at the door; refuse it here too, because
    # this function is reachable from anywhere and must not depend on its caller having
    # been careful. Same for an empty roster: nobody to ask is not everybody agreeing.
    if floor < 1 or not roster:
        result.parked_reason = (
            "no review panel is configured for this job — nothing would have checked it"
        )
        return result

    result.runs = [ReviewerRun(seat_id=s) for s in roster]
    decided = threading.Event()

    threads = [
        threading.Thread(
            target=_review_one,
            args=(job_id, run.seat_id, request, code, version, cfg, run, db_path,
                  decided),
            name=f"review-{job_id}-{run.seat_id}", daemon=True,
        )
        for run in result.runs
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=REVIEW_TIMEOUT_S + JOIN_GRACE_S)

    # Close the door BEFORE reading any results. A reviewer still running has not
    # answered, and must not be able to answer into a decision already made — the
    # completion guards only fire on the way INTO done, so a late objection could never
    # stop anything. It is recorded as not having participated, which is the truth.
    decided.set()
    for t, run in zip(threads, result.runs):
        if t.is_alive() and not run.ran:
            run.skipped = run.skipped or "that reviewer never answered"

    # ── The decision. Every branch below ends in "leave it parked" except one. ──
    if result.failed:
        result.parked_reason = "a reviewer failed this version"
        return result

    families = result.families_passed
    if len(families) < floor:
        ran = len([r for r in result.runs if r.ran])
        result.parked_reason = (
            f"only {len(families)} model famil{'y' if len(families) == 1 else 'ies'} "
            f"reviewed this ({ran} of {len(result.runs)} reviewers ran); "
            f"{floor} are required"
        )
        return result

    try:
        set_status(conn, job_id, "done",
                   spoken_summary=f"Checked by {len(families)} different models. Ready for you.")
        result.certified = True
    except GuardViolation as exc:
        # Another gate still holds (panel size, an unanswered escalation, an old
        # objection). Park honestly rather than argue with the boundary.
        result.parked_reason = str(exc).replace("guard: ", "")

    if result.certified:
        _reconcile(conn, result)
    if not result.certified:
        # Say why it stopped, in the record, in plain words. A job parked with no stated
        # reason is indistinguishable from a job nobody looked at.
        conn.execute("UPDATE jobs SET spoken_summary = ? WHERE id = ?",
                     (result.spoken(), job_id))
    return result


def _reconcile(conn, result: PanelResult) -> None:
    """Last look: did an objection land while we were deciding?

    The `decided` flag narrows that window; it cannot close it, because a straggler can
    pass the check and be writing as the main thread commits. So after certifying we ask
    the record — not our own bookkeeping — whether a failure exists against this version,
    and un-certify if one does. Status can move back out of 'done'; the guards only
    police the way in. A job wrongly parked costs a re-run. A job wrongly called finished
    is the thing this project exists to prevent.
    """
    late = conn.execute(
        "SELECT reviewer_seat FROM verdicts WHERE job_id=? AND verdict='fail' "
        "AND reviewed_version IS ?", (result.job_id, result.version),
    ).fetchone()
    if late is None:
        return
    set_status(conn, result.job_id, "review",
               spoken_summary="Sent back — a reviewer found a problem.")
    result.certified = False
    result.parked_reason = "a reviewer failed this version (it answered late)"


def run_gauntlet_for_job(conn, job_id: int, cfg: dict[str, Any], *,
                         db_path: Path | None = None) -> PanelResult:
    """Run the panel for a job that is already parked at review.

    Reads the frozen bundle off the job row ONCE — the request, the output that was put
    forward, and the version it was recorded under — and hands that same snapshot to
    everyone.
    """
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if job is None:
        raise ValueError(f"no such job: {job_id}")
    if not job["head_version"]:
        # Nothing was put forward, so there is nothing to bind a verdict to. A verdict
        # with no version condemns or approves we-don't-know-what.
        return PanelResult(job_id=job_id, version="", required_families=0,
                           parked_reason="no finished version to review")
    return run_panel(conn, job_id, job["request"], job["result"] or "",
                     job["head_version"], cfg, db_path=db_path)
