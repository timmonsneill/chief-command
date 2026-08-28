"""The voice starts speaking before Chief has finished the whole answer.

Also covers the bugs two cross-family reviews found in the first pass at this: a mouth
that could loop by calling ask_chief on its own follow-up sentence, a dropped phone
connection that could cancel Chief's own turn before he remembered saying anything, a
mid-stream failure that silently threw away a partial answer Neill had already heard,
and a blocking subprocess call sitting directly on the event loop.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402
from db.jobs import Seat, connect, create_job, init_db, upsert_seat  # noqa: E402


class FakeChiefSession:
    def __init__(self):
        self.sentences = ("One thing.", "Another thing.", "Last thing.")
        self.remembered = None

    async def say(self, said, deep=False):
        pieces = []
        for index, sentence in enumerate(self.sentences):
            pieces.append(sentence)
            yield sentence
            if index < len(self.sentences) - 1:
                await asyncio.sleep(0.05)
        # Mirrors chief_live.ChiefSession.remember(): only set once the generator runs
        # all the way to the end. If a turn gets cut off, this stays None — exactly the
        # bug the producer/queue split (see test below) exists to prevent.
        self.remembered = " ".join(pieces)


class FailingFakeChiefSession:
    """Yields one sentence, then blows up — simulates Chief's own call failing partway
    through, after the phone has already heard something."""

    async def say(self, said, deep=False):
        yield "Partial answer."
        raise RuntimeError("boom, simulated mid-stream failure")


@pytest.fixture()
def voice_client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    conn = connect(db_path)
    init_db(conn)
    upsert_seat(conn, Seat("chief", "test", "test-chief", "gpt", "subscription"))
    create_job(conn, "Check the voice stream", builder_seat="chief", origin="voice")
    conn.close()

    fake_session = FakeChiefSession()
    monkeypatch.setattr(server, "DB", db_path)
    monkeypatch.setattr(server, "_SEATS_SYNCED", True)
    monkeypatch.setattr(server, "_live_session", lambda: fake_session)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-real")

    with TestClient(server.app) as client:
        yield client, db_path


def _sse_events(response):
    event = None
    data = []
    for line in response.iter_lines():
        if not line:
            if event is not None:
                yield event, json.loads("\n".join(data))
            event = None
            data = []
        elif line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data.append(line[6:])
    if event is not None:
        yield event, json.loads("\n".join(data))


def _event_count(db_path):
    conn = connect(db_path)
    n = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    conn.close()
    return n


def test_first_sentence_arrives_before_stream_finishes(voice_client):
    client, _ = voice_client
    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        assert response.status_code == 200
        events = _sse_events(response)
        first = next(events)
        assert first == ("sentence", {"text": "One thing."})

        remaining = list(events)

    assert [event for event, _ in remaining] == ["sentence", "sentence", "done"]
    assert [data.get("text") for _, data in remaining[:2]] == [
        "Another thing.", "Last thing.",
    ]


def test_done_event_carries_full_text(voice_client):
    client, _ = voice_client
    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        events = list(_sse_events(response))

    name, done = events[-1]
    assert name == "done"
    assert done["full"] == "One thing. Another thing. Last thing."
    assert done["spoken"] == "One thing. Another thing. Last thing."
    assert done["failed"] is False
    assert done["model"]


def test_events_row_written(voice_client):
    client, db_path = voice_client
    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        list(_sse_events(response))

    conn = connect(db_path)
    rows = conn.execute(
        "SELECT seat_id, lane, detail FROM events ORDER BY id"
    ).fetchall()
    conn.close()

    assert len(rows) == 1
    assert dict(rows[0]) == {
        "seat_id": "chief",
        "lane": "chief",
        "detail": "One thing. Another thing. Last thing.",
    }


def test_empty_said_short_circuits(voice_client):
    client, db_path = voice_client
    before = _event_count(db_path)

    with client.stream("POST", "/api/voice/ask/stream", json={"said": ""}) as response:
        events = list(_sse_events(response))

    assert events == [("done", {
        "full": "",
        "spoken": "I didn't catch that.",
        "model": server._CHIEF_MODEL,
        "failed": True,
    })]

    assert _event_count(db_path) == before


def test_stream_response_headers_disable_buffering(voice_client):
    """A cache or proxy that buffers the response would quietly turn 'streaming' back
    into 'wait for the whole thing' — these headers are what stop that."""
    client, _ = voice_client
    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        assert response.headers.get("cache-control") == "no-cache"
        assert response.headers.get("x-accel-buffering") == "no"
        list(_sse_events(response))  # drain so the background turn finishes cleanly


def test_continuation_prefix_blocked_on_stream_endpoint(voice_client):
    """Chief's own follow-up sentences must never re-enter as if Neill said them —
    checked here on the server, not just in the browser (see AGENTS.md: the browser is
    not the trust boundary)."""
    client, db_path = voice_client
    before = _event_count(db_path)

    with client.stream(
        "POST", "/api/voice/ask/stream",
        json={"said": "(Chief continues:) something Chief already said"},
    ) as response:
        assert response.status_code == 400
        events = list(_sse_events(response))

    assert events == [("done", {
        "spoken": "", "full": "", "model": server._CHIEF_MODEL, "failed": True,
    })]
    assert _event_count(db_path) == before  # never touched Chief, never recorded


def test_continuation_prefix_blocked_on_plain_endpoint(voice_client):
    client, db_path = voice_client
    before = _event_count(db_path)

    resp = client.post(
        "/api/voice/ask", json={"said": "(Chief continues:) something Chief already said"}
    )

    assert resp.status_code == 400
    assert resp.json() == {
        "spoken": "", "full": "", "model": server._CHIEF_MODEL, "failed": True,
    }
    assert _event_count(db_path) == before


def test_mid_stream_exception_keeps_partial_answer(voice_client, monkeypatch):
    """A turn that breaks partway through must not throw away sentences the phone
    already heard — the record (and whatever the browser puts in lastAnswer) has to
    match what Neill actually heard, marked as cut off, not wiped to nothing."""
    client, db_path = voice_client
    monkeypatch.setattr(server, "_live_session", lambda: FailingFakeChiefSession())
    monkeypatch.setattr(server, "_chief_session", "sentinel-should-be-cleared")

    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        events = list(_sse_events(response))

    assert [name for name, _ in events] == ["sentence", "done"]
    assert events[0][1] == {"text": "Partial answer."}

    done = events[-1][1]
    assert done["full"] == "Partial answer. (cut off)"
    assert done["spoken"] == "Partial answer. (cut off)"
    assert done["failed"] is True

    conn = connect(db_path)
    row = conn.execute("SELECT detail FROM events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["detail"] == "Partial answer. (cut off)"

    # A broken turn must drop the wedged session so the next utterance rebuilds clean.
    assert server._chief_session is None


def test_no_api_key_branch_uses_ask_chief_fallback(voice_client, monkeypatch):
    """When there's no live session to carry the thread, the stream endpoint falls back
    to the same subprocess-backed brain the plain endpoint uses, wrapped off the event
    loop (it can block for minutes on a pushed-back turn)."""
    client, db_path = voice_client
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def fake_ask_chief(said, context="", pushed_back=False):
        return {
            "spoken": "Fallback answer.", "full": "Fallback answer, in full.",
            "model": "fallback-model", "failed": False,
        }

    monkeypatch.setattr(server, "ask_chief", fake_ask_chief)

    with client.stream(
        "POST", "/api/voice/ask/stream", json={"said": "what's the status"}
    ) as response:
        events = list(_sse_events(response))

    assert [name for name, _ in events] == ["sentence", "done"]
    assert events[0][1] == {"text": "Fallback answer."}
    assert events[-1][1]["full"] == "Fallback answer, in full."
    assert events[-1][1]["failed"] is False

    conn = connect(db_path)
    row = conn.execute("SELECT detail, model FROM events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["detail"] == "Fallback answer, in full."
    assert row["model"] == "fallback-model"


def test_plain_voice_ask_endpoint_still_works(voice_client):
    """This is what the browser now falls back to when the streaming path never gets a
    single word out (see voice.html) — it has to actually work, not just exist."""
    client, db_path = voice_client
    resp = client.post("/api/voice/ask", json={"said": "what's the status"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["full"] == "One thing. Another thing. Last thing."
    assert body["spoken"] == "One thing. Another thing. Last thing."
    assert body["failed"] is False

    conn = connect(db_path)
    row = conn.execute("SELECT detail FROM events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["detail"] == "One thing. Another thing. Last thing."


def test_producer_finishes_and_remembers_even_if_consumer_stops_reading(voice_client):
    """The core fix for the disconnect bug: session.say() must run to completion, and
    the record must get written, even if nothing is left reading the queue — a
    TestClient can't simulate a dropped socket mid-response, so this drives the
    producer directly and simply stops consuming after one item, exactly like a phone
    that vanished mid-answer would look from the server's side."""
    client, db_path = voice_client
    fake_session = server._live_session()

    async def run():
        queue: "asyncio.Queue" = asyncio.Queue()
        task = asyncio.create_task(
            server._live_voice_producer("status check", False, queue)
        )
        kind, _ = await queue.get()
        assert kind == "sentence"
        # Deliberately stop reading. The producer is not awaited by us again until
        # this — proving it isn't gated on anyone still listening.
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())

    assert fake_session.remembered == "One thing. Another thing. Last thing."

    conn = connect(db_path)
    row = conn.execute("SELECT detail FROM events ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    assert row["detail"] == "One thing. Another thing. Last thing."
