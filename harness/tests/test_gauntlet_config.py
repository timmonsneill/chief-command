"""The gauntlet config must actually load and name real seats.

Sol's #10 gate found three foundation cracks the panel silently rested on: seats.toml
didn't parse (inline // comments), four seats had no family, and the roster named a seat
that doesn't exist. These tests keep all three closed.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dispatch import (  # noqa: E402
    DispatchRefused,
    _strip_slash_comment,
    load_config,
    sync_seats,
    unresolved_reviewers,
)
from db.jobs import connect, init_db  # noqa: E402


def test_the_real_seats_toml_loads():
    cfg = load_config()   # the actual harness/config/seats.toml — no fixture
    assert cfg["seats"], "seats.toml parsed to no seats"
    assert cfg["gauntlet"]["reviewers"]


def test_every_seat_has_a_family():
    cfg = load_config()
    missing = [sid for sid, s in cfg["seats"].items() if "family" not in s]
    assert missing == [], f"seats with no family: {missing}"


def test_every_gauntlet_reviewer_names_a_real_seat():
    assert unresolved_reviewers(load_config()) == []


def test_inline_slash_comment_stripped_but_urls_survive():
    # The whole reason the naive stripper broke: value strings hold https://.
    assert _strip_slash_comment('base_url = "https://api.x.ai/v1"  // swap me') \
        == 'base_url = "https://api.x.ai/v1"'
    assert _strip_slash_comment('// a whole-line comment') == ''
    assert _strip_slash_comment('model = "grok-4.5"') == 'model = "grok-4.5"'


def test_sync_seats_refuses_a_phantom_reviewer(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    bad = {"seats": {"real": {"provider": "p", "model": "m", "family": "claude", "tier": "t"}},
           "gauntlet": {"reviewers": ["real", "grinder_paid"]}}
    with pytest.raises(DispatchRefused):
        sync_seats(c, bad)


def test_sync_seats_refuses_a_familyless_seat(tmp_path):
    c = connect(tmp_path / "t.db")
    init_db(c)
    bad = {"seats": {"nofam": {"provider": "p", "model": "m", "tier": "t"}},
           "gauntlet": {"reviewers": []}}
    with pytest.raises(DispatchRefused):
        sync_seats(c, bad)
