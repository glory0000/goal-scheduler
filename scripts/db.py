#!/usr/bin/env python3
"""SQLite helpers for the todo scheduler."""

import json
import os
import sqlite3
from datetime import datetime, timezone

DB_PATH = os.environ.get("TODO_DB_PATH", "data/todos.db")


def get_conn() -> sqlite3.Connection:
    """Open a SQLite connection with row factory enabled."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_iso() -> str:
    """Return current UTC time in ISO format (YYYY-MM-DDTHH:MM:SS)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {k: row[k] for k in row.keys()}


# ============ goals table ============

def create_goal(slug: str, name: str, description: str) -> None:
    """Insert a new goal in 'active' status."""
    ts = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO goals (slug, name, description, status, total_tasks,
                                  completed_tasks, created_at, updated_at)
               VALUES (?, ?, ?, 'active', 0, 0, ?, ?)""",
            (slug, name, description, ts, ts),
        )


def get_goal(slug: str) -> dict | None:
    """Fetch a goal by slug, or None."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM goals WHERE slug = ?", (slug,)).fetchone()
        return _row_to_dict(row) if row else None


def list_goals(status: str | None = None) -> list[dict]:
    """List goals, optionally filtered by status."""
    with get_conn() as conn:
        if status is None:
            rows = conn.execute("SELECT * FROM goals ORDER BY created_at").fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()
        return [_row_to_dict(r) for r in rows]


def update_goal_status(slug: str, status: str) -> None:
    """Update a goal's status (active/paused/completed)."""
    if status not in ("active", "paused", "completed"):
        raise ValueError(f"Invalid status: {status}")
    with get_conn() as conn:
        conn.execute(
            "UPDATE goals SET status = ?, updated_at = ? WHERE slug = ?",
            (status, now_iso(), slug),
        )


def update_goal_counts(slug: str, total: int, completed: int) -> None:
    """Update a goal's task count stats."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE goals SET total_tasks = ?, completed_tasks = ?,
                                  updated_at = ? WHERE slug = ?""",
            (total, completed, now_iso(), slug),
        )


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        # Run schema
        schema_path = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
        with open(schema_path) as f:
            with get_conn() as conn:
                conn.executescript(f.read())
        print("DB initialized.")
