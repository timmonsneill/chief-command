"""The Grok reviewer runs over plain HTTP and never turns a broken call into a verdict.

Grok came back onto the panel on 2026-08-27, the first third family with a working
runner. Two things must stay true: a tool failure (no key, HTTP error, unreadable reply)
is a SKIP, never a FAIL against the build — verdicts are permanent and bound to the
version — and the runner only ever sends the bundle it was handed, nothing from disk.
"""

import io
import json
import sys
import urllib.error
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import gauntlet  # noqa: E402
from dispatch import load_config  # noqa: E402


class _Resp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _answering(text: str, seen: dict):
    def fake(req, timeout):
        seen["url"] = req.full_url
        seen["headers"] = dict(req.header_items())
        seen["body"] = json.loads(req.data.decode())
        return _Resp(json.dumps(
            {"choices": [{"message": {"content": text}}]}).encode())
    return fake


def test_a_pass_from_grok_is_read_as_a_pass(monkeypatch):
    seen = {}
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(gauntlet.urllib.request, "urlopen", _answering("PASS looks right", seen))
    verdict, why = gauntlet._xai_review("add two numbers", "def add(a,b): return a+b", "grok-4.5")
    assert verdict == "pass"
    assert seen["body"]["model"] == "grok-4.5"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    # The reviewer sees exactly the work it was handed — nothing from the checkout.
    assert "def add(a,b)" in seen["body"]["messages"][0]["content"]


def test_a_fail_from_grok_is_read_as_a_fail(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(gauntlet.urllib.request, "urlopen", _answering("FAIL it subtracts", {}))
    assert gauntlet._xai_review("add", "def add(a,b): return a-b", "grok-4.5")[0] == "fail"


def test_no_key_is_a_broken_tool_not_a_verdict(monkeypatch):
    monkeypatch.delenv("XAI_API_KEY", raising=False)
    with pytest.raises(gauntlet.ReviewerBroke):
        gauntlet._xai_review("x", "y", "grok-4.5")


def test_an_http_error_is_a_broken_tool_not_a_verdict(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    def boom(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 429, "slow down", {}, io.BytesIO(b"rate limited"))
    monkeypatch.setattr(gauntlet.urllib.request, "urlopen", boom)
    with pytest.raises(gauntlet.ReviewerBroke):
        gauntlet._xai_review("x", "y", "grok-4.5")


def test_an_unreadable_reply_is_a_broken_tool_not_a_verdict(monkeypatch):
    monkeypatch.setenv("XAI_API_KEY", "test-key")
    monkeypatch.setattr(gauntlet.urllib.request, "urlopen",
                        lambda req, timeout: _Resp(b'{"error": "nope"}'))
    with pytest.raises(gauntlet.ReviewerBroke):
        gauntlet._xai_review("x", "y", "grok-4.5")


def test_the_grok_seat_is_on_and_has_a_runner():
    cfg = load_config()
    grok = cfg["seats"]["grok"]
    assert not grok.get("disabled", False), "grok is still switched off in seats.toml"
    assert gauntlet.has_runner(grok["provider"])
    assert gauntlet.has_runner(grok["fallback"]["provider"])
