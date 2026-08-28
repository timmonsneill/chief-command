"""The subscription seats' usage, read from what the tools leave on disk.

Fixtures are shaped like the real files (checked against live ones on 2026-08-27).
The reader must be read-only and must never crash on a half-written line.
"""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import usage_local  # noqa: E402

NOW = datetime(2026, 8, 27, 20, 0, tzinfo=timezone.utc)


def _codex_session(path: Path, totals, limits=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "session_meta", "payload": {"id": "x"}})]
    # cumulative: the LAST token_count is the session total
    for i, t in enumerate(totals):
        ev = {"type": "event_msg", "payload": {"type": "token_count",
              "info": {"total_token_usage": t}}}
        if limits and i == len(totals) - 1:
            ev["payload"]["rate_limits"] = limits
        lines.append(json.dumps(ev))
    path.write_text("\n".join(lines) + "\n")


def _claude_session(path: Path, messages):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for ts, model, usage in messages:
        lines.append(json.dumps({"type": "assistant", "timestamp": ts,
                                 "message": {"model": model, "usage": usage}}))
        lines.append(json.dumps({"type": "user", "timestamp": ts, "message": {"content": "hi"}}))
    lines.append('{"type": "assistant", "timestamp": "2026-08-27T19:59:00Z", "message": {"usage": {"output_to')  # torn
    path.write_text("\n".join(lines) + "\n")


def test_codex_totals_use_the_last_cumulative_count_and_report_the_allowance(tmp_path):
    home = tmp_path
    _codex_session(home / ".codex/sessions/2026/08/27/rollout-a.jsonl",
                   [{"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 10},
                    {"input_tokens": 1000, "cached_input_tokens": 900, "output_tokens": 40}],
                   limits={"primary": {"used_percent": 5.0, "window_minutes": 10080,
                                       "resets_at": int((NOW + timedelta(days=2)).timestamp())},
                           "plan_type": "pro"})
    _codex_session(home / ".codex/sessions/2026/08/26/rollout-b.jsonl",
                   [{"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 5}])
    u = usage_local.local_usage(days=7, home=home, now=NOW)["gpt"]
    assert u.sessions == 2
    assert u.output_tokens == 45 and u.input_tokens == 1010       # last count per file
    assert u.allowance_used_percent == 5.0 and u.plan == "pro"
    assert u.allowance_window_minutes == 10080


def test_claude_totals_sum_per_message_and_survive_a_torn_line(tmp_path):
    home = tmp_path
    _claude_session(home / ".claude/projects/-Users-x-proj/s1.jsonl", [
        ("2026-08-27T18:00:00Z", "claude-fable-5",
         {"input_tokens": 50, "cache_read_input_tokens": 9000, "output_tokens": 500}),
        ("2026-08-27T18:05:00Z", "claude-fable-5",
         {"input_tokens": 60, "cache_read_input_tokens": 9500, "output_tokens": 700}),
        ("2026-08-01T18:05:00Z", "claude-sonnet-5",                    # too old
         {"input_tokens": 999, "cache_read_input_tokens": 0, "output_tokens": 999}),
    ])
    u = usage_local.local_usage(days=7, home=home, now=NOW)["claude"]
    assert u.sessions == 1
    assert u.output_tokens == 1200 and u.input_tokens == 110
    assert u.cached_input_tokens == 18500
    assert u.models == {"claude-fable-5": 1200}
    assert u.allowance_used_percent is None      # Claude Code doesn't write its limits


def test_nothing_on_disk_is_not_an_error(tmp_path):
    u = usage_local.local_usage(days=7, home=tmp_path, now=NOW)
    assert u["gpt"].sessions == 0 and u["claude"].sessions == 0
    text = usage_local.in_plain_english(u, 7, now=NOW)
    assert "nothing used" in text


def test_the_summary_is_plain_english(tmp_path):
    home = tmp_path
    _codex_session(home / ".codex/sessions/2026/08/27/rollout-a.jsonl",
                   [{"input_tokens": 2_000_000, "cached_input_tokens": 1_800_000, "output_tokens": 9_000}],
                   limits={"primary": {"used_percent": 5.0, "window_minutes": 10080,
                                       "resets_at": int((NOW + timedelta(hours=30)).timestamp())},
                           "plan_type": "pro"})
    text = usage_local.in_plain_english(usage_local.local_usage(7, home=home, now=NOW), 7, now=NOW)
    assert "Codex: 1 sessions" in text and "5% of this week's allowance" in text
    assert "re-read from its own notes" in text
    assert "resets in about 30 hours" in text
    for jargon in ("jsonl", "token_count", "rollout", ".codex", "rate_limit"):
        assert jargon not in text


def test_the_reader_never_writes(tmp_path):
    home = tmp_path
    p = home / ".codex/sessions/2026/08/27/rollout-a.jsonl"
    _codex_session(p, [{"input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}])
    before = p.read_bytes()
    usage_local.local_usage(7, home=home, now=NOW)
    assert p.read_bytes() == before
    assert sorted(x.name for x in (home / ".codex/sessions/2026/08/27").iterdir()) == ["rollout-a.jsonl"]
