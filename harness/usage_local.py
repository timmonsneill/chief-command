"""What have we used? — for the subscription seats, which never write to the ledger.

The binding constraint on Claude and Codex is RATE LIMITS, not money (seats.toml: "you
cannot buy your way out of a weekly cap"). Neither seat costs cents per call, so neither
writes `usage` rows — and until now nothing could answer "how much of this week's
allowance is gone?". Both tools leave the answer on disk:

  Codex    ~/.codex/sessions/<yyyy>/<mm>/<dd>/rollout-*.jsonl
           `token_count` events carry a CUMULATIVE `total_token_usage` for the session
           (so the LAST one per file is the session's total) and a `rate_limits` block:
           used_percent, window_minutes, resets_at — the vendor's own number.
  Claude   ~/.claude/projects/<project>/*.jsonl
           one line per message; assistant lines carry `message.usage` with
           input/output/cache tokens and `message.model`.

READ-ONLY. This module never writes to those directories. Paths come from the home
directory, never a machine name (rule 4). Everything printed is plain English (Neill
reads it) — the numbers are there, the jargon isn't.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


@dataclass
class FamilyUsage:
    family: str
    sessions: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    # Vendor-reported allowance, when the tool records one (Codex does; Claude Code
    # does not write its limits to disk).
    allowance_used_percent: float | None = None
    allowance_window_minutes: int | None = None
    allowance_resets_at: datetime | None = None
    plan: str | None = None
    models: dict[str, int] = field(default_factory=dict)   # model -> output tokens


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    """Yield parsed lines; a corrupt line is skipped, never fatal — these files are
    written live by another program and the last line may be half-written."""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except OSError:
        return


def _codex_usage(root: Path, since: datetime) -> FamilyUsage:
    u = FamilyUsage(family="gpt")
    sessions_dir = root / "sessions"
    if not sessions_dir.is_dir():
        return u
    latest_limits: tuple[datetime, dict[str, Any]] | None = None
    for f in sorted(sessions_dir.glob("*/*/*/rollout-*.jsonl")):
        try:
            mtime = datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < since:
            continue
        last_total: dict[str, Any] | None = None
        last_limits: dict[str, Any] | None = None
        for obj in _iter_jsonl(f):
            payload = obj.get("payload") if isinstance(obj.get("payload"), dict) else obj
            if payload.get("type") != "token_count":
                continue
            info = payload.get("info") or {}
            if isinstance(info.get("total_token_usage"), dict):
                last_total = info["total_token_usage"]
            if isinstance(payload.get("rate_limits"), dict):
                last_limits = payload["rate_limits"]
        if last_total is None:
            continue
        u.sessions += 1
        u.input_tokens += int(last_total.get("input_tokens") or 0)
        u.cached_input_tokens += int(last_total.get("cached_input_tokens") or 0)
        u.output_tokens += int(last_total.get("output_tokens") or 0)
        if last_limits and (latest_limits is None or mtime > latest_limits[0]):
            latest_limits = (mtime, last_limits)
    if latest_limits:
        lim = latest_limits[1]
        primary = lim.get("primary") or {}
        u.allowance_used_percent = _num(primary.get("used_percent"))
        u.allowance_window_minutes = int(primary["window_minutes"]) \
            if primary.get("window_minutes") is not None else None
        if primary.get("resets_at"):
            u.allowance_resets_at = datetime.fromtimestamp(int(primary["resets_at"]), tz=timezone.utc)
        u.plan = lim.get("plan_type")
    return u


def _claude_usage(root: Path, since: datetime) -> FamilyUsage:
    u = FamilyUsage(family="claude")
    projects = root / "projects"
    if not projects.is_dir():
        return u
    for f in sorted(projects.glob("*/*.jsonl")):
        try:
            if datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc) < since:
                continue
        except OSError:
            continue
        counted = False
        for obj in _iter_jsonl(f):
            msg = obj.get("message")
            if not isinstance(msg, dict) or not isinstance(msg.get("usage"), dict):
                continue
            ts = _when(obj.get("timestamp"))
            if ts is not None and ts < since:
                continue
            usage = msg["usage"]
            u.input_tokens += int(usage.get("input_tokens") or 0)
            u.cached_input_tokens += int(usage.get("cache_read_input_tokens") or 0)
            out = int(usage.get("output_tokens") or 0)
            u.output_tokens += out
            model = str(msg.get("model") or "unknown")
            u.models[model] = u.models.get(model, 0) + out
            counted = True
        if counted:
            u.sessions += 1
    return u


def _when(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def local_usage(days: int = 7, home: Path | None = None,
                now: datetime | None = None) -> dict[str, FamilyUsage]:
    """Per family, what the subscription tools have used in the last `days` days."""
    home = Path(home) if home else Path.home()
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    return {
        "gpt": _codex_usage(home / ".codex", since),
        "claude": _claude_usage(home / ".claude", since),
    }


# ---------------------------------------------------------------------------
# In words
# ---------------------------------------------------------------------------
_NAMES = {"gpt": "Codex", "claude": "Claude"}


def _k(n: int) -> str:
    """Tokens are not words; ~0.75 words per token is close enough for a spoken number."""
    return f"{n/1_000_000:.1f} million" if n >= 1_000_000 else f"{n/1000:.0f} thousand" if n >= 1000 else str(n)


def in_plain_english(usage: dict[str, FamilyUsage], days: int = 7,
                     now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    lines = []
    for fam in ("gpt", "claude"):
        u = usage.get(fam)
        name = _NAMES.get(fam, fam)
        if u is None or u.sessions == 0:
            lines.append(f"{name}: nothing used in the last {days} days.")
            continue
        fresh = u.input_tokens
        line = (f"{name}: {u.sessions} sessions in the last {days} days — it wrote about "
                f"{_k(int(u.output_tokens * 0.75))} words and read about "
                f"{_k(int(fresh * 0.75))} new words"
                + (f" (plus {_k(int(u.cached_input_tokens * 0.75))} re-read from its own notes, "
                   "which is cheap)" if u.cached_input_tokens else "") + ".")
        if u.allowance_used_percent is not None:
            window = "week" if (u.allowance_window_minutes or 0) >= 7 * 24 * 60 else "allowance period"
            when = ""
            if u.allowance_resets_at:
                left = u.allowance_resets_at - now
                when = f", resets in about {max(0, int(left.total_seconds() // 3600))} hours"
            line += f" About {u.allowance_used_percent:.0f}% of this {window}'s allowance is used{when}."
        else:
            line += " (This tool doesn't record its allowance on disk.)"
        lines.append(line)
    return "\n".join(lines)


if __name__ == "__main__":
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 7
    print(in_plain_english(local_usage(days), days))
