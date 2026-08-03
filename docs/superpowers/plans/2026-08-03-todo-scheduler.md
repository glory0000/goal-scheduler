# Todo Scheduler Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Claude-driven personal todo scheduler that tracks free time slots, manages multiple goals with task decomposition, and sends Feishu reminders via cc-connect one-shot timers with a daily fallback cron.

**Architecture:** State stored in SQLite (tasks) + Markdown (goal descriptions) + JSON (schedule). Scheduling logic in Python helpers invoked by Claude. Reminders are one-shot cc-connect timers chained together (each creates the next). A single daily 00:05 cron rebuilds broken chains. Git auto-commits after every write.

**Tech Stack:** Python 3.11+, SQLite (stdlib `sqlite3`), Bash, cc-connect CLI, Git, Markdown.

**Spec:** `docs/superpowers/specs/2026-08-03-todo-scheduler-design.md`

## Global Constraints

- All file writes followed by `git add -A && git commit -m "<message>"`.
- Task IDs are per-goal: `<slug>-T001`, `<slug>-T002`, etc. (composed of slug + dash + sequence).
- Slot times are 24-hour `HH:MM` strings. Today's date is `YYYY-MM-DD`.
- Slot computation respects `config/schedule.json` (weekday vs weekend).
- One task fills exactly one slot (task-level granularity, no pomodoro).
- All Python scripts executable: `chmod +x scripts/*.py scripts/*.sh`.
- Python uses only stdlib (no external deps).
- All timestamps in UTC ISO format (`YYYY-MM-DDTHH:MM:SS`).
- Backups kept in `backups/`, rolling last 5.

---

## Task 1: Project skeleton + README + .gitignore

**Files:**
- Create: `README.md`
- Create: `.gitignore`
- Create: `goals/.gitkeep`, `data/.gitkeep`, `backups/.gitkeep`, `logs/.gitkeep`, `tests/.gitkeep`

- [ ] **Step 1: Create directory skeleton**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
mkdir -p goals config data scripts logs backups tests
touch goals/.gitkeep data/.gitkeep backups/.gitkeep logs/.gitkeep tests/.gitkeep
ls -la
```
Expected: All directories exist, .gitkeep files visible.

- [ ] **Step 2: Write `.gitignore`**

```gitignore
# Python
__pycache__/
*.pyc
*.pyo
.pytest_cache/

# SQLite
data/*.db-journal
data/*.db-wal
data/*.db-shm
data/*.db.bak

# Backups (kept under version control per spec)
# Actually, do NOT gitignore backups/ since we want them tracked

# Logs
logs/*.log

# OS
.DS_Store
Thumbs.db

# IDE
.vscode/
.idea/
```

- [ ] **Step 3: Write `README.md`**

```markdown
# Todo Scheduler

Claude-driven personal todo scheduler.

## What it does

- Tracks your free time slots (weekday + weekend).
- Manages multiple goals with task decomposition.
- Sends Feishu reminders at each free time slot.
- Re-schedules dynamically after task completion, focus changes, or goal updates.
- Survives session crashes via a daily rebuild cron.

## Quick start

1. Run setup (see `docs/superpowers/specs/...` Initialization section).
2. Add your first goal via Feishu: tell Claude what you want to do.
3. Claude will brainstorm the task breakdown, write it to `goals/<slug>/goal.md` and SQLite.
4. Set your focus: "今日重点 = a-stock-quant".
5. Claude creates the first timer. Each reminder fires the next one.

## Key files

- `goals/<slug>/goal.md` — goal description + progress stats.
- `data/todos.db` — SQLite: goals, tasks, settings.
- `config/schedule.json` — your free time slots.
- `scripts/db.py`, `scheduler.py`, `reminder.py` — Python helpers.
- `logs/cc-connect.log` — cc-connect command history.

## Common commands

```bash
# Dump current state
bash scripts/dump_state.sh

# Simulate a reminder at a given time
bash scripts/simulate_reminder.sh "2026-08-04 21:00"

# Re-run fallback rebuild manually
bash scripts/dump_state.sh
```

## See also

- Design spec: `docs/superpowers/specs/2026-08-03-todo-scheduler-design.md`
- Implementation plan: `docs/superpowers/plans/2026-08-03-todo-scheduler.md`
```

- [ ] **Step 4: Commit**

```bash
git add .gitignore README.md goals/.gitkeep data/.gitkeep backups/.gitkeep logs/.gitkeep tests/.gitkeep
git commit -m "Add project skeleton, README, .gitignore"
```
Expected: Commit succeeds with all skeleton files staged.

---

## Task 2: Schedule config + SQLite schema

**Files:**
- Create: `config/schedule.json`
- Create: `data/schema.sql`

- [ ] **Step 1: Write `config/schedule.json`**

```json
{
  "weekday": [
    {"start": "07:30", "end": "09:00", "label": "morning"},
    {"start": "12:00", "end": "13:00", "label": "lunch"},
    {"start": "18:00", "end": "19:00", "label": "evening"},
    {"start": "21:00", "end": "23:00", "label": "night"}
  ],
  "weekend": [
    {"start": "09:30", "end": "13:30", "label": "morning-block"},
    {"start": "14:00", "end": "18:00", "label": "afternoon-block"},
    {"start": "19:00", "end": "23:00", "label": "evening-block"}
  ]
}
```

- [ ] **Step 2: Write `data/schema.sql`**

```sql
CREATE TABLE IF NOT EXISTS goals (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  total_tasks INTEGER DEFAULT 0,
  completed_tasks INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  goal_slug TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  estimated_hours REAL,
  depends_on TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  last_reminded_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (goal_slug) REFERENCES goals(slug)
);

CREATE INDEX IF NOT EXISTS idx_tasks_goal ON tasks(goal_slug);
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);

CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

- [ ] **Step 3: Initialize the database**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
sqlite3 data/todos.db < data/schema.sql
sqlite3 data/todos.db ".tables"
sqlite3 data/todos.db ".schema goals"
```
Expected: Three tables listed (`goals`, `tasks`, `settings`). `goals` schema matches.

- [ ] **Step 4: Commit**

```bash
git add config/schedule.json data/schema.sql data/todos.db
git commit -m "Add schedule config and SQLite schema"
```

---

## Task 3: `scripts/db.py` — DB connection helper + goal CRUD (TDD)

**Files:**
- Create: `tests/test_db.py`
- Create: `scripts/db.py`

**Interfaces:**
- Produces:
  - `DB_PATH = "data/todos.db"`
  - `def get_conn() -> sqlite3.Connection`
  - `def now_iso() -> str`
  - `def create_goal(slug: str, name: str, description: str) -> None`
  - `def get_goal(slug: str) -> dict | None`
  - `def list_goals(status: str | None = None) -> list[dict]`
  - `def update_goal_status(slug: str, status: str) -> None`
  - `def update_goal_counts(slug: str, total: int, completed: int) -> None`

- [ ] **Step 1: Write the failing test**

Create `tests/test_db.py`:

```python
import os
import sys
import tempfile
import pytest

# Use a temp DB for tests
TEST_DB_DIR = tempfile.mkdtemp()
os.environ["TODO_DB_PATH"] = os.path.join(TEST_DB_DIR, "test.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Apply schema to test DB
import sqlite3
with sqlite3.connect(os.environ["TODO_DB_PATH"]) as conn:
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")) as f:
        conn.executescript(f.read())

from db import (
    create_goal, get_goal, list_goals,
    update_goal_status, update_goal_counts, now_iso
)


def test_create_and_get_goal():
    create_goal(
        slug="test-goal",
        name="Test Goal",
        description="Just testing"
    )
    g = get_goal("test-goal")
    assert g is not None
    assert g["slug"] == "test-goal"
    assert g["name"] == "Test Goal"
    assert g["status"] == "active"
    assert g["total_tasks"] == 0
    assert g["completed_tasks"] == 0


def test_list_goals():
    create_goal("g1", "Goal 1", "")
    create_goal("g2", "Goal 2", "")
    update_goal_status("g2", "paused")
    active = list_goals(status="active")
    paused = list_goals(status="paused")
    assert len(active) == 1
    assert active[0]["slug"] == "g1"
    assert len(paused) == 1
    assert paused[0]["slug"] == "g2"


def test_update_goal_status():
    create_goal("g3", "G3", "")
    update_goal_status("g3", "completed")
    g = get_goal("g3")
    assert g["status"] == "completed"


def test_update_goal_counts():
    create_goal("g4", "G4", "")
    update_goal_counts("g4", total=5, completed=2)
    g = get_goal("g4")
    assert g["total_tasks"] == 5
    assert g["completed_tasks"] == 2


def test_now_iso_format():
    ts = now_iso()
    # Should match YYYY-MM-DDTHH:MM:SS
    assert len(ts) == 19
    assert ts[4] == "-"
    assert ts[10] == "T"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_db.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'db'`.

- [ ] **Step 3: Implement `scripts/db.py`**

```python
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
```

- [ ] **Step 4: Make executable and run tests**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
chmod +x scripts/db.py
python -m pytest tests/test_db.py -v
```
Expected: All 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_db.py scripts/db.py
git commit -m "Add db.py: connection helper and goal CRUD"
```

---

## Task 4: `scripts/db.py` — task CRUD (TDD)

**Files:**
- Modify: `tests/test_db.py` (add tests)
- Modify: `scripts/db.py` (add functions)

**Interfaces (new):**
- `def create_task(id: str, goal_slug: str, sequence: int, title: str, description: str, estimated_hours: float, depends_on: list[str]) -> None`
- `def get_task(id: str) -> dict | None`
- `def list_tasks(goal_slug: str | None = None, status: str | None = None) -> list[dict]`
- `def list_eligible_tasks(goal_slug: str | None = None) -> list[dict]` — pending tasks whose deps are all done.
- `def update_task_status(id: str, status: str) -> None`
- `def mark_task_reminded(id: str) -> None`
- `def count_tasks_by_status(goal_slug: str) -> dict[str, int]`

- [ ] **Step 1: Append failing tests to `tests/test_db.py`**

Add to `tests/test_db.py`:

```python
from db import (
    create_task, get_task, list_tasks, list_eligible_tasks,
    update_task_status, mark_task_reminded, count_tasks_by_status,
)


def test_create_and_get_task():
    create_goal("g-tasks", "G Tasks", "")
    create_task(
        id="g-tasks-T001",
        goal_slug="g-tasks",
        sequence=1,
        title="Task 1",
        description="",
        estimated_hours=1.5,
        depends_on=[],
    )
    t = get_task("g-tasks-T001")
    assert t is not None
    assert t["title"] == "Task 1"
    assert t["estimated_hours"] == 1.5
    assert t["status"] == "pending"
    assert t["depends_on"] == []  # parsed back from JSON


def test_list_tasks_by_goal():
    create_goal("g-list", "G List", "")
    create_task("g-list-T001", "g-list", 1, "A", "", 1.0, [])
    create_task("g-list-T002", "g-list", 2, "B", "", 1.0, [])
    tasks = list_tasks(goal_slug="g-list")
    assert len(tasks) == 2
    assert tasks[0]["sequence"] == 1


def test_list_eligible_tasks():
    create_goal("g-elig", "G Elig", "")
    create_task("g-elig-T001", "g-elig", 1, "A", "", 1.0, [])
    create_task("g-elig-T002", "g-elig", 2, "B", "", 1.0, ["g-elig-T001"])
    update_task_status("g-elig-T001", "done")
    eligible = list_eligible_tasks(goal_slug="g-elig")
    assert len(eligible) == 1
    assert eligible[0]["id"] == "g-elig-T002"


def test_update_task_status():
    create_goal("g-st", "G ST", "")
    create_task("g-st-T001", "g-st", 1, "X", "", 1.0, [])
    update_task_status("g-st-T001", "done")
    t = get_task("g-st-T001")
    assert t["status"] == "done"
    assert t["completed_at"] is not None


def test_mark_task_reminded():
    create_goal("g-rem", "G Rem", "")
    create_task("g-rem-T001", "g-rem", 1, "X", "", 1.0, [])
    mark_task_reminded("g-rem-T001")
    t = get_task("g-rem-T001")
    assert t["last_reminded_at"] is not None


def test_count_tasks_by_status():
    create_goal("g-cnt", "G Cnt", "")
    create_task("g-cnt-T001", "g-cnt", 1, "A", "", 1.0, [])
    create_task("g-cnt-T002", "g-cnt", 2, "B", "", 1.0, [])
    create_task("g-cnt-T003", "g-cnt", 3, "C", "", 1.0, [])
    update_task_status("g-cnt-T002", "done")
    counts = count_tasks_by_status("g-cnt")
    assert counts["pending"] == 2
    assert counts["done"] == 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_db.py -v
```
Expected: New task tests FAIL with `ImportError`.

- [ ] **Step 3: Append task CRUD to `scripts/db.py`**

Append to `scripts/db.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_db.py -v
```
Expected: All tests PASS (12 total: 5 goal + 6 task + 1 now_iso).

- [ ] **Step 5: Commit**

```bash
git add tests/test_db.py scripts/db.py
git commit -m "Add db.py: task CRUD with dependency filtering"
```

---

## Task 5: `scripts/db.py` — settings CRUD + goal-md progress updater (TDD)

**Files:**
- Modify: `tests/test_db.py`
- Modify: `scripts/db.py`

**Interfaces (new):**
- `def get_setting(key: str) -> str | None`
- `def set_setting(key: str, value: str) -> None`
- `def get_today_focus() -> str | None`
- `def set_today_focus(slug: str | None) -> None`
- `def recompute_goal_counts(slug: str) -> None` — count tasks, update goals table.
- `def write_goal_md_progress(slug: str) -> None` — refresh the `## 任务进度` block in `goals/<slug>/goal.md`.

- [ ] **Step 1: Append failing tests**

Append to `tests/test_db.py`:

```python
from db import (
    get_setting, set_setting,
    get_today_focus, set_today_focus,
    recompute_goal_counts, write_goal_md_progress,
)
import os


def test_settings_roundtrip():
    set_setting("foo", "bar")
    assert get_setting("foo") == "bar"


def test_today_focus():
    create_goal("focus-1", "F1", "")
    set_today_focus("focus-1")
    assert get_today_focus() == "focus-1"
    set_today_focus(None)
    assert get_today_focus() is None


def test_recompute_goal_counts():
    create_goal("rc", "RC", "")
    create_task("rc-T001", "rc", 1, "A", "", 1.0, [])
    create_task("rc-T002", "rc", 2, "B", "", 1.0, [])
    update_task_status("rc-T001", "done")
    recompute_goal_counts("rc")
    g = get_goal("rc")
    assert g["total_tasks"] == 2
    assert g["completed_tasks"] == 1


def test_write_goal_md_progress(tmp_path, monkeypatch):
    # Redirect cwd
    monkeypatch.chdir(tmp_path)
    (tmp_path / "goals" / "rc2").mkdir(parents=True)
    md = tmp_path / "goals" / "rc2" / "goal.md"
    md.write_text(
        "# 目标：RC2\n\n## 任务进度\n- 总任务数：0\n- 已完成：0\n\n## 备注\n", encoding="utf-8"
    )
    create_goal("rc2", "RC2", "")
    create_task("rc2-T001", "rc2", 1, "A", "", 1.0, [])
    create_task("rc2-T002", "rc2", 2, "B", "", 1.0, [])
    update_task_status("rc2-T002", "done")
    write_goal_md_progress("rc2")
    text = md.read_text(encoding="utf-8")
    assert "总任务数：2" in text
    assert "已完成：1" in text
    assert "完成率：50%" in text
```

- [ ] **Step 2: Run tests to verify new ones fail**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_db.py -v
```
Expected: 4 new tests FAIL with ImportError.

- [ ] **Step 3: Append settings + progress functions to `scripts/db.py`**

Append to `scripts/db.py`:

```python
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
```

- [ ] **Step 4: Run all tests**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_db.py -v
```
Expected: All 16 tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_db.py scripts/db.py
git commit -m "Add db.py: settings, today_focus, goal progress sync"
```

---

## Task 6: `scripts/scheduler.py` — slot & task resolver (TDD)

**Files:**
- Create: `tests/test_scheduler.py`
- Create: `scripts/scheduler.py`

**Interfaces:**
- `def load_schedule() -> dict` — read `config/schedule.json`.
- `def is_weekend(date_str: str) -> bool` — YYYY-MM-DD.
- `def get_slots_for_date(date_str: str) -> list[dict]` — return slots for that date.
- `def get_next_slot_after(date_str: str, time_str: str) -> tuple[str, str] | None` — next slot (date, start_time) after given date+time.
- `def compute_schedule(today_focus: str | None, from_date: str, from_time: str, max_slots: int = 20) -> list[dict]` — return list of `{date, slot_start, slot_end, goal_slug, task_id}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_scheduler.py`:

```python
import os
import sys
import tempfile
import pytest
from datetime import datetime

TEST_DB_DIR = tempfile.mkdtemp()
os.environ["TODO_DB_PATH"] = os.path.join(TEST_DB_DIR, "test.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import sqlite3
with sqlite3.connect(os.environ["TODO_DB_PATH"]) as conn:
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")) as f:
        conn.executescript(f.read())

import db
import scheduler


@pytest.fixture(autouse=True)
def reset_db():
    # Clean tables before each test
    with db.get_conn() as conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM settings")


def test_load_schedule():
    sched = scheduler.load_schedule()
    assert "weekday" in sched
    assert "weekend" in sched
    assert len(sched["weekday"]) == 4
    assert len(sched["weekend"]) == 3


def test_is_weekend():
    # 2026-08-01 is Saturday, 2026-08-03 is Monday
    assert scheduler.is_weekend("2026-08-01") is True
    assert scheduler.is_weekend("2026-08-02") is True
    assert scheduler.is_weekend("2026-08-03") is False


def test_get_slots_for_weekday():
    slots = scheduler.get_slots_for_date("2026-08-03")  # Monday
    assert len(slots) == 4
    assert slots[0]["start"] == "07:30"


def test_get_slots_for_weekend():
    slots = scheduler.get_slots_for_date("2026-08-01")  # Saturday
    assert len(slots) == 3
    assert slots[0]["start"] == "09:30"


def test_get_next_slot_after_morning():
    # Monday 08:00 → next slot is 12:00 same day
    nxt = scheduler.get_next_slot_after("2026-08-03", "08:00")
    assert nxt == ("2026-08-03", "12:00")


def test_get_next_slot_after_last_weekday_slot():
    # Monday 22:30 → no slots today, wrap to next day
    nxt = scheduler.get_next_slot_after("2026-08-03", "22:30")
    # Should be 2026-08-04 (Tuesday) 07:30
    assert nxt == ("2026-08-04", "07:30")


def test_compute_schedule_focus_first():
    db.create_goal("g-a", "GA", "")
    db.create_goal("g-b", "GB", "")
    db.create_task("g-a-T001", "g-a", 1, "A1", "", 1.0, [])
    db.create_task("g-a-T002", "g-a", 2, "A2", "", 1.0, [])
    db.create_task("g-b-T001", "g-b", 1, "B1", "", 1.0, [])

    # Monday morning, focus on g-a
    plan = scheduler.compute_schedule(
        today_focus="g-a", from_date="2026-08-03", from_time="07:30"
    )
    # 4 weekday slots remaining; first two should be g-a tasks
    assert plan[0]["task_id"] == "g-a-T001"
    assert plan[1]["task_id"] == "g-a-T002"
    # Then overflow to g-b
    assert plan[2]["goal_slug"] == "g-b"


def test_compute_schedule_respects_deps():
    db.create_goal("g-d", "GD", "")
    db.create_task("g-d-T001", "g-d", 1, "A", "", 1.0, [])
    db.create_task("g-d-T002", "g-d", 2, "B", "", 1.0, ["g-d-T001"])
    # T001 not done → only T001 (no deps) is eligible
    plan = scheduler.compute_schedule(
        today_focus="g-d", from_date="2026-08-03", from_time="07:30"
    )
    eligible_ids = [p["task_id"] for p in plan]
    assert "g-d-T001" in eligible_ids
    assert "g-d-T002" not in eligible_ids  # blocked by T001


def test_compute_schedule_skips_paused_goals():
    db.create_goal("g-p", "GP", "")
    db.create_goal("g-q", "GQ", "")
    db.create_task("g-p-T001", "g-p", 1, "P1", "", 1.0, [])
    db.create_task("g-q-T001", "g-q", 1, "Q1", "", 1.0, [])
    db.update_goal_status("g-p", "paused")
    plan = scheduler.compute_schedule(
        today_focus="g-p", from_date="2026-08-03", from_time="07:30"
    )
    # g-p is paused/focus but ineligible; should fallback
    assert all(p["goal_slug"] == "g-q" for p in plan)


def test_compute_schedule_no_tasks_returns_empty():
    db.create_goal("g-e", "GE", "")
    plan = scheduler.compute_schedule(
        today_focus="g-e", from_date="2026-08-03", from_time="07:30"
    )
    assert plan == []
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_scheduler.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'scheduler'`.

- [ ] **Step 3: Implement `scripts/scheduler.py`**

```python
#!/usr/bin/env python3
"""Compute scheduling decisions: which task runs in which slot."""

import json
import os
from datetime import date, datetime, timedelta

import db

SCHEDULE_PATH = os.environ.get("TODO_SCHEDULE_PATH", "config/schedule.json")


def load_schedule() -> dict:
    """Load the schedule config from disk."""
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        return json.load(f)


def is_weekend(date_str: str) -> bool:
    """True if the given YYYY-MM-DD is Sat or Sun."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    return dt.weekday() >= 5  # Sat=5, Sun=6


def get_slots_for_date(date_str: str) -> list[dict]:
    """Return the slots applicable to this date."""
    sched = load_schedule()
    key = "weekend" if is_weekend(date_str) else "weekday"
    return sched[key]


def get_next_slot_after(date_str: str, time_str: str) -> tuple[str, str] | None:
    """Find the next (date, start_time) slot strictly after the given moment.

    Returns None if no slot in the next 7 days.
    """
    cur_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    cur_time = datetime.strptime(time_str, "%H:%M").time()

    for offset in range(0, 7):
        check_date = (cur_date + timedelta(days=offset)).strftime("%Y-%m-%d")
        slots = get_slots_for_date(check_date)
        for slot in slots:
            slot_start = datetime.strptime(slot["start"], "%H:%M").time()
            if offset == 0 and slot_start <= cur_time:
                continue
            return (check_date, slot["start"])
    return None


def _slot_duration_hours(slot: dict) -> float:
    s = datetime.strptime(slot["start"], "%H:%M")
    e = datetime.strptime(slot["end"], "%H:%M")
    return (e - s).total_seconds() / 3600


def compute_schedule(
    today_focus: str | None,
    from_date: str,
    from_time: str,
    max_slots: int = 20,
) -> list[dict]:
    """Plan which task fills each upcoming free slot.

    Strategy:
      1. Focus goal's pending eligible tasks first (sequence order).
      2. Overflow to other active goals' eligible tasks, oldest-updated first.
      3. Skip tasks whose `estimated_hours` exceeds slot duration (warn).
      4. Stop when no eligible tasks remain or `max_slots` reached.

    Returns list of `{date, slot_start, slot_end, goal_slug, task_id}`.
    Empty list if nothing to schedule.
    """
    plan: list[dict] = []
    used_task_ids: set[str] = set()
    cur_date, cur_time = from_date, from_time

    active_goals = [g for g in db.list_goals(status="active")]

    for _ in range(max_slots):
        nxt = get_next_slot_after(cur_date, cur_time)
        if nxt is None:
            break
        slot_date, slot_start = nxt
        slot = next(
            s for s in get_slots_for_date(slot_date) if s["start"] == slot_start
        )
        slot_hours = _slot_duration_hours(slot)

        # Pick next task
        candidate = None

        # 1. Focus goal first
        if today_focus:
            focus = db.get_goal(today_focus)
            if focus and focus["status"] == "active":
                elig = [
                    t for t in db.list_eligible_tasks(goal_slug=today_focus)
                    if t["id"] not in used_task_ids
                ]
                elig.sort(key=lambda t: t["sequence"])
                for t in elig:
                    if (t["estimated_hours"] or 0) <= slot_hours:
                        candidate = t
                        break

        # 2. Overflow to other goals (round-robin by oldest updated_at)
        if candidate is None:
            other_goals = sorted(
                [g for g in active_goals if g["slug"] != today_focus],
                key=lambda g: g["updated_at"],
            )
            for g in other_goals:
                elig = [
                    t for t in db.list_eligible_tasks(goal_slug=g["slug"])
                    if t["id"] not in used_task_ids
                ]
                elig.sort(key=lambda t: t["sequence"])
                for t in elig:
                    if (t["estimated_hours"] or 0) <= slot_hours:
                        candidate = t
                        break
                if candidate:
                    break

        if candidate is None:
            # No more eligible tasks; stop planning.
            break

        plan.append({
            "date": slot_date,
            "slot_start": slot_start,
            "slot_end": slot["end"],
            "goal_slug": candidate["goal_slug"],
            "task_id": candidate["id"],
        })
        used_task_ids.add(candidate["id"])
        # Advance cursor to end of this slot (so next iteration finds the slot after)
        cur_date, cur_time = slot_date, slot["end"]

    return plan


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "plan":
        focus = db.get_today_focus()
        now = datetime.now()
        plan = compute_schedule(focus, now.strftime("%Y-%m-%d"), now.strftime("%H:%M"))
        for p in plan:
            print(f"{p['date']} {p['slot_start']}-{p['slot_end']} "
                  f"{p['goal_slug']} {p['task_id']}")
```

- [ ] **Step 4: Make executable and run tests**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
chmod +x scripts/scheduler.py
python -m pytest tests/test_scheduler.py -v
```
Expected: All 9 scheduler tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_scheduler.py scripts/scheduler.py
git commit -m "Add scheduler.py: slot resolution and task planning"
```

---

## Task 7: `scripts/reminder.py` — message formatter (TDD)

**Files:**
- Create: `tests/test_reminder.py`
- Create: `scripts/reminder.py`

**Interfaces:**
- `def format_reminder(date_str: str, slot_start: str, slot_end: str, goal: dict, task: dict) -> str`

- [ ] **Step 1: Write the failing test**

Create `tests/test_reminder.py`:

```python
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import reminder


def test_format_reminder_basic():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器基础架构",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "pending",
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "21:00" in msg
    assert "21:00-23:00" in msg
    assert "A股量化" in msg
    assert "实现数据采集器基础架构" in msg
    assert "2 小时" in msg
    assert "T001 完成了" in msg


def test_format_reminder_with_deps():
    goal = {"slug": "video", "name": "视频剪辑"}
    task = {
        "id": "video-T003",
        "title": "学习 Premiere 转场",
        "estimated_hours": 1.0,
        "depends_on": ["video-T001", "video-T002"],
        "status": "pending",
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="19:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "依赖" in msg
    assert "T001" in msg
    assert "T002" in msg
    assert "✓" in msg
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/test_reminder.py -v
```
Expected: FAIL with `ModuleNotFoundError: No module named 'reminder'`.

- [ ] **Step 3: Implement `scripts/reminder.py`**

```python
#!/usr/bin/env python3
"""Format Feishu reminder messages."""


def format_reminder(
    date_str: str,
    slot_start: str,
    slot_end: str,
    goal: dict,
    task: dict,
) -> str:
    """Build a reminder message following the spec §7 template."""
    hours = task.get("estimated_hours") or 0
    if hours == int(hours):
        hours_str = f"{int(hours)} 小时"
    else:
        hours_str = f"{hours:.1f} 小时"

    deps = task.get("depends_on") or []
    if deps:
        dep_lines = "\n".join(f"  - {d} ✓" for d in deps)
        deps_block = f"📎 依赖：\n{dep_lines}"
    else:
        deps_block = "📎 依赖：无"

    task_short_id = task["id"].split("-")[-1]  # strip slug prefix
    return (
        f"⏰ {slot_start} 时段开始（{slot_start}-{slot_end}）\n"
        f"\n"
        f"📌 目标：{goal['name']}\n"
        f"🎯 任务：{task_short_id} - {task['title']}\n"
        f"⏱️ 预计耗时：{hours_str}\n"
        f"{deps_block}\n"
        f"\n"
        f"完成后请回复 \"{task_short_id} 完成了\"。\n"
        f"如需跳过请回复 \"跳过\"。\n"
        f"如需调整今日重点请回复 \"今日重点 = xxx\"。"
    )


if __name__ == "__main__":
    import sys
    import db

    if len(sys.argv) >= 2 and sys.argv[1] == "preview":
        task_id = sys.argv[2]
        task = db.get_task(task_id)
        if task is None:
            print(f"Task {task_id} not found")
            sys.exit(1)
        goal = db.get_goal(task["goal_slug"])
        print(format_reminder("2026-08-04", "21:00", "23:00", goal, task))
```

- [ ] **Step 4: Make executable and run tests**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
chmod +x scripts/reminder.py
python -m pytest tests/test_reminder.py -v
```
Expected: Both tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_reminder.py scripts/reminder.py
git commit -m "Add reminder.py: Feishu message formatter"
```

---

## Task 8: Test helper scripts

**Files:**
- Create: `scripts/dump_state.sh`
- Create: `scripts/simulate_reminder.sh`
- Create: `scripts/break_session.sh`

- [ ] **Step 1: Write `scripts/dump_state.sh`**

```bash
#!/usr/bin/env bash
# Print current DB + file state for debugging.

set -e
cd "$(dirname "$0")/.."

echo "=== Goals ==="
sqlite3 data/todos.db "SELECT slug, name, status, total_tasks, completed_tasks FROM goals;"

echo ""
echo "=== Tasks ==="
sqlite3 -header -column data/todos.db "SELECT id, goal_slug, sequence, status FROM tasks ORDER BY goal_slug, sequence;"

echo ""
echo "=== Settings ==="
sqlite3 data/todos.db "SELECT key, value FROM settings;"

echo ""
echo "=== Pending cc-connect timers ==="
cc-connect timer list 2>&1 || echo "(cc-connect unavailable)"

echo ""
echo "=== Active cc-connect crons ==="
cc-connect cron list 2>&1 || echo "(cc-connect unavailable)"
```

- [ ] **Step 2: Write `scripts/simulate_reminder.sh`**

```bash
#!/usr/bin/env bash
# Simulate a reminder firing at the given "YYYY-MM-DD HH:MM".
# Prints what message WOULD be sent and what next timer WOULD be created.

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
  echo "Usage: $0 'YYYY-MM-DD HH:MM'"
  exit 1
fi

DATE_STR=$(echo "$1" | awk '{print $1}')
TIME_STR=$(echo "$1" | awk '{print $2}')

FOCUS=$(python -c "import db; print(db.get_today_focus() or '')")
echo "Today focus: $FOCUS"

echo ""
echo "Plan from $1:"
python -c "
import sys
sys.path.insert(0, 'scripts')
import scheduler
plan = scheduler.compute_schedule('$FOCUS', '$DATE_STR', '$TIME_STR', max_slots=5)
for p in plan:
    print(f\"  {p['date']} {p['slot_start']}-{p['slot_end']} {p['goal_slug']} {p['task_id']}\")
if not plan:
    print('  (nothing scheduled)')
"

echo ""
echo "First reminder message preview:"
python -c "
import sys
sys.path.insert(0, 'scripts')
import db, scheduler, reminder

FOCUS = '$FOCUS'
plan = scheduler.compute_schedule(FOCUS, '$DATE_STR', '$TIME_STR', max_slots=1)
if not plan:
    print('  (no task to remind)')
else:
    p = plan[0]
    task = db.get_task(p['task_id'])
    goal = db.get_goal(p['goal_slug'])
    print(reminder.format_reminder(p['date'], p['slot_start'], p['slot_end'], goal, task))
"
```

- [ ] **Step 3: Write `scripts/break_session.sh`**

```bash
#!/usr/bin/env bash
# Kill all pending cc-connect timers to simulate session crash.
# Used to test the daily fallback cron rebuild.

set -e
echo "Listing pending timers..."
cc-connect timer list

echo ""
echo "Deleting all pending timers..."
IDS=$(cc-connect timer list 2>/dev/null | grep -oE 'timer_[a-zA-Z0-9]+' || true)
for id in $IDS; do
  echo "Deleting $id..."
  cc-connect timer del "$id" || true
done

echo ""
echo "Done. Verify with: cc-connect timer list"
```

- [ ] **Step 4: Make executable**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
chmod +x scripts/dump_state.sh scripts/simulate_reminder.sh scripts/break_session.sh
ls -la scripts/
```

- [ ] **Step 5: Smoke-test dump_state.sh**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
bash scripts/dump_state.sh
```
Expected: Prints empty Goals/Tasks/Settings (DB has just been initialized), then cc-connect output (or "(cc-connect unavailable)").

- [ ] **Step 6: Commit**

```bash
git add scripts/dump_state.sh scripts/simulate_reminder.sh scripts/break_session.sh
git commit -m "Add test helper scripts"
```

---

## Task 9: Run all tests one more time

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -m pytest tests/ -v
```
Expected: All 27 tests PASS (16 db + 9 scheduler + 2 reminder).

- [ ] **Step 2: Confirm no stray files**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git status
```
Expected: Clean working tree.

- [ ] **Step 3: Commit any leftover (if pytest cache etc.)**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
echo ".pytest_cache/" >> .gitignore
git add .gitignore
git diff --cached
git commit -m "chore: gitignore pytest cache"
```
(Only if pytest left artifacts. Skip commit if no changes.)

---

## Task 10: Create the daily fallback cron via cc-connect

**Files:** none (cc-connect state, no files)

- [ ] **Step 1: Verify cc-connect CLI is available**

```bash
cc-connect --help 2>&1 | head -20
```
Expected: cc-connect command lists its usage.

- [ ] **Step 2: Create the single fallback cron**

```bash
cd D:/codeSpace/claudecode/stock_data/todos

cc-connect cron add \
  --cron "5 0 * * *" \
  --prompt "Daily fallback for todos scheduler. Read data/todos.db and config/schedule.json. For each remaining free slot today, ensure a cc-connect timer exists pointing at a pending task. Cancel stale timers (pointing at done/skipped tasks or past slots). Commit any DB or file changes. If everything is in order, no commit needed." \
  --desc "Todo scheduler: daily reminder chain rebuild"
```
Expected: cc-connect prints new cron ID.

- [ ] **Step 3: Verify cron list shows exactly 1 entry**

```bash
cc-connect cron list
```
Expected: 1 cron job named "Todo scheduler: daily reminder chain rebuild" with cron `5 0 * * *`.

- [ ] **Step 4: Document cron setup in README**

Append to `README.md`:

```markdown
## Fallback cron

The system relies on a single cc-connect cron to rebuild broken timer chains. Verify with:

```bash
cc-connect cron list
```

Expected: 1 job at `5 0 * * *` (00:05 daily). If missing, recreate:

```bash
cc-connect cron add --cron "5 0 * * *" --prompt "<see commit history>" --desc "Todo scheduler: daily reminder chain rebuild"
```
```

- [ ] **Step 5: Commit README update**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git add README.md
git commit -m "Document fallback cron in README"
```

---

## Task 11: Seed example goal for manual end-to-end test

**Files:**
- Create: `goals/example-goal/goal.md`
- Modify: `data/todos.db` (via script)

- [ ] **Step 1: Write a placeholder goal.md**

Create `goals/example-goal/goal.md`:

```markdown
# 目标：示例目标（手动测试用）

> 创建日期：2026-08-03
> 状态：进行中

## 目标描述
这是一个用于验证整个 todo scheduler 流程的示例目标。

## 任务进度
- 总任务数：0
- 已完成：0
- 进行中：0
- 待办：0
- 完成率：0%

## 备注
可以随时删除。
```

- [ ] **Step 2: Seed the example goal via Python REPL**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
python -c "
import sys
sys.path.insert(0, 'scripts')
import db
db.create_goal('example-goal', '示例目标（手动测试用）', '这是一个用于验证流程的示例目标。')
db.create_task('example-goal-T001', 'example-goal', 1, '子任务 1', '描述', 1.0, [])
db.create_task('example-goal-T002', 'example-goal', 2, '子任务 2', '描述', 1.0, ['example-goal-T001'])
db.recompute_goal_counts('example-goal')
db.write_goal_md_progress('example-goal')
print('Seeded.')
"
```
Expected: Prints "Seeded."

- [ ] **Step 3: Verify DB and md**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
bash scripts/dump_state.sh
cat goals/example-goal/goal.md
```
Expected: Shows the goal + 2 tasks. goal.md "## 任务进度" shows 总任务数：2.

- [ ] **Step 4: Simulate a reminder**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
bash scripts/simulate_reminder.sh "$(date +%Y-%m-%d) 07:30"
```
Expected: Lists example-goal-T001 in the plan and previews a reminder message.

- [ ] **Step 5: Commit**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git add goals/example-goal/goal.md data/todos.db
git commit -m "Seed example goal for manual testing"
```

---

## Task 12: Manual end-to-end test (Claude in Feishu)

**Files:** none (Claude exercises the flows)

- [ ] **Step 1: Add the example goal to `goals/index.md`**

Create `goals/index.md`:

```markdown
# 目标索引

- [示例目标（手动测试用）](example-goal/goal.md) — 状态：进行中 — 完成率 0%
```

- [ ] **Step 2: Commit index**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git add goals/index.md
git commit -m "Add goals index"
```

- [ ] **Step 3: In Feishu, send: "今日重点 = example-goal"**

After this, Claude should (verify in conversation or via `dump_state.sh`):
- Update `settings.today_focus`
- Compute plan via `scheduler.compute_schedule`
- Create cc-connect timers for each planned slot

Verify:
```bash
bash scripts/dump_state.sh
cc-connect timer list
```
Expected: `today_focus` = `example-goal`. Timers exist pointing at the planned slots.

- [ ] **Step 4: In Feishu, send: "T001 完成了"**

After this, Claude should:
- Update task status to `done`
- Recompute goal counts
- Refresh goal.md progress block
- Cancel the existing "next" timer (pointing at T001)
- Create a new timer pointing at T002 (now eligible)

Verify:
```bash
bash scripts/dump_state.sh
cc-connect timer list
```
Expected: T001 status = done. Next timer points at T002.

- [ ] **Step 5: In Feishu, send: "暂停 example-goal"**

After this, Claude should:
- Update goal status to `paused`
- Cancel all pending timers for that goal
- (No replacement timers since paused)

Verify:
```bash
bash scripts/dump_state.sh
cc-connect timer list
```
Expected: example-goal status = paused. No timers.

- [ ] **Step 6: In Feishu, send: "恢复 example-goal"**

After this, Claude should:
- Update goal status back to `active`
- Recompute and recreate timers

Verify:
```bash
bash scripts/dump_state.sh
cc-connect timer list
```
Expected: example-goal status = active. New timers exist.

- [ ] **Step 7: Test fallback cron manually**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
bash scripts/break_session.sh
bash scripts/dump_state.sh
```
Expected: All timers deleted. dump_state shows no pending timers.

Now manually trigger the fallback by sending the cron prompt in Feishu (or wait until 00:05 if testing in production). After trigger, verify:

```bash
cc-connect timer list
```
Expected: Timers restored for today's remaining slots.

- [ ] **Step 8: Final commit**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git status
git add -A
git diff --cached --stat
git commit -m "Manual E2E test verified" --allow-empty
```

---

## Task 13: Shadow period kickoff documentation

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add shadow period section to README**

Append to `README.md`:

```markdown
## Shadow period (1-2 weeks)

Before fully trusting the scheduler, run it in parallel with your manual planning:

1. Each morning, dump state via `bash scripts/dump_state.sh`.
2. Compare Claude's planned schedule with your manual plan.
3. Note discrepancies (Claude missed X, over-allocated Y, etc.).
4. Tweak `config/schedule.json` or scheduling rules in `scheduler.py` as needed.
5. Once 7+ days match consistently, remove the example goal and go live.

## When to engage Claude

Tell Claude any of these via Feishu:

- "新目标：<描述>" — start a new goal (Claude will brainstorm).
- "Txxx 完成了" / "Txxx 进度 50%" — update task status.
- "今日重点 = <slug>" — change focus.
- "跳过 <时段>" / "暂停 <slug>" — skip or pause.
- "<目标> 增加任务：<描述>" — add a task.
- "删除 Txxx" / "改 Txxx 为先做 Tyyy" — modify tasks.
```

- [ ] **Step 2: Commit**

```bash
cd D:/codeSpace/claudecode/stock_data/todos
git add README.md
git commit -m "Document shadow period and Claude engagement"
```

---

## Self-Review

1. **Spec coverage:** ✅ 11 sections covered by 13 tasks.
2. **Placeholder scan:** No TBDs. All code blocks complete.
3. **Type consistency:** `db.get_goal` returns `dict | None` everywhere. `scheduler.compute_schedule` returns `list[dict]` everywhere. `reminder.format_reminder` signature consistent. `task_id` format `slug-Tnnn` consistent.
4. **Spec gaps found and fixed during writing:**
   - Spec said "estimated_hours should not exceed slot duration, warn user" — Task 6 implements this as "skip if exceeds". Documented in scheduler.py docstring.
   - Task 12 step 7 manually triggers fallback prompt in Feishu since cc-connect cron exec isn't available via Bash without the cron firing naturally.

---

## Summary

After completing all 13 tasks, you have:

- ✅ Working SQLite-backed todo scheduler with TDD-tested Python helpers
- ✅ Three CLI scripts: `db.py`, `scheduler.py`, `reminder.py`
- ✅ Three test helper bash scripts
- ✅ 27 passing unit tests
- ✅ One cc-connect cron (fallback) + dynamic cc-connect timers (reminders)
- ✅ Example goal seeded for manual verification
- ✅ Manual end-to-end test executed through Feishu
- ✅ README documenting usage and shadow period

Ready to begin execution.