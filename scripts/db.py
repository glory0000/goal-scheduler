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
    """Update a goal's status (active/paused/completed/archived)."""
    if status not in ("active", "paused", "completed", "archived"):
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
    """Pending tasks whose `depends_on` are all done.

    Archived tasks are skipped (Task 2 of CRUD补全 plan). The current
    implementation only queries `status='pending'`, so archived tasks are
    excluded implicitly; the explicit guard documents intent and protects
    against future filter changes.
    """
    candidates = list_tasks(goal_slug=goal_slug, status="pending")
    out = []
    for t in candidates:
        if t["status"] == "archived":
            continue
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
    """Update task status.

    First transition into in_progress stamps started_at via COALESCE.
    The done transition stamps completed_at but does not touch started_at.
    The pending/skipped/archived transitions do not touch started_at or completed_at.
    """
    if status not in ("pending", "in_progress", "done", "skipped", "archived"):
        raise ValueError(f"Invalid status: {status}")
    ts = now_iso()
    with get_conn() as conn:
        if status == "in_progress":
            conn.execute(
                """UPDATE tasks SET status = ?,
                                      started_at = COALESCE(started_at, ?),
                                      updated_at = ?
                   WHERE id = ?""",
                (status, ts, ts, id),
            )
        elif status == "done":
            conn.execute(
                """UPDATE tasks SET status = ?, completed_at = ?, updated_at = ?
                   WHERE id = ?""",
                (status, ts, ts, id),
            )
        else:  # pending or skipped
            conn.execute(
                """UPDATE tasks SET status = ?, updated_at = ?
                   WHERE id = ?""",
                (status, ts, id),
            )


def archive_goal(slug: str) -> bool:
    """Soft-delete a goal by setting status='archived'.

    Returns True if the status changed, False if already archived (no-op).
    Raises ValueError if the goal does not exist.
    """
    current = get_goal(slug)
    if current is None:
        raise ValueError(f"goal '{slug}' does not exist")
    if current["status"] == "archived":
        return False
    update_goal_status(slug, "archived")
    return True


def archive_task(task_id: str) -> bool:
    """Soft-delete a task by setting status='archived'.

    Returns True if the status changed, False if already archived (no-op).
    Raises ValueError if the task does not exist.
    """
    current = get_task(task_id)
    if current is None:
        raise ValueError(f"task '{task_id}' does not exist")
    if current["status"] == "archived":
        return False
    update_task_status(task_id, "archived")
    return True


def restore_goal(slug: str) -> None:
    """Restore an archived goal back to 'active'.

    Raises ValueError if the goal does not exist or is not currently archived.
    """
    current = get_goal(slug)
    if current is None:
        raise ValueError(f"goal '{slug}' does not exist")
    if current["status"] != "archived":
        raise ValueError(f"goal '{slug}' is not archived (status='{current['status']}')")
    update_goal_status(slug, "active")


def restore_task(task_id: str) -> None:
    """Restore an archived task back to 'pending'.

    Raises ValueError if the task does not exist or is not currently archived.
    """
    current = get_task(task_id)
    if current is None:
        raise ValueError(f"task '{task_id}' does not exist")
    if current["status"] != "archived":
        raise ValueError(f"task '{task_id}' is not archived (status='{current['status']}')")
    update_task_status(task_id, "pending")


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


# ============ settings table ============

def get_setting(key: str) -> str | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
        return row["value"] if row else None


def set_setting(key: str, value: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


def get_today_focus() -> str | None:
    return get_setting("today_focus")


def set_today_focus(slug: str | None) -> None:
    if slug is None:
        with get_conn() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'today_focus'")
    else:
        set_setting("today_focus", slug)


# ============ derived stats / progress sync ============

def recompute_goal_counts(slug: str) -> None:
    """Recount a goal's tasks and persist to goals table."""
    counts = count_tasks_by_status(slug)
    total = sum(counts.values())
    completed = counts["done"]
    update_goal_counts(slug, total=total, completed=completed)


def write_goal_md_progress(slug: str) -> None:
    """Rewrite the `## 任务进度` block in goals/<slug>/goal.md."""
    counts = count_tasks_by_status(slug)
    total = sum(counts.values())
    completed = counts["done"]
    in_progress = counts["in_progress"]
    pending = counts["pending"]
    pct = int(round(completed * 100 / total)) if total > 0 else 0

    md_path = os.path.join("goals", slug, "goal.md")
    if not os.path.exists(md_path):
        return

    with open(md_path, encoding="utf-8") as f:
        text = f.read()

    new_block = (
        "## 任务进度\n"
        f"- 总任务数：{total}\n"
        f"- 已完成：{completed}\n"
        f"- 进行中：{in_progress}\n"
        f"- 待办：{pending}\n"
        f"- 完成率：{pct}%\n"
    )

    import re
    if re.search(r"## 任务进度\n", text):
        text = re.sub(
            r"## 任务进度\n.*?(?=\n## |\Z)",
            new_block + "\n",
            text,
            count=1,
            flags=re.DOTALL,
        )
    else:
        # Insert before first `## ` heading or at end
        m = re.search(r"^## ", text, flags=re.MULTILINE)
        if m:
            text = text[: m.start()] + new_block + "\n" + text[m.start():]
        else:
            text = text.rstrip() + "\n\n" + new_block

    with open(md_path, "w", encoding="utf-8") as f:
        f.write(text)


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "init":
        # Run schema
        schema_path = os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
        with open(schema_path) as f:
            with get_conn() as conn:
                conn.executescript(f.read())
        print("DB initialized.")
