"""Tests for services.memory_rollup — rolling cross-session memory (Phase 3).

The Flash client is mocked at the gemini_brain._get_client boundary so
these tests run without google-genai actually issuing a request.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest


def _point_db_at_tmp(tmp_path: Path) -> Path:
    """Redirect history-store DB to tmp + reset init cache.

    Mirrors the helper in test_history_store. Both modules read the same
    sqlite file — memory_rollup goes through history_store for every DB
    interaction.
    """
    db_path = tmp_path / "voice_history.db"
    os.environ["VOICE_HISTORY_DB_PATH"] = str(db_path)
    import services.history_store as store
    store._initialized = False
    return db_path


# ---------------------------------------------------------------------------
# Flash mock — fakes ``client.aio.models.generate_content(...)``
# ---------------------------------------------------------------------------
class _FakeResponse:
    def __init__(self, text: str) -> None:
        # Mirror the candidates[0].content.parts[*].text shape that
        # memory_rollup walks.
        part = MagicMock()
        part.text = text
        part.thought = False
        content = MagicMock()
        content.parts = [part]
        candidate = MagicMock()
        candidate.content = content
        self.candidates = [candidate]


class _FakeFlashClient:
    """Stands in for google.genai.Client. Captures the call args so tests
    can assert what we actually sent to Flash."""

    def __init__(self, output_text: str = "Decisions made: (none)") -> None:
        self.output_text = output_text
        self.calls: list[dict] = []
        # Mirror client.aio.models.generate_content(...).
        self.aio = MagicMock()
        self.aio.models = MagicMock()
        self.aio.models.generate_content = self._generate_content

    async def _generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self.output_text)


def _install_fake_flash(monkeypatch, output_text="summary v1"):
    """Patch services.gemini_brain._get_client so memory_rollup gets the fake."""
    fake = _FakeFlashClient(output_text=output_text)
    import services.gemini_brain as gb
    monkeypatch.setattr(gb, "_get_client", lambda: fake)
    return fake


# ---------------------------------------------------------------------------
# Constants / module-level smoke
# ---------------------------------------------------------------------------
def test_module_constants_set():
    """ROLLUP_TRIGGER_TURNS / ROLLUP_MODEL etc. are exposed for the WS layer
    and the cost tracker — test pins them so a future tweak doesn't silently
    drift the trigger threshold."""
    import services.memory_rollup as rollup

    assert rollup.ROLLUP_TRIGGER_TURNS == 30
    assert rollup.ROLLUP_MODEL == "gemini-2.5-flash"
    assert rollup.MAX_ROLLUP_TURNS == 80
    assert rollup.MAX_SUMMARY_TOKENS == 600


# ---------------------------------------------------------------------------
# maybe_rollup — under threshold no-ops
# ---------------------------------------------------------------------------
def test_maybe_rollup_under_threshold_noops(tmp_path, monkeypatch):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch)

    async def run():
        # Below trigger — no Flash call, no summary row written.
        for i in range(5):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        await rollup.maybe_rollup("Arch")
        return await store.latest_summary("Arch")

    summary = asyncio.run(run())
    assert summary is None
    assert fake.calls == []  # Flash never called


# ---------------------------------------------------------------------------
# maybe_rollup — over threshold writes a row
# ---------------------------------------------------------------------------
def test_maybe_rollup_over_threshold_calls_flash_and_writes(tmp_path, monkeypatch):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch, output_text="rolled summary body")

    async def run():
        # Trigger threshold = 30 — write 32 to fire it.
        for i in range(32):
            role = "user" if i % 2 == 0 else "assistant"
            await store.append_turn("sess-1", "Arch", role, f"msg-{i}")
        await rollup.maybe_rollup("Arch")
        return await store.latest_summary("Arch")

    summary = asyncio.run(run())
    assert summary is not None
    assert summary["summary_text"] == "rolled summary body"
    assert summary["model"] == rollup.ROLLUP_MODEL
    # Watermark should be the highest id we summarized (= 32, since rowid
    # starts at 1 and we wrote 32 rows).
    assert summary["covers_through_turn_id"] == 32
    # Flash WAS called.
    assert len(fake.calls) == 1
    assert fake.calls[0]["model"] == "gemini-2.5-flash"


# ---------------------------------------------------------------------------
# maybe_rollup — second rollup factors in prior summary
# ---------------------------------------------------------------------------
def test_maybe_rollup_includes_prior_summary_in_prompt(tmp_path, monkeypatch):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch, output_text="summary B")

    async def run():
        # Seed a prior summary directly so we don't need 60 turns.
        await store.append_summary(
            "Arch", None, "PRIOR-SUMMARY-MARKER", 3, rollup.ROLLUP_MODEL,
        )
        # Now write enough new turns past id=3 to trigger.
        # First seed 3 dummy turns to get past the watermark, then 30 fresh.
        for i in range(3):
            await store.append_turn("sess-x", "Arch", "user", f"old-{i}")
        # turns_since_summary uses the COUNT of voice_turns rows after the
        # watermark — which now includes the 3 just-added rows. So we need
        # 30+ rows past id=3. The 3 we just wrote have ids 1..3 (matching
        # the watermark), so we need at least 30 more.
        for i in range(31):
            await store.append_turn("sess-x", "Arch", "assistant", f"new-{i}")
        await rollup.maybe_rollup("Arch")
        return await store.latest_summary("Arch")

    summary = asyncio.run(run())
    # Latest summary is now the second one we wrote.
    assert summary is not None
    assert summary["summary_text"] == "summary B"
    # The prompt fed to Flash should mention the prior summary marker.
    assert len(fake.calls) == 1
    contents = fake.calls[0]["contents"]
    # Walk every Part and look for the marker.
    found_marker = False
    for content in contents:
        for part in getattr(content, "parts", []) or []:
            text = getattr(part, "text", "") or ""
            if "PRIOR-SUMMARY-MARKER" in text:
                found_marker = True
    assert found_marker, "prior summary not folded into Flash prompt"


# ---------------------------------------------------------------------------
# maybe_rollup — race protection
# ---------------------------------------------------------------------------
def test_concurrent_maybe_rollup_calls_dont_double_write(tmp_path, monkeypatch):
    """Two concurrent maybe_rollup calls for the same project must not both
    fire Flash + write a row — the second should see the first's lock and
    skip."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch, output_text="single summary")

    async def run():
        for i in range(35):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        await asyncio.gather(
            rollup.maybe_rollup("Arch"),
            rollup.maybe_rollup("Arch"),
            rollup.maybe_rollup("Arch"),
        )
        # Count rows in voice_summaries directly to confirm exactly one wrote.
        import aiosqlite
        db = await aiosqlite.connect(os.environ["VOICE_HISTORY_DB_PATH"])
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT COUNT(*) AS n FROM voice_summaries WHERE project = ?",
                ("Arch",),
            )
            row = await cur.fetchone()
            return row["n"]
        finally:
            await db.close()

    count = asyncio.run(run())
    assert count == 1
    # And Flash was called at most once (the lock-skip path also avoids
    # the SDK call).
    assert len(fake.calls) == 1


# ---------------------------------------------------------------------------
# maybe_rollup — error path
# ---------------------------------------------------------------------------
def test_maybe_rollup_swallows_flash_errors(tmp_path, monkeypatch):
    """A Flash 5xx / network error must NOT raise out of maybe_rollup —
    it's fire-and-forget from the WS layer."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    class _BoomClient:
        def __init__(self):
            self.aio = MagicMock()
            self.aio.models = MagicMock()
            self.aio.models.generate_content = self._boom

        async def _boom(self, **_):
            raise RuntimeError("simulated 503 from Flash")

    import services.gemini_brain as gb
    monkeypatch.setattr(gb, "_get_client", lambda: _BoomClient())

    async def run():
        for i in range(35):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        # Must not raise.
        await rollup.maybe_rollup("Arch")
        return await store.latest_summary("Arch")

    summary = asyncio.run(run())
    # Flash failed → no summary row written.
    assert summary is None


# ---------------------------------------------------------------------------
# maybe_rollup — empty project / empty input
# ---------------------------------------------------------------------------
def test_maybe_rollup_empty_project_string_noops(tmp_path, monkeypatch):
    _point_db_at_tmp(tmp_path)
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch)

    async def run():
        await rollup.maybe_rollup("")  # empty project
        return None

    asyncio.run(run())
    assert fake.calls == []


def test_maybe_rollup_blank_summary_response_skips_write(tmp_path, monkeypatch):
    """If Flash returns whitespace-only output, we should NOT write a phantom
    blank summary row."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    _install_fake_flash(monkeypatch, output_text="   \n  ")

    async def run():
        for i in range(35):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        await rollup.maybe_rollup("Arch")
        return await store.latest_summary("Arch")

    summary = asyncio.run(run())
    assert summary is None


# ---------------------------------------------------------------------------
# Prompt structure — system instruction + user payload
# ---------------------------------------------------------------------------
def test_summarizer_prompt_structure_carries_required_sections(tmp_path, monkeypatch):
    """The system instruction passed to Flash must contain all three
    structured sections so the model produces a comparable shape on every
    rollup."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    fake = _install_fake_flash(monkeypatch, output_text="ok")

    async def run():
        for i in range(31):
            await store.append_turn("sess-1", "Arch", "user", f"q{i}")
        await rollup.maybe_rollup("Arch")

    asyncio.run(run())
    assert len(fake.calls) == 1
    config = fake.calls[0]["config"]
    sys_instr = getattr(config, "system_instruction", "") or ""
    assert "Decisions made:" in sys_instr
    assert "Current focus:" in sys_instr
    assert "Open threads:" in sys_instr


# ---------------------------------------------------------------------------
# load_persistent_memory — happy path + degraded paths
# ---------------------------------------------------------------------------
def test_load_persistent_memory_returns_summary_and_recent_turns(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    async def run():
        await store.append_turn("sess-1", "Arch", "user", "hello")
        await store.append_turn("sess-1", "Arch", "assistant", "hi")
        await store.append_summary(
            "Arch", None, "rolled summary", 2, rollup.ROLLUP_MODEL,
        )
        return await rollup.load_persistent_memory("Arch", raw_limit=20)

    summary, recent = asyncio.run(run())
    assert summary == "rolled summary"
    assert recent == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
    ]


def test_load_persistent_memory_returns_none_summary_when_absent(tmp_path):
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    async def run():
        await store.append_turn("sess-1", "Arch", "user", "only one turn")
        return await rollup.load_persistent_memory("Arch")

    summary, recent = asyncio.run(run())
    assert summary is None
    assert recent == [{"role": "user", "content": "only one turn"}]


def test_load_persistent_memory_handles_missing_db_gracefully(tmp_path, monkeypatch):
    """If history_store raises on either read, load_persistent_memory should
    return safe defaults rather than letting the exception escape."""
    _point_db_at_tmp(tmp_path)
    import services.history_store as store
    import services.memory_rollup as rollup

    async def boom(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    monkeypatch.setattr(store, "latest_summary", boom)
    monkeypatch.setattr(store, "load_recent_for_project", boom)

    async def run():
        return await rollup.load_persistent_memory("Arch")

    summary, recent = asyncio.run(run())
    assert summary is None
    assert recent == []
