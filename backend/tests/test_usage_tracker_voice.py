"""Voice usage tracking unit tests.

Covers the three deliverables for Phase 1.1 voice-cost integration:

  1. Math correctness: record_stt_usage / record_tts_usage produce the
     right USD totals for Google and local providers.
  2. Rollup inclusion: the voice blocks show up in get_rolling_totals,
     get_session_totals, get_by_model_totals, and get_daily_series.
  3. Schema migration: booting init_db against
       (a) a fresh DB                   → columns exist via _DDL
       (b) a pre-existing DB without them → ALTER TABLE adds them cleanly
     both succeed and preserve existing rows.

Run:
    pytest -x backend/tests/test_usage_tracker_voice.py -v
"""

import aiosqlite
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def temp_db_path(tmp_path, monkeypatch):
    """Point CHIEF_DB_PATH at a per-test SQLite file and reload modules.

    Python caches `db.DB_PATH` at import time, so we have to clear any prior
    import and re-import the modules under test after setting the env var.
    """
    db_file = tmp_path / "test_usage.db"
    monkeypatch.setenv("CHIEF_DB_PATH", str(db_file))

    # Force a re-import so DB_PATH picks up the new env var.
    import sys
    for mod in ("db", "services.usage_tracker"):
        if mod in sys.modules:
            del sys.modules[mod]

    import db  # noqa: F401  — re-imported with new DB_PATH

    yield db_file


@pytest_asyncio.fixture
async def initialised_db(temp_db_path):
    """Yield a path to a fresh, init_db-initialised SQLite file."""
    from db import init_db

    await init_db()
    yield temp_db_path


@pytest_asyncio.fixture
async def seeded_session(initialised_db):
    """Create a session + one turn we can attach voice usage to."""
    from services.usage_tracker import create_session, record_turn

    session_id = "test-session-voice"
    await create_session(session_id)
    turn = await record_turn(
        session_id=session_id,
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 100, "output_tokens": 200},
        user_text="hello chief",
        assistant_text="hi there",
    )
    yield session_id, turn["id"]


# ---------------------------------------------------------------------------
# Pricing math
# ---------------------------------------------------------------------------

def test_voice_pricing_constants_match_spec():
    """Pricing table must match the April 2026 Google rates in the spec."""
    from services.usage_tracker import VOICE_PRICING

    # $0.016 per minute of STT audio = $0.00026... per second
    assert VOICE_PRICING["google_stt"]["usd_per_unit"] == pytest.approx(0.016 / 60)
    # $30 per 1M characters of TTS input = $0.00003 per char
    assert VOICE_PRICING["google_tts"]["usd_per_unit"] == pytest.approx(30 / 1_000_000)
    # Local providers are always free — keeps schema uniform.
    assert VOICE_PRICING["local_stt"]["usd_per_unit"] == 0.0
    assert VOICE_PRICING["local_tts"]["usd_per_unit"] == 0.0


def test_compute_stt_cost_for_30_seconds_google():
    """30 seconds at $0.016/min → $0.008 exactly."""
    from services.usage_tracker import compute_stt_cost_usd

    cost = compute_stt_cost_usd("google", 30.0)
    assert cost == pytest.approx(0.008)


def test_compute_tts_cost_for_1200_chars_google():
    """1200 chars at $30/1M → $0.036 exactly."""
    from services.usage_tracker import compute_tts_cost_usd

    cost = compute_tts_cost_usd("google", 1200)
    assert cost == pytest.approx(0.036)


def test_compute_cost_local_always_zero():
    """Local providers should roll up as $0 regardless of volume."""
    from services.usage_tracker import compute_stt_cost_usd, compute_tts_cost_usd

    assert compute_stt_cost_usd("local", 600.0) == 0.0
    assert compute_tts_cost_usd("local", 100_000) == 0.0


def test_compute_cost_unknown_provider_falls_back_to_local():
    """Misspelled provider string must not crash — fall back to free."""
    from services.usage_tracker import compute_stt_cost_usd, compute_tts_cost_usd

    assert compute_stt_cost_usd("garbage", 30.0) == 0.0
    assert compute_tts_cost_usd("garbage", 1200) == 0.0


# ---------------------------------------------------------------------------
# record_* functions — writes land on the right row
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_stt_usage_writes_seconds_and_cost(seeded_session):
    from db import get_db
    from services.usage_tracker import record_stt_usage

    session_id, turn_id = seeded_session

    result = await record_stt_usage(turn_id, "google", 30.0)
    assert result["stt_seconds"] == 30.0
    assert result["stt_provider"] == "google"
    assert result["stt_cost_usd"] == pytest.approx(0.008)

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM turns WHERE id = ?", (turn_id,))
        row = await cur.fetchone()
    assert row["stt_seconds"] == 30.0
    assert row["stt_provider"] == "google"
    assert row["stt_cost_usd"] == pytest.approx(0.008)


@pytest.mark.asyncio
async def test_record_tts_usage_writes_chars_and_cost(seeded_session):
    from db import get_db
    from services.usage_tracker import record_tts_usage

    session_id, turn_id = seeded_session

    result = await record_tts_usage(turn_id, "google", 1200)
    assert result["tts_chars"] == 1200
    assert result["tts_provider"] == "google"
    assert result["tts_cost_usd"] == pytest.approx(0.036)

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM turns WHERE id = ?", (turn_id,))
        row = await cur.fetchone()
    assert row["tts_chars"] == 1200
    assert row["tts_provider"] == "google"
    assert row["tts_cost_usd"] == pytest.approx(0.036)


@pytest.mark.asyncio
async def test_record_local_usage_zero_cost_but_data_preserved(seeded_session):
    """Even in local mode, seconds/chars are preserved for visibility."""
    from db import get_db
    from services.usage_tracker import record_stt_usage, record_tts_usage

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "local", 30.0)
    await record_tts_usage(turn_id, "local", 1200)

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM turns WHERE id = ?", (turn_id,))
        row = await cur.fetchone()
    assert row["stt_seconds"] == 30.0
    assert row["stt_cost_usd"] == 0.0
    assert row["tts_chars"] == 1200
    assert row["tts_cost_usd"] == 0.0
    assert row["stt_provider"] == "local"
    assert row["tts_provider"] == "local"


@pytest.mark.asyncio
async def test_record_is_overwrite_not_additive(seeded_session):
    """Retries during a single turn should not double-bill."""
    from db import get_db
    from services.usage_tracker import record_stt_usage

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "google", 30.0)
    await record_stt_usage(turn_id, "google", 45.0)  # retry w/ updated number

    async with get_db() as db:
        cur = await db.execute("SELECT stt_seconds FROM turns WHERE id = ?", (turn_id,))
        row = await cur.fetchone()
    assert row["stt_seconds"] == 45.0


# ---------------------------------------------------------------------------
# Rollups
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_session_totals_includes_voice_block(seeded_session):
    from services.usage_tracker import (
        get_session_totals,
        record_stt_usage,
        record_tts_usage,
    )

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "google", 30.0)
    await record_tts_usage(turn_id, "google", 1200)

    totals = await get_session_totals(session_id)
    assert "voice" in totals
    assert totals["voice"]["stt"]["seconds"] == 30.0
    assert totals["voice"]["stt"]["cost_usd"] == pytest.approx(0.008)
    assert totals["voice"]["tts"]["chars"] == 1200
    assert totals["voice"]["tts"]["cost_usd"] == pytest.approx(0.036)
    assert totals["voice"]["total_usd"] == pytest.approx(0.044)


@pytest.mark.asyncio
async def test_get_rolling_totals_includes_voice_window_data(seeded_session):
    from services.usage_tracker import (
        get_rolling_totals,
        record_stt_usage,
        record_tts_usage,
    )

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "google", 30.0)
    await record_tts_usage(turn_id, "google", 1200)

    totals = await get_rolling_totals()
    assert "voice" in totals
    for window in ("today", "week", "month"):
        assert window in totals["voice"]
        assert "stt" in totals["voice"][window]
        assert "tts" in totals["voice"][window]
        assert "total_usd" in totals["voice"][window]

    # Today must include the just-recorded turn.
    today = totals["voice"]["today"]
    assert today["stt"]["seconds"] == pytest.approx(30.0)
    assert today["tts"]["chars"] == 1200
    assert today["total_usd"] == pytest.approx(0.044)


@pytest.mark.asyncio
async def test_rolling_provider_breakdown_splits_google_vs_local(initialised_db):
    """Mixed-provider turns should aggregate per-provider without cross-pollution."""
    from services.usage_tracker import (
        create_session,
        get_rolling_totals,
        record_stt_usage,
        record_tts_usage,
        record_turn,
    )

    await create_session("mixed-providers")

    t1 = await record_turn(
        session_id="mixed-providers",
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 10, "output_tokens": 10},
        user_text="t1", assistant_text="r1",
    )
    t2 = await record_turn(
        session_id="mixed-providers",
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 10, "output_tokens": 10},
        user_text="t2", assistant_text="r2",
    )

    await record_stt_usage(t1["id"], "google", 30.0)
    await record_tts_usage(t1["id"], "google", 1000)
    await record_stt_usage(t2["id"], "local", 60.0)
    await record_tts_usage(t2["id"], "local", 2000)

    today = (await get_rolling_totals())["voice"]["today"]

    stt_prov = today["stt"]["provider_breakdown"]
    tts_prov = today["tts"]["provider_breakdown"]
    assert stt_prov.get("google", {}).get("seconds") == pytest.approx(30.0)
    assert stt_prov.get("local", {}).get("seconds") == pytest.approx(60.0)
    assert tts_prov.get("google", {}).get("chars") == 1000
    assert tts_prov.get("local", {}).get("chars") == 2000
    # Only google contributes to the $-total; local is $0.
    assert today["total_usd"] == pytest.approx(0.008 + 0.03)


@pytest.mark.asyncio
async def test_get_by_model_totals_includes_voice(seeded_session):
    from services.usage_tracker import (
        get_by_model_totals,
        record_stt_usage,
        record_tts_usage,
    )

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "google", 30.0)
    await record_tts_usage(turn_id, "google", 1200)

    by_model = await get_by_model_totals()
    assert "voice" in by_model
    assert "today" in by_model["voice"]
    assert by_model["voice"]["today"]["total_usd"] == pytest.approx(0.044)


@pytest.mark.asyncio
async def test_get_daily_series_adds_voice_cost_per_day(seeded_session):
    from services.usage_tracker import (
        get_daily_series,
        record_stt_usage,
        record_tts_usage,
    )

    session_id, turn_id = seeded_session
    await record_stt_usage(turn_id, "google", 30.0)
    await record_tts_usage(turn_id, "google", 1200)

    series = await get_daily_series(days=30)
    assert len(series) >= 1
    today = series[-1]
    assert "voice_cost_usd" in today
    assert "stt_cost_usd" in today
    assert "tts_cost_usd" in today
    assert today["voice_cost_usd"] == pytest.approx(0.044)


@pytest.mark.asyncio
async def test_rolling_totals_bucket_by_turn_created_at_not_session_start(initialised_db):
    """A session that straddles midnight must attribute each turn's cost to
    the day the TURN happened, not the session's started_at.

    Regression against the pre-fix behaviour where get_rolling_totals used
    ``sessions.started_at`` for Claude cost but ``turns.created_at`` for
    voice cost — producing disjoint windows.

    Setup: one session starts at 23:55 yesterday with a Claude turn at
    23:55, then produces a voice turn at 00:05 today. After the fix, the
    Claude turn should land in yesterday's bucket and the voice turn in
    today's — and `today_cents` should reflect ONLY the today-turn.
    """
    from datetime import datetime, timezone, timedelta

    from db import get_db
    from services.usage_tracker import (
        compute_cost_cents,
        get_rolling_totals,
        record_stt_usage,
        record_tts_usage,
    )

    now = datetime.now(timezone.utc)
    today_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    yesterday_2355 = (today_midnight - timedelta(minutes=5)).isoformat()
    today_0005 = (today_midnight + timedelta(minutes=5)).isoformat()
    session_started = yesterday_2355

    # Insert the session + two turns at explicit timestamps so we can
    # straddle midnight deterministically.
    model = "claude-haiku-4-5"
    yesterday_cost_cents = compute_cost_cents(model, 1000, 500, 0, 0)
    today_cost_cents = compute_cost_cents(model, 2000, 1000, 0, 0)
    async with get_db() as db:
        await db.execute(
            "INSERT INTO sessions (id, started_at, total_cost_cents, turn_count) VALUES (?, ?, ?, ?)",
            ("straddle", session_started, yesterday_cost_cents + today_cost_cents, 2),
        )
        # Turn A: 23:55 yesterday — Claude-only
        await db.execute(
            """INSERT INTO turns
               (session_id, created_at, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_cents,
                user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("straddle", yesterday_2355, model, 1000, 500, 0, 0, yesterday_cost_cents, "y", "y"),
        )
        # Turn B: 00:05 today — Claude + voice
        cur = await db.execute(
            """INSERT INTO turns
               (session_id, created_at, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, cost_cents,
                user_text, assistant_text)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("straddle", today_0005, model, 2000, 1000, 0, 0, today_cost_cents, "t", "t"),
        )
        today_turn_id = cur.lastrowid
        await db.commit()

    await record_stt_usage(today_turn_id, "google", 30.0)
    await record_tts_usage(today_turn_id, "google", 1200)

    totals = await get_rolling_totals()

    # Claude bucket: today must include ONLY the today-turn's cost.
    assert totals["today_cents"] == today_cost_cents, (
        "today_cents should bucket by turns.created_at, not sessions.started_at"
    )
    # Week still includes both (assuming same ISO week); month likewise.
    # We only assert the straddle invariant strictly on today.

    # Voice bucket: today must include the voice charges from the today-turn.
    assert totals["voice"]["today"]["total_usd"] == pytest.approx(0.044)

    # Sanity: yesterday's turn was registered (should be visible in wider windows)
    # — this guards against accidentally dropping the row.
    assert totals["week_cents"] >= yesterday_cost_cents + today_cost_cents
    assert totals["month_cents"] >= yesterday_cost_cents + today_cost_cents


@pytest.mark.asyncio
async def test_get_rolling_totals_no_turns_returns_zero_voice(initialised_db):
    """Empty DB must return a $0 voice block, not missing keys."""
    from services.usage_tracker import get_rolling_totals

    totals = await get_rolling_totals()
    for window in ("today", "week", "month"):
        vw = totals["voice"][window]
        assert vw["stt"]["seconds"] == 0.0
        assert vw["tts"]["chars"] == 0
        assert vw["total_usd"] == 0.0
        assert vw["stt"]["provider_breakdown"] == {}
        assert vw["tts"]["provider_breakdown"] == {}


# ---------------------------------------------------------------------------
# Migration idempotency
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_init_db_is_idempotent_on_fresh_db(temp_db_path):
    """Two init_db calls on a fresh DB must not raise."""
    from db import init_db

    await init_db()
    await init_db()  # Must be a no-op, not an error.


@pytest.mark.asyncio
async def test_migration_adds_voice_columns_to_pre_migration_db(tmp_path, monkeypatch):
    """Simulate a pre-existing chief.db (voice columns absent).

    Write the OLD schema by hand, insert a row, then run init_db() and
    verify:
      (a) the migration ran without error,
      (b) the six voice columns were added,
      (c) the pre-existing row is preserved.
    """
    db_file = tmp_path / "pre_migration.db"
    monkeypatch.setenv("CHIEF_DB_PATH", str(db_file))

    # Force re-import so DB_PATH picks up the env var.
    import sys
    for mod in ("db", "services.usage_tracker"):
        if mod in sys.modules:
            del sys.modules[mod]

    # Build a DB with the old schema (no voice cols).
    OLD_DDL = """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'owner',
        started_at TEXT NOT NULL,
        ended_at TEXT,
        total_cost_cents INTEGER NOT NULL DEFAULT 0,
        turn_count INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE turns (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL REFERENCES sessions(id),
        created_at TEXT NOT NULL,
        model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        cache_read_tokens INTEGER NOT NULL DEFAULT 0,
        cache_creation_tokens INTEGER NOT NULL DEFAULT 0,
        cost_cents INTEGER NOT NULL DEFAULT 0,
        user_text TEXT NOT NULL DEFAULT '',
        assistant_text TEXT NOT NULL DEFAULT ''
    );
    """
    async with aiosqlite.connect(db_file) as db:
        await db.executescript(OLD_DDL)
        await db.execute(
            "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
            ("legacy-session", "2026-04-17T00:00:00+00:00"),
        )
        await db.execute(
            """INSERT INTO turns (session_id, created_at, model, input_tokens, output_tokens)
               VALUES (?, ?, ?, ?, ?)""",
            ("legacy-session", "2026-04-17T00:00:00+00:00", "claude-haiku-4-5", 100, 50),
        )
        await db.commit()

    # Run init_db — should ALTER TABLE and not blow up.
    from db import init_db

    await init_db()

    # Verify columns are there and legacy row is untouched.
    async with aiosqlite.connect(db_file) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("PRAGMA table_info(turns)")
        cols = {r[1] for r in await cur.fetchall()}
        assert {
            "stt_seconds", "stt_provider", "stt_cost_usd",
            "tts_chars", "tts_provider", "tts_cost_usd",
        }.issubset(cols)

        cur = await db.execute("SELECT * FROM turns WHERE session_id = ?", ("legacy-session",))
        row = await cur.fetchone()
        assert row["input_tokens"] == 100
        # Migrated rows default voice columns to 0 / NULL.
        assert row["stt_seconds"] == 0
        assert row["stt_provider"] is None
        assert row["tts_cost_usd"] == 0

    # Second init_db call: must still be a no-op.
    await init_db()


# ---------------------------------------------------------------------------
# Mocked WS-flow end-to-end: Google STT → Claude → Google TTS
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ws_flow_populates_both_stt_and_tts_cost(initialised_db):
    """Simulate the websockets._run_llm_turn flow with Google providers.

    Verifies that a turn row ends up with BOTH STT and TTS usage populated
    after record_turn + record_stt_usage + record_tts_usage run in the order
    the WS handler uses.
    """
    from db import get_db
    from services.usage_tracker import (
        create_session,
        get_session_totals,
        record_stt_usage,
        record_tts_usage,
        record_turn,
    )

    # Simulate a 30-second audio input at 16 kHz / 16-bit → 960_000 bytes.
    audio_bytes = 16000 * 2 * 30
    stt_seconds = audio_bytes / (16000 * 2)
    assert stt_seconds == 30.0

    # Assistant text (what the LLM streamed + sent to TTS)
    assistant_text = "x" * 1200

    session_id = "ws-flow-sim"
    await create_session(session_id)

    # 1. record_turn (Claude side) — use token counts large enough to
    # produce a non-zero rounded-cent Claude cost so the assertion isn't
    # brittle across pricing tiers.
    turn = await record_turn(
        session_id=session_id,
        model="claude-opus-4-7",
        usage_dict={"input_tokens": 200_000, "output_tokens": 50_000},
        user_text="simulated user text",
        assistant_text=assistant_text,
    )

    # 2. record_stt_usage (what _handle_audio_turn would do after record_turn)
    await record_stt_usage(turn["id"], provider="google", audio_seconds=stt_seconds)

    # 3. record_tts_usage (sum of sentence lengths that went to TTS)
    await record_tts_usage(turn["id"], provider="google", chars=len(assistant_text))

    # Verify the row has all three legs.
    async with get_db() as db:
        cur = await db.execute("SELECT * FROM turns WHERE id = ?", (turn["id"],))
        row = await cur.fetchone()
    assert row["cost_cents"] > 0  # Claude
    assert row["stt_seconds"] == 30.0
    assert row["stt_provider"] == "google"
    assert row["stt_cost_usd"] == pytest.approx(0.008)
    assert row["tts_chars"] == 1200
    assert row["tts_provider"] == "google"
    assert row["tts_cost_usd"] == pytest.approx(0.036)

    totals = await get_session_totals(session_id)
    assert totals["voice"]["total_usd"] == pytest.approx(0.044)


@pytest.mark.asyncio
async def test_ws_flow_local_mode_records_zero_cost(initialised_db):
    """Local mode must still write a turn row with voice fields — just $0."""
    from db import get_db
    from services.usage_tracker import (
        create_session,
        record_stt_usage,
        record_tts_usage,
        record_turn,
    )

    await create_session("ws-local")
    turn = await record_turn(
        session_id="ws-local",
        model="claude-haiku-4-5",
        usage_dict={"input_tokens": 10, "output_tokens": 10},
        user_text="q", assistant_text="a",
    )
    await record_stt_usage(turn["id"], provider="local", audio_seconds=30.0)
    await record_tts_usage(turn["id"], provider="local", chars=1200)

    async with get_db() as db:
        cur = await db.execute("SELECT * FROM turns WHERE id = ?", (turn["id"],))
        row = await cur.fetchone()
    # Duration/char data preserved for operator visibility.
    assert row["stt_seconds"] == 30.0
    assert row["tts_chars"] == 1200
    # But cost is zero — not billed.
    assert row["stt_cost_usd"] == 0.0
    assert row["tts_cost_usd"] == 0.0


# ---------------------------------------------------------------------------
# End-to-end sanity
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# settings table — runtime-tunable knobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_setting_returns_default_when_missing(initialised_db):
    """A fresh DB has no rows in `settings`; reads must return the default."""
    from db import get_setting, get_setting_float

    assert await get_setting("nonexistent") is None
    assert await get_setting("nonexistent", default="fallback") == "fallback"
    assert await get_setting_float("nonexistent_float", 50.0) == 50.0


@pytest.mark.asyncio
async def test_set_then_get_setting_round_trip(initialised_db):
    """UPSERT semantics: write then read returns the latest value."""
    from db import get_setting, get_setting_float, set_setting

    await set_setting("monthly_voice_warning_usd", "75.0")
    assert await get_setting("monthly_voice_warning_usd") == "75.0"
    assert await get_setting_float("monthly_voice_warning_usd", 50.0) == 75.0

    # Re-write — ON CONFLICT replaces.
    await set_setting("monthly_voice_warning_usd", "100.0")
    assert await get_setting_float("monthly_voice_warning_usd", 50.0) == 100.0


@pytest.mark.asyncio
async def test_get_setting_float_falls_back_on_garbage(initialised_db):
    """Corrupted text in the table must not break the caller — return default."""
    from db import get_setting_float, set_setting

    await set_setting("monthly_voice_warning_usd", "not-a-number")
    # Rather than raising, fall back to the default.
    assert await get_setting_float("monthly_voice_warning_usd", 50.0) == 50.0


@pytest.mark.asyncio
async def test_end_to_end_google_turn_math(initialised_db):
    """Record a turn with 30s STT + 1200-char TTS @ google rates.

    Sanity check for the owner: 30s * $0.016/60 + 1200 * $30/1e6 = $0.044
    """
    from services.usage_tracker import (
        create_session,
        get_session_totals,
        record_stt_usage,
        record_tts_usage,
        record_turn,
    )

    session_id = "e2e-google"
    await create_session(session_id)
    turn = await record_turn(
        session_id=session_id,
        model="claude-sonnet-4-6",
        usage_dict={"input_tokens": 500, "output_tokens": 300},
        user_text="how's the project?",
        assistant_text="Going well. Dispatch bridge shipped Thursday.",
    )

    await record_stt_usage(turn["id"], "google", 30.0)
    await record_tts_usage(turn["id"], "google", 1200)

    totals = await get_session_totals(session_id)
    assert totals["voice"]["total_usd"] == pytest.approx(0.044)
    # Claude cost is independent — $0.0015 input + $0.0045 output = ~$0.006
    # Cents, rounded. Don't pin exact — just confirm it's non-zero.
    assert totals["cost_cents"] > 0
