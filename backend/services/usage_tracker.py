"""Session and turn persistence with cost tracking."""

import logging
from datetime import datetime, timedelta, timezone

from db import get_db

logger = logging.getLogger(__name__)

PRICING_PER_MTOK = {
    "claude-haiku-4-5":  {"in": 1.0,  "out": 5.0,  "cached_in": 0.1},
    "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0, "cached_in": 0.3},
    "claude-opus-4-7":   {"in": 5.0,  "out": 25.0, "cached_in": 0.5},
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_cost_cents(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int) -> int:
    rates = PRICING_PER_MTOK.get(model, PRICING_PER_MTOK["claude-haiku-4-5"])
    billable_input = max(0, input_tokens - cache_read_tokens)
    cost_dollars = (
        (billable_input / 1_000_000) * rates["in"]
        + (output_tokens / 1_000_000) * rates["out"]
        + (cache_read_tokens / 1_000_000) * rates["cached_in"]
        + (cache_creation_tokens / 1_000_000) * rates["in"]
    )
    return round(cost_dollars * 100)


async def create_session(session_id: str, user_id: str = "owner") -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (id, user_id, started_at, total_cost_cents, turn_count) VALUES (?, ?, ?, 0, 0)",
            (session_id, user_id, _now_iso()),
        )
        await db.commit()
    logger.info("Session created: %s", session_id)


async def close_session(session_id: str) -> None:
    async with get_db() as db:
        await db.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (_now_iso(), session_id),
        )
        await db.commit()
    logger.info("Session closed: %s", session_id)


async def record_turn(
    session_id: str,
    model: str,
    usage_dict: dict,
    user_text: str,
    assistant_text: str,
) -> dict:
    input_tokens = usage_dict.get("input_tokens", 0)
    output_tokens = usage_dict.get("output_tokens", 0)
    cache_read_tokens = usage_dict.get("cache_read_input_tokens", 0)
    cache_creation_tokens = usage_dict.get("cache_creation_input_tokens", 0)
    cost_cents = compute_cost_cents(model, input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens)

    async with get_db() as db:
        cursor = await db.execute(
            """INSERT INTO turns
               (session_id, created_at, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_cents, user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                session_id, _now_iso(), model,
                input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens,
                cost_cents, user_text, assistant_text,
            ),
        )
        turn_id = cursor.lastrowid
        await db.execute(
            """UPDATE sessions
               SET total_cost_cents = total_cost_cents + ?,
                   turn_count = turn_count + 1
               WHERE id = ?""",
            (cost_cents, session_id),
        )
        await db.commit()

    logger.info("Turn recorded session=%s model=%s cost=%d¢", session_id, model, cost_cents)
    return {
        "id": turn_id,
        "session_id": session_id,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cache_creation_tokens": cache_creation_tokens,
        "cost_cents": cost_cents,
    }


async def get_session_totals(session_id: str) -> dict:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cur.fetchone()
        if not row:
            return {}

        cur2 = await db.execute(
            """SELECT
               COALESCE(SUM(input_tokens), 0) AS input_tokens,
               COALESCE(SUM(output_tokens), 0) AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cached_tokens
               FROM turns WHERE session_id = ?""",
            (session_id,),
        )
        turns_row = await cur2.fetchone()

        started_at = datetime.fromisoformat(row["started_at"])
        ended_at_str = row["ended_at"]
        ended_at = datetime.fromisoformat(ended_at_str) if ended_at_str else datetime.now(timezone.utc)
        duration_s = (ended_at - started_at).total_seconds()

        return {
            "input_tokens": turns_row["input_tokens"] if turns_row else 0,
            "output_tokens": turns_row["output_tokens"] if turns_row else 0,
            "cached_tokens": turns_row["cached_tokens"] if turns_row else 0,
            "cost_cents": row["total_cost_cents"],
            "turn_count": row["turn_count"],
            "duration_s": round(duration_s),
        }


async def get_rolling_totals() -> dict:
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start_dt = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=now.weekday()
    )
    week_start = week_start_dt.isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    async with get_db() as db:
        async def _sum_since(since: str) -> int:
            cur = await db.execute(
                "SELECT COALESCE(SUM(total_cost_cents), 0) AS total FROM sessions WHERE started_at >= ?",
                (since,),
            )
            row = await cur.fetchone()
            return row["total"] if row else 0

        today_cents = await _sum_since(today_start)
        week_cents = await _sum_since(week_start)
        month_cents = await _sum_since(month_start)

    return {
        "today_cents": today_cents,
        "week_cents": week_cents,
        "month_cents": month_cents,
    }


async def list_sessions(limit: int = 50) -> list[dict]:
    async with get_db() as db:
        cursor = await db.execute(
            "SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,)
        )
        rows = await cursor.fetchall()

    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        row = dict(r)
        started = datetime.fromisoformat(row["started_at"])
        ended = datetime.fromisoformat(row["ended_at"]) if row.get("ended_at") else now
        row["duration_s"] = round((ended - started).total_seconds())
        out.append(row)
    return out


async def get_session_with_turns(session_id: str) -> dict | None:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        session_row = await cur.fetchone()
        if not session_row:
            return None
        cursor = await db.execute(
            "SELECT * FROM turns WHERE session_id = ? ORDER BY created_at ASC", (session_id,)
        )
        turn_rows = await cursor.fetchall()

    return {
        **dict(session_row),
        "turns": [dict(r) for r in turn_rows],
    }
