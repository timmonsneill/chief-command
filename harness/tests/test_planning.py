"""Todos + attachments — the command-center planning layer.

The load-bearing behaviour: todos belong to a PROJECT and group into the owner's
own sections, so his list stops living in a terminal window.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import connect, init_db  # noqa: E402
from db.planning import (  # noqa: E402
    add_attachment,
    add_todo,
    attachments_for,
    delete_todo,
    todos_for,
    toggle_todo,
)


@pytest.fixture()
def conn(tmp_path):
    c = connect(tmp_path / "test.db")
    init_db(c)
    c.execute("INSERT INTO projects (id, name) VALUES ('p1', 'Project One')")
    c.execute("INSERT INTO projects (id, name) VALUES ('p2', 'Project Two')")
    return c


def test_a_todo_belongs_to_its_project_only(conn):
    add_todo(conn, "p1", "only in p1")
    assert len(todos_for(conn, "p1")) == 1
    assert todos_for(conn, "p2") == []


def test_todos_group_into_owner_named_sections_in_order(conn):
    add_todo(conn, "p1", "rotate the key", section="Now")
    add_todo(conn, "p1", "ship it", section="Later")
    add_todo(conn, "p1", "call the bank", section="Now")
    groups = todos_for(conn, "p1")
    assert [g["section"] for g in groups] == ["Now", "Later"]   # first-seen order
    assert [t["text"] for t in groups[0]["items"]] == ["rotate the key", "call the bank"]


def test_a_sectionless_todo_folds_into_general(conn):
    add_todo(conn, "p1", "loose item")
    assert todos_for(conn, "p1")[0]["section"] == "General"


def test_toggle_marks_done_and_back(conn):
    tid = add_todo(conn, "p1", "do the thing")
    toggle_todo(conn, tid)
    assert todos_for(conn, "p1")[0]["items"][0]["done"] == 1
    toggle_todo(conn, tid)
    assert todos_for(conn, "p1")[0]["items"][0]["done"] == 0


def test_done_items_sink_below_open_ones_in_their_section(conn):
    a = add_todo(conn, "p1", "first", section="Now")
    add_todo(conn, "p1", "second", section="Now")
    toggle_todo(conn, a)                       # complete the first
    items = todos_for(conn, "p1")[0]["items"]
    assert items[0]["text"] == "second" and items[-1]["text"] == "first"


def test_owner_only_flag_is_recorded(conn):
    add_todo(conn, "p1", "only Neill can rotate this", owner_only=True)
    assert todos_for(conn, "p1")[0]["items"][0]["owner_only"] == 1


def test_delete_removes_it(conn):
    tid = add_todo(conn, "p1", "temporary")
    delete_todo(conn, tid)
    assert todos_for(conn, "p1") == []


def test_deleting_a_project_takes_its_todos(conn):
    add_todo(conn, "p1", "gone with the project")
    conn.execute("DELETE FROM projects WHERE id = 'p1'")
    assert conn.execute("SELECT COUNT(*) c FROM todos").fetchone()["c"] == 0


# ── Attachments ──────────────────────────────────────────────────────────────
def test_an_attachment_is_pinned_to_its_project(conn):
    add_attachment(conn, "bug.png", "/uploads/1_bug.png", "image",
                   project_id="p1", size_bytes=1234)
    rows = attachments_for(conn, "p1")
    assert len(rows) == 1 and rows[0]["kind"] == "image" and rows[0]["filename"] == "bug.png"
    assert attachments_for(conn, "p2") == []


def test_attachment_kind_is_validated(conn):
    with pytest.raises(ValueError):
        add_attachment(conn, "x.bin", "/uploads/x", "executable", project_id="p1")
