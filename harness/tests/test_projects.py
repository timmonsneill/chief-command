"""The real project list, and a project's readable memory.

The load-bearing behaviour: Chief gets the REAL projects from the table (it stopped
improvising "a little medical records project"), and a project's memory NOTES are
readable — while a memory name can never reach outside the memory folder.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from db.jobs import connect, init_db  # noqa: E402
from db.projects import (  # noqa: E402
    memory_file,
    memory_index,
    projects_context,
    real_projects,
)


@pytest.fixture()
def conn(tmp_path):
    # init_db runs schema.sql, which SEEDS the real projects (chief, jess, arch) so Chief
    # knows them on any fresh machine — these tests verify against that real seed.
    c = connect(tmp_path / "test.db")
    init_db(c)
    return c


def test_the_real_projects_are_seeded_from_schema(conn):
    names = [p["name"] for p in real_projects(conn)]
    assert names == ["Chief Command", "Jess", "Arch (Arch to Freedom EMR)"]


def test_arch_is_walled_off_from_its_code(conn):
    # Decision C: Chief may read Arch's notes, but repo_path must stay NULL so the fleet
    # can never be pointed at its code or patient data.
    arch = next(p for p in real_projects(conn) if p["id"] == "arch")
    assert arch["repo_path"] is None


def test_archived_projects_are_hidden(conn):
    conn.execute("INSERT INTO projects (id, name, archived) VALUES ('old', 'Old', 1)")
    assert "Old" not in [p["name"] for p in real_projects(conn)]


def test_projects_context_is_plain_english_and_names_them(conn):
    ctx = projects_context(conn)
    assert "Chief Command" in ctx
    assert "Arch" in ctx
    assert "the only ones" in ctx  # tells Chief not to invent others
    # No filenames or paths leak into what a model speaks aloud.
    assert ".db" not in ctx and "/" not in ctx


def test_projects_context_empty_when_no_projects(conn):
    conn.execute("DELETE FROM projects")
    assert projects_context(conn) == ""


# --- memory readability -----------------------------------------------------
@pytest.fixture()
def conn_with_memory(conn, tmp_path):
    mem = tmp_path / "arch_memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("# Memory Index\n- important thing")
    (mem / "feedback_rule.md").write_text("the rule is X")
    conn.execute("UPDATE projects SET memory_dir = ? WHERE id = 'arch'", (str(mem),))
    return conn


def test_memory_index_reads_the_curated_file(conn_with_memory):
    idx = memory_index(conn_with_memory, "arch")
    assert idx is not None and "important thing" in idx


def test_memory_index_none_when_no_memory_dir(conn_with_memory):
    assert memory_index(conn_with_memory, "chief") is None  # chief has no memory_dir


def test_a_named_memory_file_reads(conn_with_memory):
    assert memory_file(conn_with_memory, "arch", "feedback_rule.md") == "the rule is X"


def test_memory_name_cannot_escape_the_folder(conn_with_memory):
    # The classic: a memory name that tries to climb out to a real secret.
    assert memory_file(conn_with_memory, "arch", "../../../../etc/passwd") is None
    assert memory_file(conn_with_memory, "arch", "/etc/passwd") is None


def test_missing_memory_file_is_none_not_a_crash(conn_with_memory):
    assert memory_file(conn_with_memory, "arch", "does_not_exist.md") is None


def test_only_markdown_notes_are_served(conn_with_memory, tmp_path):
    # A non-.md file in the folder must NOT be readable — we serve NOTES, not any file.
    root = tmp_path / "arch_memory"
    (root / "secret.env").write_text("OPENAI_KEY=sk-leak")
    assert memory_file(conn_with_memory, "arch", "secret.env") is None
