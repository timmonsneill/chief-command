"""Todos and attachments — the command-center layer that belongs to a PROJECT.

Kept separate from jobs.py because this is planning, not dispatch: a todo is a note
to ourselves, not a unit of work the fleet claims. The one rule that matters here is
that these follow the PROJECT, not the window — that's the whole reason they exist.
"""

from __future__ import annotations

import sqlite3
from typing import Any


# ---------------------------------------------------------------------------
# Todos
# ---------------------------------------------------------------------------
def add_todo(conn, project_id: str, text: str, section: str | None = None,
             owner_only: bool = False) -> int:
    section = (section or "").strip() or None
    pos = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 AS p FROM todos WHERE project_id = ?",
        (project_id,),
    ).fetchone()["p"]
    cur = conn.execute(
        "INSERT INTO todos (project_id, section, text, owner_only, position) "
        "VALUES (?, ?, ?, ?, ?)",
        (project_id, section, text.strip(), int(owner_only), pos),
    )
    return int(cur.lastrowid)


def toggle_todo(conn, todo_id: int) -> None:
    conn.execute(
        "UPDATE todos SET done = 1 - done, "
        "done_at = CASE WHEN done = 0 THEN datetime('now') ELSE NULL END "
        "WHERE id = ?",
        (todo_id,),
    )


def delete_todo(conn, todo_id: int) -> None:
    conn.execute("DELETE FROM todos WHERE id = ?", (todo_id,))


def todos_for(conn, project_id: str) -> list[dict[str, Any]]:
    """A project's todos, grouped into its sections in display order.

    Returns [{section, items:[...]}], sections ordered by their first todo, and
    unfinished items ahead of finished ones within each section so the live list
    stays at the top."""
    rows = [dict(r) for r in conn.execute(
        "SELECT id, section, text, done, owner_only FROM todos "
        "WHERE project_id = ? ORDER BY position, id",
        (project_id,),
    )]
    order: list[str] = []
    buckets: dict[str, list[dict]] = {}
    for r in rows:
        name = r["section"] or "General"
        if name not in buckets:
            buckets[name] = []
            order.append(name)
        buckets[name].append(r)
    out = []
    for name in order:
        items = sorted(buckets[name], key=lambda x: (x["done"], x["id"]))
        out.append({"section": name, "items": items})
    return out


# ---------------------------------------------------------------------------
# Attachments
# ---------------------------------------------------------------------------
def add_attachment(conn, filename: str, stored_path: str, kind: str,
                   *, project_id: str | None = None, job_id: int | None = None,
                   size_bytes: int | None = None) -> int:
    if kind not in ("image", "file"):
        raise ValueError("attachment kind must be 'image' or 'file'")
    cur = conn.execute(
        "INSERT INTO attachments (project_id, job_id, filename, stored_path, kind, size_bytes) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, job_id, filename, stored_path, kind, size_bytes),
    )
    return int(cur.lastrowid)


def attachments_for(conn, project_id: str) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT id, filename, kind, size_bytes, created_at FROM attachments "
        "WHERE project_id = ? ORDER BY id DESC",
        (project_id,),
    )]
