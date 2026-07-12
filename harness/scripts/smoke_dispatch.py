"""Phase 1 smoke test — the real loop, end to end.

Proves the §8 Phase-1 acceptance flow with live models:
    "have the local model write X, then have <a higher-tier seat> review it"
    ...and it happens, with the job recorded.

The Codex seat needs Neill's OAuth, so this uses the Claude CLI as the reviewing
seat — same tier, same code path, same guard. Swapping in Codex is a config line.

Run:  .venv/bin/python harness/scripts/smoke_dispatch.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import (  # noqa: E402
    GuardViolation,
    Seat,
    claim_next_job,
    connect,
    create_job,
    init_db,
    overnight_report,
    record_verdict,
    set_status,
    upsert_seat,
)

OLLAMA = "http://127.0.0.1:11434/api/generate"
TASK = "Write a Python function `is_palindrome(s: str) -> bool` that ignores case and non-alphanumerics. Return ONLY the code."


def grinder_writes(prompt: str) -> str:
    """The local seat actually does the work."""
    payload = json.dumps({
        "model": "qwen2.5-coder:7b",
        "prompt": prompt,
        "stream": False,
    }).encode()
    req = urllib.request.Request(OLLAMA, data=payload, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        return json.loads(r.read())["response"].strip()


def reviewer_reviews(code: str) -> tuple[str, str]:
    """A higher-tier seat reviews it. Returns (verdict, summary)."""
    prompt = (
        "Review this code. Reply with exactly one line: PASS <reason> or FAIL <reason>.\n\n"
        f"Task was: {TASK}\n\nCode:\n{code}"
    )
    out = subprocess.run(
        ["claude", "-p", prompt],
        capture_output=True, text=True, timeout=180,
    ).stdout.strip()
    verdict = "pass" if out.upper().lstrip().startswith("PASS") else "fail"
    return verdict, out[:300]


def main() -> int:
    conn = connect()
    init_db(conn)

    # The seats from config/seats.toml. Providers swap behind these names.
    upsert_seat(conn, Seat("grinder", "ollama", "qwen2.5-coder:7b", "local"))
    upsert_seat(conn, Seat("reviewer", "claude-cli", "claude-fable-5", "metered", daily_cap_cents=500))

    print("1. queueing a job for the local grinder…")
    job = create_job(conn, TASK, builder_seat="grinder", origin="text")

    claimed = claim_next_job(conn, "grinder")
    assert claimed and claimed["id"] == job
    print(f"   claimed job #{job} (status={claimed['status']})")

    print("2. grinder (qwen2.5-coder:7b, LOCAL) writing…")
    code = grinder_writes(TASK)
    set_status(conn, job, "review", result=code)
    print(f"   got {len(code)} chars back")

    print("3. trying to ship it WITHOUT review — the guard should stop this…")
    try:
        set_status(conn, job, "done")
        print("   ✗ FAILED: unreviewed local output shipped. The guard is broken.")
        return 1
    except GuardViolation as e:
        print(f"   ✓ blocked: {e}")

    print("4. reviewer (claude-fable-5, METERED) reviewing…")
    verdict, summary = reviewer_reviews(code)
    record_verdict(conn, job, "reviewer", verdict=verdict, model_family="claude", summary=summary)
    print(f"   verdict: {verdict.upper()} — {summary.splitlines()[0][:80]}")

    if verdict != "pass":
        print("   reviewer rejected it; job stays in review. (Working as designed.)")
        return 0

    print("5. now shipping it…")
    set_status(conn, job, "done", spoken_summary="Local model wrote is_palindrome. Reviewed and passed.")
    print("   ✓ done")

    print("\n6. the §7 report — 'what did the overnight run do?'")
    for r in overnight_report(conn):
        print(f"   #{r['id']} [{r['status']}] built by {r['builder_seat']} ({r['builder_tier']}) "
              f"· {r['review_count']} review(s) · {r['families_reviewed']} model famil(y/ies)")
        print(f"        \"{r['request'][:60]}…\"")
        print(f"        spoken: {r['spoken_summary']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
