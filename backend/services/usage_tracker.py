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


async def create_session(session_id: str, user_id: str = "owner", project: str | None = None) -> None:
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (id, user_id, started_at, total_cost_cents, turn_count, project) VALUES (?, ?, ?, 0, 0, ?)",
            (session_id, user_id, _now_iso(), project),
        )
        await db.commit()
    logger.info("Session created: %s project=%s", session_id, project)


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


async def list_sessions(limit: int = 50, project: str | None = None) -> list[dict]:
    async with get_db() as db:
        if project and project != "All":
            cursor = await db.execute(
                "SELECT * FROM sessions WHERE project = ? ORDER BY started_at DESC LIMIT ?",
                (project, limit),
            )
        else:
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


async def get_by_model_totals() -> dict:
    """Return per-model cost/token/turn totals for today, this week, and this month."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
    week_start = (
        now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
    ).isoformat()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()

    query = """
        SELECT
            model,
            COALESCE(SUM(cost_cents), 0)    AS cost_cents,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COUNT(*)                         AS turns
        FROM turns
        WHERE created_at >= ?
        GROUP BY model
    """

    def _rows_to_dict(rows) -> dict:
        result: dict = {}
        for row in rows:
            result[row["model"]] = {
                "cost_cents": row["cost_cents"],
                "input_tokens": row["input_tokens"],
                "output_tokens": row["output_tokens"],
                "turns": row["turns"],
            }
        return result

    async with get_db() as db:
        cur = await db.execute(query, (today_start,))
        today_rows = await cur.fetchall()

        cur = await db.execute(query, (week_start,))
        week_rows = await cur.fetchall()

        cur = await db.execute(query, (month_start,))
        month_rows = await cur.fetchall()

    return {
        "today": _rows_to_dict(today_rows),
        "week": _rows_to_dict(week_rows),
        "month": _rows_to_dict(month_rows),
    }


async def get_daily_series(days: int = 30) -> list[dict]:
    """Return daily cost_cents and turn counts for the last `days` days, oldest-first."""
    days = min(days, 365)
    since = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=days - 1)
    ).isoformat()

    async with get_db() as db:
        cur = await db.execute(
            """
            SELECT
                substr(created_at, 1, 10)    AS date,
                COALESCE(SUM(cost_cents), 0) AS cost_cents,
                COUNT(*)                      AS turns
            FROM turns
            WHERE created_at >= ?
            GROUP BY date
            ORDER BY date ASC
            """,
            (since,),
        )
        rows = await cur.fetchall()

    return [
        {"date": row["date"], "cost_cents": row["cost_cents"], "turns": row["turns"]}
        for row in rows
    ]


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
