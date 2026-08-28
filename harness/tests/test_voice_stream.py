"""The voice starts speaking before Chief has finished the whole answer."""

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

    async def say(self, said, deep=False):
        for index, sentence in enumerate(self.sentences):
            yield sentence
            if index < len(self.sentences) - 1:
                await asyncio.sleep(0.05)


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
    conn = connect(db_path)
    before = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    conn.close()

    with client.stream("POST", "/api/voice/ask/stream", json={"said": ""}) as response:
        events = list(_sse_events(response))

    assert events == [("done", {
        "full": "",
        "spoken": "I didn't catch that.",
        "model": server._CHIEF_MODEL,
        "failed": True,
    })]

    conn = connect(db_path)
    after = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]
    conn.close()
    assert after == before
