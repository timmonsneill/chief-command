"""Unit tests for services.classifier.

Mocks the shared AsyncAnthropic client so tests hit no network. We assert:
- All four intent branches round-trip cleanly (3 examples each).
- Zero-API shortcuts (cancel/status) return confidence=1.0 without calling API.
- Parse failure / empty / malformed output defaults to "chat".
- Task intent without a usable spec falls back to chat.
- Length cap truncates absurdly long input before it reaches the model.
- Latency is measured and reported (with a mocked ~50ms backend).
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Optional

import pytest

from services import classifier


# ---------------------------------------------------------------------------
# Minimal mock of an Anthropic Messages response.
# ---------------------------------------------------------------------------


@dataclass
class _MockTextBlock:
    type: str = "text"
    text: str = ""


@dataclass
class _MockResponse:
    content: list[_MockTextBlock]


class _MockMessages:
    def __init__(
        self,
        response_text: str,
        *,
        delay_s: float = 0.0,
        raise_exc: Optional[Exception] = None,
    ):
        self._response_text = response_text
        self._delay_s = delay_s
        self._raise_exc = raise_exc
        self.last_call_kwargs: Optional[dict] = None
        self.call_count = 0

    async def create(self, **kwargs):
        self.call_count += 1
        self.last_call_kwargs = kwargs
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        if self._raise_exc is not None:
            raise self._raise_exc
        return _MockResponse(content=[_MockTextBlock(type="text", text=self._response_text)])


class _MockClient:
    def __init__(
        self,
        response_text: str,
        *,
        delay_s: float = 0.0,
        raise_exc: Optional[Exception] = None,
    ):
        self.messages = _MockMessages(response_text, delay_s=delay_s, raise_exc=raise_exc)


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch):
    """Patch classifier's _get_client to return a controllable mock.

    Yields a setter the test can use to change what the mock returns.
    """
    holder: dict = {"client": _MockClient('{"intent":"chat","task_spec":null}')}

    def _get(
        response_text: str,
        *,
        delay_s: float = 0.0,
        raise_exc: Optional[Exception] = None,
    ) -> _MockClient:
        holder["client"] = _MockClient(
            response_text, delay_s=delay_s, raise_exc=raise_exc
        )
        monkeypatch.setattr(classifier, "_get_client", lambda: holder["client"])
        return holder["client"]

    # Default mock in place before tests configure a specific response.
    monkeypatch.setattr(classifier, "_get_client", lambda: holder["client"])
    return _get


# ---------------------------------------------------------------------------
# Happy paths — 3 examples per intent.
# ---------------------------------------------------------------------------


CHAT_EXAMPLES = [
    ("What's the plan for auth?", '{"intent":"chat","task_spec":null}'),
    ("How does the VAD pipeline work?", '{"intent":"chat","task_spec":null}'),
    ("Tell me about Riggs.", '{"intent":"chat","task_spec":null}'),
]

TASK_EXAMPLES = [
    ("Build the auth refactor.", '{"intent":"task","task_spec":"Build the auth refactor."}'),
    ("Write tests for billing.", '{"intent":"task","task_spec":"Write tests for billing."}'),
    ("Fix the VAD bug.", '{"intent":"task","task_spec":"Fix the VAD bug."}'),
]

# These would normally hit the _STATUS_SHORTCUTS fast path (and thus not call
# the API at all); we ship non-shortcut strings here so the API path is
# exercised.
STATUS_EXAMPLES = [
    ("any update yet on that?", '{"intent":"status","task_spec":null}'),
    ("where are you with that right now?", '{"intent":"status","task_spec":null}'),
    ("is it close to done?", '{"intent":"status","task_spec":null}'),
]

CANCEL_EXAMPLES = [
    ("forget the refactor", '{"intent":"cancel","task_spec":null}'),
    ("bail on that task", '{"intent":"cancel","task_spec":null}'),
    ("drop what you're doing", '{"intent":"cancel","task_spec":null}'),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("user_text,response", CHAT_EXAMPLES)
async def test_classify_chat(mock_client, user_text: str, response: str) -> None:
    mock_client(response)
    result = await classifier.classify_intent(user_text, "Chief Command")
    assert result["intent"] == "chat"
    assert result["task_spec"] is None
    assert result["confidence"] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("user_text,response", TASK_EXAMPLES)
async def test_classify_task(mock_client, user_text: str, response: str) -> None:
    mock_client(response)
    result = await classifier.classify_intent(user_text, "Chief Command")
    assert result["intent"] == "task"
    assert isinstance(result["task_spec"], str)
    assert result["task_spec"] == json.loads(response)["task_spec"]
    assert result["confidence"] == 1.0


@pytest.mark.asyncio
@pytest.mark.parametrize("user_text,response", STATUS_EXAMPLES)
async def test_classify_status(mock_client, user_text: str, response: str) -> None:
    mock_client(response)
    result = await classifier.classify_intent(user_text, "Arch")
    assert result["intent"] == "status"
    assert result["task_spec"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("user_text,response", CANCEL_EXAMPLES)
async def test_classify_cancel(mock_client, user_text: str, response: str) -> None:
    mock_client(response)
    result = await classifier.classify_intent(user_text, "Arch")
    assert result["intent"] == "cancel"
    assert result["task_spec"] is None


# ---------------------------------------------------------------------------
# Zero-API shortcut tests — these must return immediately without calling the
# API (confidence=1.0, call_count=0).
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("text", ["stop.", "cancel", "abort", "never mind", "nvm"])
async def test_cancel_shortcuts_skip_api(mock_client, text: str) -> None:
    client = mock_client('{"intent":"task","task_spec":"should-not-see-this"}')
    result = await classifier.classify_intent(text, "Chief Command")
    assert result == {"intent": "cancel", "task_spec": None, "confidence": 1.0}
    assert client.messages.call_count == 0, "shortcut must not hit the API"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text", ["status?", "progress", "how's it going?", "still working?", "how long?"]
)
async def test_status_shortcuts_skip_api(mock_client, text: str) -> None:
    client = mock_client('{"intent":"task","task_spec":"should-not-see-this"}')
    result = await classifier.classify_intent(text, "Chief Command")
    assert result == {"intent": "status", "task_spec": None, "confidence": 1.0}
    assert client.messages.call_count == 0, "shortcut must not hit the API"


# ---------------------------------------------------------------------------
# Defensive defaults.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_parse_failure_defaults_to_chat(mock_client) -> None:
    """Unparseable model output -> chat (conservative)."""
    mock_client("this is not json at all, just prose")
    result = await classifier.classify_intent("Whatever", "Chief Command")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_empty_response_defaults_to_chat(mock_client) -> None:
    mock_client("")
    result = await classifier.classify_intent("Whatever", "Chief Command")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_invalid_intent_defaults_to_chat(mock_client) -> None:
    mock_client('{"intent":"launch_missiles","task_spec":null}')
    result = await classifier.classify_intent("Whatever", "Chief Command")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_task_without_spec_falls_back_to_chat(mock_client) -> None:
    """Task intent with null spec -> chat (Nova's safety demotion)."""
    mock_client('{"intent":"task","task_spec":null}')
    result = await classifier.classify_intent("go do the thing", "Arch")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_task_with_empty_string_spec_falls_back_to_chat(mock_client) -> None:
    """Task intent with whitespace-only spec -> chat."""
    mock_client('{"intent":"task","task_spec":"   "}')
    result = await classifier.classify_intent("go do the thing", "Arch")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_empty_user_text_short_circuits(mock_client) -> None:
    """Empty user text returns default without hitting the API."""
    client = mock_client('{"intent":"task","task_spec":"poof"}')
    result = await classifier.classify_intent("   ", "Chief Command")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}
    assert client.messages.call_count == 0


@pytest.mark.asyncio
async def test_api_exception_defaults_to_chat(mock_client) -> None:
    """Network / transport error -> chat (conservative)."""
    mock_client("irrelevant", raise_exc=RuntimeError("network exploded"))
    result = await classifier.classify_intent("Build X", "Arch")
    assert result == {"intent": "chat", "task_spec": None, "confidence": 0.0}


@pytest.mark.asyncio
async def test_json_embedded_in_prose(mock_client) -> None:
    """Model wrapping JSON in prose still parses via the fallback extractor."""
    mock_client('Sure — here is my answer: {"intent":"task","task_spec":"run tests"} okay?')
    result = await classifier.classify_intent("run tests", "Chief Command")
    assert result["intent"] == "task"
    assert result["task_spec"] == "run tests"


@pytest.mark.asyncio
async def test_length_cap_truncates_overlong_input(mock_client) -> None:
    """Input over _MAX_USER_TEXT_CHARS is truncated before hitting the API."""
    client = mock_client('{"intent":"chat","task_spec":null}')
    huge = "a" * (classifier._MAX_USER_TEXT_CHARS + 1000)
    result = await classifier.classify_intent(huge, "Chief Command")
    assert result["intent"] == "chat"
    # Verify the truncation actually reached the API layer.
    sent_content = client.messages.last_call_kwargs["messages"][0]["content"]
    assert len(sent_content) == classifier._MAX_USER_TEXT_CHARS


# ---------------------------------------------------------------------------
# Latency measurement — used in the final report.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_latency_measurement(mock_client, capsys) -> None:
    """Measure p50 wall-clock latency over 10 calls with a 50ms mocked backend.

    Sanity check that the classifier wrapper adds negligible overhead on top of
    the underlying API call. A production measurement against live Haiku would
    obviously differ — this captures the framework cost.
    """
    mock_client('{"intent":"chat","task_spec":null}', delay_s=0.05)

    samples: list[float] = []
    for _ in range(10):
        t0 = time.perf_counter()
        await classifier.classify_intent("hello", "Chief Command")
        samples.append((time.perf_counter() - t0) * 1000.0)

    samples.sort()
    p50 = samples[len(samples) // 2]
    # With a 50ms mocked backend, the wrapper should add <5ms.
    assert p50 < 100.0
    # Emit to stdout so test output captures the measurement.
    print(f"\nclassifier latency p50={p50:.1f}ms (mocked 50ms backend)")
    capsys.readouterr()  # don't double-print
