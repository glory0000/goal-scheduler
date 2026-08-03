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


# ============ tasks table ============

def create_task(
    id: str,
    goal_slug: str,
    sequence: int,
    title: str,
    description: str,
    estimated_hours: float,
    depends_on: list[str],
) -> None:
    """Insert a new task in 'pending' status."""
    ts = now_iso()
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO tasks (id, goal_slug, sequence, title, description,
                                  estimated_hours, depends_on, status,
                                  created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?)""",
            (
                id, goal_slug, sequence, title, description,
                estimated_hours, json.dumps(depends_on), ts, ts,
            ),
        )


def get_task(id: str) -> dict | None:
    """Fetch a task by id, or None."""
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (id,)).fetchone()
        if row is None:
            return None
        d = _row_to_dict(row)
        d["depends_on"] = json.loads(d["depends_on"]) if d["depends_on"] else []
        return d


def list_tasks(goal_slug: str | None = None, status: str | None = None) -> list[dict]:
    """List tasks, optionally filtered by goal and/or status."""
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if goal_slug is not None:
        query += " AND goal_slug = ?"
        params.append(goal_slug)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY goal_slug, sequence"
    with get_conn() as conn:
        rows = conn.execute(query, params).fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            d["depends_on"] = json.loads(d["depends_on"]) if d["depends_on"] else []
            out.append(d)
        return out


def list_eligible_tasks(goal_slug: str | None = None) -> list[dict]:
    """Pending tasks whose `depends_on` are all done."""
    candidates = list_tasks(goal_slug=goal_slug, status="pending")
    out = []
    for t in candidates:
        all_done = True
        for dep_id in t["depends_on"]:
            dep = get_task(dep_id)
            if dep is None or dep["status"] != "done":
                all_done = False
                break
        if all_done:
            out.append(t)
    return out


def update_task_status(id: str, status: str) -> None:
    """Update task status. If 'done', also stamp completed_at."""
    if status not in ("pending", "in_progress", "done", "skipped"):
        raise ValueError(f"Invalid status: {status}")
    ts = now_iso()
    completed_at = ts if status == "done" else None
    with get_conn() as conn:
        conn.execute(
            """UPDATE tasks SET status = ?, completed_at = ?, updated_at = ?
               WHERE id = ?""",
            (status, completed_at, ts, id),
        )


def mark_task_reminded(id: str) -> None:
    """Stamp task's last_reminded_at to now."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE tasks SET last_reminded_at = ? WHERE id = ?",
            (now_iso(), id),
        )


def count_tasks_by_status(goal_slug: str) -> dict[str, int]:
    """Return a {status: count} dict for a goal's tasks."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT status, COUNT(*) AS n FROM tasks
               WHERE goal_slug = ? GROUP BY status""",
            (goal_slug,),
        ).fetchall()
        out = {"pending": 0, "in_progress": 0, "done": 0, "skipped": 0}
        for r in rows:
            out[r["status"]] = r["n"]
        return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        # Run schema
        schema_path = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
        with open(schema_path) as f:
            with get_conn() as conn:
                conn.executescript(f.read())
        print("DB initialized.")
