"""The dashboard shows this week's usage for the flat-rate seats, and never dies on it."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

import server  # noqa: E402
import usage_local  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_cache():
    server._USAGE_CACHE.update(at=0.0, value=None)
    yield
    server._USAGE_CACHE.update(at=0.0, value=None)


def test_state_carries_this_weeks_usage_in_plain_english(monkeypatch):
    fake = {"gpt": usage_local.FamilyUsage("gpt", sessions=3, output_tokens=4000,
                                           allowance_used_percent=12.0,
                                           allowance_window_minutes=10080),
            "claude": usage_local.FamilyUsage("claude", sessions=1, output_tokens=800)}
    monkeypatch.setattr(usage_local, "local_usage", lambda days=7, **kw: fake)
    s = TestClient(server.app).get("/api/state").json()
    week = s["usage_week"]
    assert any("Codex: 3 sessions" in line for line in week["summary"])
    assert week["families"]["gpt"]["allowance_used_percent"] == 12.0
    for line in week["summary"]:
        for jargon in ("jsonl", "token", ".codex", "rate_limit"):
            assert jargon not in line


def test_a_broken_usage_reader_does_not_break_the_dashboard(monkeypatch):
    def boom(days=7, **kw):
        raise OSError("disk went away")
    monkeypatch.setattr(usage_local, "local_usage", boom)
    s = TestClient(server.app).get("/api/state").json()
    assert s["usage_week"]["summary"] == ["Couldn't read this week's usage right now."]
    assert "seats" in s and "jobs" in s


def test_usage_is_cached_not_rescanned_every_poll(monkeypatch):
    calls = {"n": 0}
    def counted(days=7, **kw):
        calls["n"] += 1
        return {"gpt": usage_local.FamilyUsage("gpt"), "claude": usage_local.FamilyUsage("claude")}
    monkeypatch.setattr(usage_local, "local_usage", counted)
    c = TestClient(server.app)
    c.get("/api/state"); c.get("/api/state"); c.get("/api/state")
    assert calls["n"] == 1
