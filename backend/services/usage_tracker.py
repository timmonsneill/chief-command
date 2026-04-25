"""Session and turn persistence with cost tracking.

Tracks three billed legs per voice turn:
  1. Claude tokens                → turns.cost_cents   (existing)
  2. Google STT (audio seconds)   → turns.stt_cost_usd (voice port)
  3. Google TTS (text characters) → turns.tts_cost_usd (voice port)

Voice costs are stored in USD (float) rather than cents (int) because Google's
per-second / per-character rates produce sub-cent per-turn costs that would
lose precision if rounded to cents on write. Rollup endpoints convert to
whatever unit the frontend wants.
"""

import logging
from datetime import datetime, timedelta, timezone

from db import get_db

logger = logging.getLogger(__name__)

PRICING_PER_MTOK = {
    "claude-haiku-4-5":  {"in": 1.0,  "out": 5.0,  "cached_in": 0.1},
    "claude-sonnet-4-6": {"in": 3.0,  "out": 15.0, "cached_in": 0.3},
    "claude-opus-4-7":   {"in": 5.0,  "out": 25.0, "cached_in": 0.5},
}

# ---------------------------------------------------------------------------
# Voice pricing (April 2026)
# ---------------------------------------------------------------------------
# Cloud Speech-to-Text v2 streaming: $0.016 / minute of audio
# Cloud TTS Chirp 3 HD:              $30 / 1M characters
# Local providers (faster-whisper / Kokoro) are free — $0 rate keeps the
# schema uniform so rollups work regardless of provider.
VOICE_PRICING: dict[str, dict[str, float | str]] = {
    "google_stt": {"unit": "second", "usd_per_unit": 0.016 / 60},
    "google_tts": {"unit": "char",   "usd_per_unit": 30 / 1_000_000},
    "local_stt":  {"unit": "second", "usd_per_unit": 0.0},
    "local_tts":  {"unit": "char",   "usd_per_unit": 0.0},
}


def _stt_rate(provider: str) -> float:
    """USD per second of STT audio for the given provider."""
    key = f"{(provider or 'local').lower()}_stt"
    rate = VOICE_PRICING.get(key, VOICE_PRICING["local_stt"])["usd_per_unit"]
    return float(rate)


def _tts_rate(provider: str) -> float:
    """USD per character of TTS output for the given provider."""
    key = f"{(provider or 'local').lower()}_tts"
    rate = VOICE_PRICING.get(key, VOICE_PRICING["local_tts"])["usd_per_unit"]
    return float(rate)


def compute_stt_cost_usd(provider: str, audio_seconds: float) -> float:
    """Cost in USD for `audio_seconds` of STT at the given provider's rate."""
    seconds = max(0.0, float(audio_seconds or 0.0))
    return seconds * _stt_rate(provider)


def compute_tts_cost_usd(provider: str, chars: int) -> float:
    """Cost in USD for `chars` of TTS input at the given provider's rate."""
    n = max(0, int(chars or 0))
    return n * _tts_rate(provider)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_cost_cents(model: str, input_tokens: int, output_tokens: int, cache_read_tokens: int, cache_creation_tokens: int) -> int:
    # Synthetic narration turn rows carry no LLM tokens — they're just an
    # audit anchor for the per-narration TTS char bill. Short-circuit to 0
    # so a future refactor that accidentally passes nonzero tokens here
    # can't silently bill them at the haiku fallback rate. Riggs HIGH
    # 2026-04-24 round 2.
    if model == "narration":
        return 0
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


async def record_stt_usage(turn_id: int, provider: str, audio_seconds: float) -> dict:
    """Attach STT usage (seconds + cost) to an already-recorded turn row.

    Called by the WebSocket handler after `record_turn` has returned the
    turn's rowid. Idempotent-ish: overwrites whatever was there, so a retry
    in the same turn doesn't double-bill.

    Returns the fields written so the caller can include them in the usage
    WS event without re-querying.
    """
    seconds = max(0.0, float(audio_seconds or 0.0))
    cost_usd = compute_stt_cost_usd(provider, seconds)
    async with get_db() as db:
        await db.execute(
            """UPDATE turns
               SET stt_seconds = ?, stt_provider = ?, stt_cost_usd = ?
               WHERE id = ?""",
            (seconds, provider, cost_usd, turn_id),
        )
        await db.commit()
    logger.info(
        "STT usage recorded turn=%s provider=%s seconds=%.2f cost_usd=%.6f",
        turn_id, provider, seconds, cost_usd,
    )
    return {"stt_seconds": seconds, "stt_provider": provider, "stt_cost_usd": cost_usd}


async def record_tts_usage(turn_id: int, provider: str, chars: int) -> dict:
    """Attach TTS usage (chars + cost) to an already-recorded turn row.

    See record_stt_usage — symmetric for TTS.
    """
    n = max(0, int(chars or 0))
    cost_usd = compute_tts_cost_usd(provider, n)
    async with get_db() as db:
        await db.execute(
            """UPDATE turns
               SET tts_chars = ?, tts_provider = ?, tts_cost_usd = ?
               WHERE id = ?""",
            (n, provider, cost_usd, turn_id),
        )
        await db.commit()
    logger.info(
        "TTS usage recorded turn=%s provider=%s chars=%d cost_usd=%.6f",
        turn_id, provider, n, cost_usd,
    )
    return {"tts_chars": n, "tts_provider": provider, "tts_cost_usd": cost_usd}


async def get_session_totals(session_id: str) -> dict:
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,))
        row = await cur.fetchone()
        if not row:
            return {}

        cur2 = await db.execute(
            """SELECT
               COALESCE(SUM(input_tokens), 0)     AS input_tokens,
               COALESCE(SUM(output_tokens), 0)    AS output_tokens,
               COALESCE(SUM(cache_read_tokens), 0) AS cached_tokens,
               COALESCE(SUM(stt_seconds), 0.0)    AS stt_seconds,
               COALESCE(SUM(stt_cost_usd), 0.0)   AS stt_cost_usd,
               COALESCE(SUM(tts_chars), 0)        AS tts_chars,
               COALESCE(SUM(tts_cost_usd), 0.0)   AS tts_cost_usd
               FROM turns WHERE session_id = ?""",
            (session_id,),
        )
        turns_row = await cur2.fetchone()

        started_at = datetime.fromisoformat(row["started_at"])
        ended_at_str = row["ended_at"]
        ended_at = datetime.fromisoformat(ended_at_str) if ended_at_str else datetime.now(timezone.utc)
        duration_s = (ended_at - started_at).total_seconds()

        stt_cost = float(turns_row["stt_cost_usd"]) if turns_row else 0.0
        tts_cost = float(turns_row["tts_cost_usd"]) if turns_row else 0.0

        return {
            "input_tokens": turns_row["input_tokens"] if turns_row else 0,
            "output_tokens": turns_row["output_tokens"] if turns_row else 0,
            "cached_tokens": turns_row["cached_tokens"] if turns_row else 0,
            "cost_cents": row["total_cost_cents"],
            "turn_count": row["turn_count"],
            "duration_s": round(duration_s),
            "voice": {
                "stt": {
                    "seconds": float(turns_row["stt_seconds"]) if turns_row else 0.0,
                    "cost_usd": stt_cost,
                },
                "tts": {
                    "chars": int(turns_row["tts_chars"]) if turns_row else 0,
                    "cost_usd": tts_cost,
                },
                "total_usd": stt_cost + tts_cost,
            },
        }


async def _voice_rollup_for_window(db, since: str) -> dict:
    """Voice usage rollup for `turns` created at or after `since`.

    Shape:
      {
        stt: { seconds, cost_usd, provider_breakdown: {google|local|unknown: {...}} },
        tts: { chars,   cost_usd, provider_breakdown: {google|local|unknown: {...}} },
        total_usd,
      }

    NULL providers (turns recorded before the migration landed) roll up under
    'unknown' so they still contribute to totals but don't pollute the
    google/local counters.
    """
    cur = await db.execute(
        """SELECT
            COALESCE(SUM(stt_seconds), 0.0)  AS stt_seconds,
            COALESCE(SUM(stt_cost_usd), 0.0) AS stt_cost_usd,
            COALESCE(SUM(tts_chars), 0)      AS tts_chars,
            COALESCE(SUM(tts_cost_usd), 0.0) AS tts_cost_usd
           FROM turns WHERE created_at >= ?""",
        (since,),
    )
    row = await cur.fetchone()
    stt_seconds = float(row["stt_seconds"]) if row else 0.0
    stt_cost = float(row["stt_cost_usd"]) if row else 0.0
    tts_chars = int(row["tts_chars"]) if row else 0
    tts_cost = float(row["tts_cost_usd"]) if row else 0.0

    # Provider breakdown — STT. Only rows with >0 seconds so we don't
    # surface zero-activity providers.
    cur = await db.execute(
        """SELECT
            COALESCE(stt_provider, 'unknown') AS provider,
            COALESCE(SUM(stt_seconds), 0.0)   AS seconds,
            COALESCE(SUM(stt_cost_usd), 0.0)  AS cost_usd
           FROM turns
           WHERE created_at >= ? AND stt_seconds > 0
           GROUP BY COALESCE(stt_provider, 'unknown')""",
        (since,),
    )
    stt_providers: dict[str, dict] = {
        r["provider"]: {
            "seconds": float(r["seconds"]),
            "cost_usd": float(r["cost_usd"]),
        }
        for r in await cur.fetchall()
    }

    # Provider breakdown — TTS.
    cur = await db.execute(
        """SELECT
            COALESCE(tts_provider, 'unknown') AS provider,
            COALESCE(SUM(tts_chars), 0)       AS chars,
            COALESCE(SUM(tts_cost_usd), 0.0)  AS cost_usd
           FROM turns
           WHERE created_at >= ? AND tts_chars > 0
           GROUP BY COALESCE(tts_provider, 'unknown')""",
        (since,),
    )
    tts_providers: dict[str, dict] = {
        r["provider"]: {
            "chars": int(r["chars"]),
            "cost_usd": float(r["cost_usd"]),
        }
        for r in await cur.fetchall()
    }

    return {
        "stt": {
            "seconds": stt_seconds,
            "cost_usd": stt_cost,
            "provider_breakdown": stt_providers,
        },
        "tts": {
            "chars": tts_chars,
            "cost_usd": tts_cost,
            "provider_breakdown": tts_providers,
        },
        "total_usd": stt_cost + tts_cost,
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
            # Bucket by turns.created_at (not sessions.started_at) so a session
            # that straddles midnight attributes each turn's Claude cost to the
            # day the TURN happened. This aligns with voice rollups (also
            # turn-bucketed) and get_by_model_totals / get_daily_series.
            cur = await db.execute(
                "SELECT COALESCE(SUM(cost_cents), 0) AS total FROM turns WHERE created_at >= ?",
                (since,),
            )
            row = await cur.fetchone()
            return row["total"] if row else 0

        today_cents = await _sum_since(today_start)
        week_cents = await _sum_since(week_start)
        month_cents = await _sum_since(month_start)

        # Voice rollups pulled from `turns` (voice cost is stored per-turn, not
        # per-session). Claude-side field names unchanged — voice is additive.
        today_voice = await _voice_rollup_for_window(db, today_start)
        week_voice = await _voice_rollup_for_window(db, week_start)
        month_voice = await _voice_rollup_for_window(db, month_start)

    return {
        "today_cents": today_cents,
        "week_cents": week_cents,
        "month_cents": month_cents,
        "voice": {
            "today": today_voice,
            "week": week_voice,
            "month": month_voice,
        },
    }


async def list_sessions(limit: int = 50, project: str | None = None) -> list[dict]:
    async with get_db() as db:
        if project:
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

    # Filter ``model = 'narration'`` so synthetic narration rows
    # (task-complete / cancelled / dispatch-failed lines) don't show up as
    # a distinct "narration" bucket on UsagePage.ModelBreakdown — that
    # would expose internal bookkeeping to the owner. Voice $ for those
    # narrations is still counted by `_voice_rollup_for_window` (which
    # rolls every row regardless of model). Riggs CRITICAL 2026-04-24
    # round 2.
    query = """
        SELECT
            model,
            COALESCE(SUM(cost_cents), 0)    AS cost_cents,
            COALESCE(SUM(input_tokens), 0)  AS input_tokens,
            COALESCE(SUM(output_tokens), 0) AS output_tokens,
            COUNT(*)                         AS turns
        FROM turns
        WHERE created_at >= ? AND model != 'narration'
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

        # Voice rollups alongside per-model Claude totals. Frontend renders
        # them separately but bundling saves a round-trip.
        today_voice = await _voice_rollup_for_window(db, today_start)
        week_voice = await _voice_rollup_for_window(db, week_start)
        month_voice = await _voice_rollup_for_window(db, month_start)

    return {
        "today": _rows_to_dict(today_rows),
        "week": _rows_to_dict(week_rows),
        "month": _rows_to_dict(month_rows),
        "voice": {
            "today": today_voice,
            "week": week_voice,
            "month": month_voice,
        },
    }


async def get_daily_series(days: int = 30) -> list[dict]:
    """Return daily cost_cents, voice cost_usd and turn counts for the last
    `days` days, oldest-first.

    Daily series feeds the 30-day trend chart on UsagePage. Voice cost is
    included alongside Claude cost_cents per day so the chart can stack or
    overlay them.
    """
    days = min(days, 365)
    since = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=days - 1)
    ).isoformat()

    async with get_db() as db:
        # Exclude narration rows from the COUNT(turns) column so the daily
        # turn count reflects real Chief replies, not internal narration
        # bookkeeping (which on a busy day would roughly double the rendered
        # number). The voice cost columns (stt_cost_usd / tts_cost_usd) MUST
        # still include narration rows because that Google spend is real.
        # Implementation: the main aggregate filters narration out for the
        # turn count + claude cost, then a correlated subquery ADDS the
        # narration row's voice cost back per-day. Riggs CRITICAL
        # 2026-04-24 round 2.
        cur = await db.execute(
            """
            SELECT
                substr(created_at, 1, 10)           AS date,
                COALESCE(SUM(cost_cents), 0)        AS cost_cents,
                COALESCE(SUM(stt_cost_usd), 0.0)    AS stt_cost_usd,
                COALESCE(SUM(tts_cost_usd), 0.0)    AS tts_cost_usd,
                COUNT(*)                             AS turns
            FROM turns
            WHERE created_at >= ? AND model != 'narration'
            GROUP BY date
            ORDER BY date ASC
            """,
            (since,),
        )
        rows = await cur.fetchall()

        # Per-day voice cost for narration rows specifically — added back
        # onto the per-day series so Google spend isn't lost. Keyed by the
        # same `date` substring used in the main query.
        cur = await db.execute(
            """
            SELECT
                substr(created_at, 1, 10)           AS date,
                COALESCE(SUM(stt_cost_usd), 0.0)    AS stt_cost_usd,
                COALESCE(SUM(tts_cost_usd), 0.0)    AS tts_cost_usd
            FROM turns
            WHERE created_at >= ? AND model = 'narration'
            GROUP BY date
            """,
            (since,),
        )
        narration_voice = {
            row["date"]: (
                float(row["stt_cost_usd"]),
                float(row["tts_cost_usd"]),
            )
            for row in await cur.fetchall()
        }
        # Days that had ONLY narration rows (and no real-Claude rows) won't
        # appear in `rows` because the main query filtered them. Synthesize
        # zero-turn entries for those days so their voice $ shows up.
        narration_only_dates = set(narration_voice) - {row["date"] for row in rows}

    out: list[dict] = []
    for row in rows:
        narr_stt, narr_tts = narration_voice.get(row["date"], (0.0, 0.0))
        stt_total = float(row["stt_cost_usd"]) + narr_stt
        tts_total = float(row["tts_cost_usd"]) + narr_tts
        out.append({
            "date": row["date"],
            "cost_cents": row["cost_cents"],
            "turns": row["turns"],
            "stt_cost_usd": stt_total,
            "tts_cost_usd": tts_total,
            "voice_cost_usd": stt_total + tts_total,
        })
    # Days that had ONLY narration rows still need to appear in the trend.
    for date in sorted(narration_only_dates):
        narr_stt, narr_tts = narration_voice[date]
        out.append({
            "date": date,
            "cost_cents": 0,
            "turns": 0,
            "stt_cost_usd": narr_stt,
            "tts_cost_usd": narr_tts,
            "voice_cost_usd": narr_stt + narr_tts,
        })
    out.sort(key=lambda r: r["date"])
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
