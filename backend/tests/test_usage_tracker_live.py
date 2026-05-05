"""Stage 3 usage_tracker tests — Live audio billing + $15/day cap.

Audio token cost on the Live native-audio model is a new pricing leg
(``audio_in`` / ``audio_out`` rates in ``PRICING_PER_MTOK``); the
``check_daily_cap`` helper sums Claude / Gemini / Live cents + STT/TTS
USD across today's turns to enforce the per-day spending ceiling.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import pytest_asyncio

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("OWNER_PASSWORD", "test")
os.environ.setdefault("JWT_SECRET", "test")


@pytest_asyncio.fixture
async def temp_db(tmp_path, monkeypatch):
    """Per-test SQLite DB at CHIEF_DB_PATH; reload db + usage_tracker."""
    db_file = tmp_path / "test_live.db"
    monkeypatch.setenv("CHIEF_DB_PATH", str(db_file))
    for mod in ("db", "services.usage_tracker"):
        if mod in sys.modules:
            del sys.modules[mod]
    from db import init_db
    await init_db()
    yield db_file


# ---------------------------------------------------------------------------
# Audio token billing
# ---------------------------------------------------------------------------
def test_compute_cost_audio_input_billed_at_audio_in_rate():
    """Live audio_in rate is $3/M; audio_out is $12/M."""
    from services.usage_tracker import compute_cost_cents

    # Pure audio input: 1M tokens × $3 = $3.00 = 300 cents
    cents = compute_cost_cents(
        "gemini-live-2.5-flash-native-audio",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        audio_input_tokens=1_000_000,
        audio_output_tokens=0,
    )
    assert cents == 300


def test_compute_cost_audio_output_billed_at_audio_out_rate():
    from services.usage_tracker import compute_cost_cents

    # Pure audio output: 1M tokens × $12 = $12.00 = 1200 cents
    cents = compute_cost_cents(
        "gemini-live-2.5-flash-native-audio",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        audio_input_tokens=0,
        audio_output_tokens=1_000_000,
    )
    assert cents == 1200


def test_compute_cost_mixed_text_and_audio_for_live_model():
    """Spec example: audio_in=5000, audio_out=3000, text_in=1000, text_out=200.

    cost = (5000 * $3 + 3000 * $12 + 1000 * $0.50 + 200 * $2) / 1M
         = (15_000 + 36_000 + 500 + 400) / 1M
         = 51_900 / 1_000_000 dollars
         = $0.0519 = 5.19 cents → rounds to 5
    """
    from services.usage_tracker import compute_cost_cents

    cents = compute_cost_cents(
        "gemini-live-2.5-flash-native-audio",
        input_tokens=1000,
        output_tokens=200,
        cache_read_tokens=0,
        cache_creation_tokens=0,
        audio_input_tokens=5000,
        audio_output_tokens=3000,
    )
    # 5.19 → round() → 5
    assert cents == 5


def test_compute_cost_non_live_model_ignores_audio_tokens():
    """Audio token args on a Claude model must not bleed into the bill."""
    from services.usage_tracker import compute_cost_cents

    no_audio = compute_cost_cents(
        "claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_read_tokens=0, cache_creation_tokens=0,
    )
    with_audio_args = compute_cost_cents(
        "claude-sonnet-4-6",
        input_tokens=1000, output_tokens=500,
        cache_read_tokens=0, cache_creation_tokens=0,
        audio_input_tokens=10_000, audio_output_tokens=5_000,
    )
    assert no_audio == with_audio_args


def test_compute_cost_legacy_callers_unchanged():
    """Callers that don't pass audio kwargs see the same cents as before."""
    from services.usage_tracker import compute_cost_cents

    # Legacy positional-only call shape (5 args) must still work.
    cents = compute_cost_cents("claude-haiku-4-5", 1_000_000, 1_000_000, 0, 0)
    # Haiku: $1/M in + $5/M out = $6 = 600c
    assert cents == 600


@pytest.mark.asyncio
async def test_record_turn_includes_audio_token_cost_for_live_model(temp_db):
    """End-to-end: record_turn with audio tokens writes the right cost_cents row."""
    from services.usage_tracker import create_session, record_turn

    sid = "live-turn"
    await create_session(sid)
    turn = await record_turn(
        session_id=sid,
        model="gemini-live-2.5-flash-native-audio",
        usage_dict={
            "input_tokens": 1000,
            "output_tokens": 200,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "audio_input_tokens": 5000,
            "audio_output_tokens": 3000,
        },
        user_text="hi chief",
        assistant_text="hi neill",
    )
    # 5.19c → 5 (matches compute_cost spec example).
    assert turn["cost_cents"] == 5


# ---------------------------------------------------------------------------
# Daily cost cap
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_check_daily_cap_under_returns_false(temp_db):
    """Empty DB → over_cap=False, current=0."""
    from services.usage_tracker import check_daily_cap

    over_cap, current = await check_daily_cap("owner")
    assert over_cap is False
    assert current == 0.0


@pytest.mark.asyncio
async def test_check_daily_cap_over_15_dollars(temp_db):
    """Today's cost > $15 → over_cap=True."""
    from services.usage_tracker import (
        check_daily_cap,
        create_session,
        record_turn,
    )

    sid = "expensive-day"
    await create_session(sid)
    # Opus at $5/M in + $25/M out. To hit $15+ in cents we need:
    #   $15 = 1_500c. Opus output cost = 25 * tokens/1M; need 600_000 tokens
    #   to get $15 in pure output. Use input + output for safety.
    await record_turn(
        session_id=sid,
        model="claude-opus-4-7",
        usage_dict={"input_tokens": 1_000_000, "output_tokens": 500_000},
        user_text="x",
        assistant_text="y",
    )
    # 1M * $5 = $5 + 500K * $25 = $12.50  =  $17.50 = 1750c
    over_cap, current = await check_daily_cap("owner")
    assert over_cap is True
    assert current >= 15.0


@pytest.mark.asyncio
async def test_check_daily_cap_includes_voice_cost_in_total(temp_db):
    """STT/TTS USD is summed alongside cents-based Claude cost."""
    from services.usage_tracker import (
        check_daily_cap,
        create_session,
        record_stt_usage,
        record_tts_usage,
        record_turn,
    )

    sid = "voice-cost-test"
    await create_session(sid)
    turn = await record_turn(
        session_id=sid,
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 100, "output_tokens": 50},
        user_text="hi",
        assistant_text="hello",
    )
    # Voice cost = $0.044 (30s STT + 1200char TTS at Google rates).
    await record_stt_usage(turn["id"], "google", 30.0)
    await record_tts_usage(turn["id"], "google", 1200)

    over_cap, current = await check_daily_cap("owner")
    assert over_cap is False
    # Should include the $0.044 voice cost plus the haiku cents.
    assert current > 0.04


@pytest.mark.asyncio
async def test_check_daily_cap_buckets_by_turn_created_at(temp_db):
    """A turn created yesterday must NOT count against today's cap."""
    from db import get_db
    from services.usage_tracker import check_daily_cap

    yesterday = (
        datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        - timedelta(days=1)
    ).isoformat()
    async with get_db() as db:
        await db.execute(
            """INSERT INTO sessions (id, user_id, started_at, total_cost_cents, turn_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("yesterday-session", "owner", yesterday, 200_000, 1),
        )
        await db.execute(
            """INSERT INTO turns
               (session_id, created_at, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_cents,
                user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("yesterday-session", yesterday, "claude-opus-4-7", 0, 0, 0, 0, 200_000, "y", "y"),
        )
        await db.commit()

    over_cap, current = await check_daily_cap("owner")
    assert over_cap is False
    assert current == 0.0


@pytest.mark.asyncio
async def test_check_daily_cap_respects_subject_filter(temp_db):
    """Cost on subject=alice must not count against subject=owner's cap."""
    from db import get_db
    from services.usage_tracker import check_daily_cap, _now_iso

    async with get_db() as db:
        await db.execute(
            """INSERT INTO sessions (id, user_id, started_at, total_cost_cents, turn_count)
               VALUES (?, ?, ?, ?, ?)""",
            ("alice-session", "alice", _now_iso(), 200_000, 1),
        )
        await db.execute(
            """INSERT INTO turns
               (session_id, created_at, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_cents,
                user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("alice-session", _now_iso(), "claude-opus-4-7", 0, 0, 0, 0, 200_000, "x", "y"),
        )
        await db.commit()

    over_cap_owner, current_owner = await check_daily_cap("owner")
    over_cap_alice, current_alice = await check_daily_cap("alice")
    assert over_cap_owner is False
    assert current_owner == 0.0
    assert over_cap_alice is True


@pytest.mark.asyncio
async def test_check_daily_cap_env_override(temp_db, monkeypatch):
    """DAILY_COST_CAP_DOLLARS env var overrides the default."""
    from services.usage_tracker import (
        check_daily_cap,
        create_session,
        record_turn,
    )

    sid = "env-override"
    await create_session(sid)
    # Spend ~$0.25 (haiku 100K out): 100_000 * $5/M = $0.50
    await record_turn(
        session_id=sid,
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 0, "output_tokens": 100_000},
        user_text="x",
        assistant_text="y",
    )
    # Default cap = $15 → not over.
    over_default, _ = await check_daily_cap("owner")
    assert over_default is False
    # With cap set to $0.10 → over.
    monkeypatch.setenv("DAILY_COST_CAP_DOLLARS", "0.10")
    over_low, _ = await check_daily_cap("owner")
    assert over_low is True


# ---------------------------------------------------------------------------
# record_think_deep_cost
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_record_think_deep_cost_writes_turn_row(temp_db):
    """A think_deep escalation lands as a turn row with cost > 0 (Sonnet rate)."""
    from db import get_db
    from services.usage_tracker import record_think_deep_cost

    turn = await record_think_deep_cost(
        model="claude-sonnet-4-6",
        scope="Chief Command",
        input_tokens=1000,
        output_tokens=500,
        prompt="walk me through it",
        assistant_text="careful answer",
    )
    # Sonnet: $3/M in × 1k = $0.003 + $15/M out × 500 = $0.0075 → $0.0105 → 1c
    assert turn["cost_cents"] >= 1

    # Stored in DB on the synthetic session.
    async with get_db() as db:
        cur = await db.execute(
            "SELECT model, input_tokens, output_tokens FROM turns WHERE session_id = ?",
            ("think-deep-bookkeeping",),
        )
        row = await cur.fetchone()
    assert row is not None
    assert row["model"] == "claude-sonnet-4-6"
    assert row["input_tokens"] == 1000
    assert row["output_tokens"] == 500


@pytest.mark.asyncio
async def test_record_think_deep_cost_counts_toward_daily_cap(temp_db):
    """Escalation cost must show up in check_daily_cap."""
    from services.usage_tracker import check_daily_cap, record_think_deep_cost

    # Burn enough Opus tokens to exceed $15 in escalation alone.
    # Opus = $5/M in + $25/M out. 1M in + 500K out = $5 + $12.50 = $17.50.
    await record_think_deep_cost(
        model="claude-opus-4-7",
        scope="Chief Command",
        input_tokens=1_000_000,
        output_tokens=500_000,
    )
    over_cap, current = await check_daily_cap("owner")
    assert over_cap is True
    assert current >= 15.0
