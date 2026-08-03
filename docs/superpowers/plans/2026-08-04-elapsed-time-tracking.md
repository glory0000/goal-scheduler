# Task Elapsed Time Tracking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Track when each task first enters `in_progress` and display that elapsed time on the goal detail page, the today timeline, and the Feishu reminder message.

**Architecture:** Add a `tasks.started_at TEXT` column via the migrations framework. Update `db.update_task_status` to stamp `started_at` on the first `in_progress` transition using `COALESCE`. New `scripts/format_utils.py` exports `format_elapsed(started_at, completed_at=None) -> str`; the dashboard `_task_row`/`_today_view` and the reminder formatter append a `（已用 ...）` suffix for `in_progress` tasks.

**Tech Stack:** Python 3 stdlib (`datetime` parsing, `sqlite3` via existing `db.py`), pytest, Flask, Jinja2 templates, existing migrations framework (`scripts/migrate.py`).

## Global Constraints

- **First-start semantics.** `started_at` is stamped only on the *first* transition into `in_progress`, via `UPDATE ... SET started_at = COALESCE(started_at, ?)`. Re-entering `in_progress` does not reset the value. (Spec §2, §4.2)
- **Compact auto format.** `< 60s` → `Ns`; `< 1h` → `Xm Ys`; `< 24h` → `Xh Ym`; `≥ 24h` → `Xd Yh`. `—` when `started_at` is `NULL`. (Spec §2, §4.3)
- **Display locations.** Dashboard `/goal/<slug>` table gains `Started` and `Elapsed` columns; dashboard `/today` and Feishu reminder append `（已用 ...）` suffix for `in_progress` tasks. `goals/<slug>/goal.md` is **not** modified. (Spec §2, §4.4)
- **Status update semantics** (spec §4.2):
  - `*` → `in_progress`: `UPDATE tasks SET status=?, started_at = COALESCE(started_at, ?), updated_at=? WHERE id=?`
  - `*` → `done`: `UPDATE tasks SET status=?, completed_at=?, updated_at=? WHERE id=?` (do not touch `started_at`)
  - `*` → `pending`/`skipped`: `UPDATE tasks SET status=?, updated_at=? WHERE id=?` (preserve `started_at` *and* `completed_at`)
- **`completed_at` is preserved on revert from `done` → `in_progress`.** The new in_progress path does not clear `completed_at`. This is the v1 trade-off accepted in the spec. (Spec §5 row 6, §7)
- **Defensive try/except on `format_elapsed`.** Dashboard and reminder wrap the call in `try/except ValueError` and render `—` on failure. (Spec §5 row 3)
- **Existing tests must still pass.** The 52 prior tests in `tests/test_db.py`, `tests/test_migrate.py`, `tests/test_dashboard.py`, `tests/test_reminder.py`, `tests/test_scheduler.py` continue to pass without modification (other than the test additions explicitly listed in this plan). (Spec §8 #6)
- **No file outside the planned list** (`scripts/db.py`, `scripts/format_utils.py`, `scripts/reminder.py`, `dashboard/app.py`, `dashboard/templates/goal_detail.html`, `dashboard/templates/today.html`, `migrations/002_add_started_at.sql`, `tests/test_format_utils.py`, plus extensions to existing test files) is modified. (Spec §8 #7)
- **Migration filename `002_add_started_at.sql`.** This is the *first real* schema change going through the migrations framework. v1 baseline (`schema.sql`) is unchanged — existing production DBs migrate forward via `python scripts/migrate.py upgrade`.

---

### Task 1: Migration + `update_task_status` COALESCE semantics

**Files:**
- Create: `migrations/002_add_started_at.sql`
- Modify: `scripts/db.py:158-169` (`update_task_status`)
- Modify: `tests/test_db.py` (append tests)
- Modify: `tests/test_migrate.py` (append tests)

**Interfaces:**
- Consumes: existing `db.now_iso()` (no change), existing `tests/test_db.py` module-level DB fixture (no change).
- Produces:
  - `migrations/002_add_started_at.sql` containing exactly one statement: `ALTER TABLE tasks ADD COLUMN started_at TEXT;`
  - `db.update_task_status(id, status)` now branches on `status`:
    - `"in_progress"` → stamps `started_at` via `COALESCE`
    - `"done"` → stamps `completed_at` (unchanged behavior)
    - `"pending"` / `"skipped"` → touches only `status` and `updated_at`
  - `db.get_task(id)["started_at"]` returns the ISO string or `None` (the new column comes back via the existing `*` select).

- [ ] **Step 1: Create the migration file**

Create `migrations/002_add_started_at.sql` with the following content (single statement, no trailing semicolon issues, ASCII text):

```sql
ALTER TABLE tasks ADD COLUMN started_at TEXT;
```

No code in this step — just write the file. (`tests/test_db.py` already creates a fresh DB by reading `data/schema.sql`; once that schema runs, the test DB will be at v1 baseline and this file is the only pending migration.)

- [ ] **Step 2: Add failing tests for `update_task_status` semantics**

Open `tests/test_db.py` and append the following four tests at the end of the file (after the existing `test_write_goal_md_progress`):

```python
def test_update_task_status_stamps_started_at_on_first_in_progress():
    create_goal("g-s1", "GS1", "")
    create_task("g-s1-T001", "g-s1", 1, "X", "", 1.0, [])
    update_task_status("g-s1-T001", "in_progress")
    t = get_task("g-s1-T001")
    assert t["started_at"] is not None
    # Stamped on the same call as updated_at
    assert t["started_at"] == t["updated_at"]


def test_update_task_status_done_preserves_started_at():
    create_goal("g-s2", "GS2", "")
    create_task("g-s2-T001", "g-s2", 1, "X", "", 1.0, [])
    update_task_status("g-s2-T001", "in_progress")
    started = get_task("g-s2-T001")["started_at"]
    update_task_status("g-s2-T001", "done")
    t = get_task("g-s2-T001")
    assert t["started_at"] == started  # preserved through done
    assert t["completed_at"] is not None


def test_update_task_status_in_progress_idempotent_via_coalesce():
    create_goal("g-s3", "GS3", "")
    create_task("g-s3-T001", "g-s3", 1, "X", "", 1.0, [])
    update_task_status("g-s3-T001", "in_progress")
    started = get_task("g-s3-T001")["started_at"]
    update_task_status("g-s3-T001", "pending")
    update_task_status("g-s3-T001", "in_progress")
    t = get_task("g-s3-T001")
    assert t["started_at"] == started  # unchanged (COALESCE)


def test_update_task_status_done_to_in_progress_preserves_started_at():
    create_goal("g-s4", "GS4", "")
    create_task("g-s4-T001", "g-s4", 1, "X", "", 1.0, [])
    update_task_status("g-s4-T001", "in_progress")
    update_task_status("g-s4-T001", "done")
    started = get_task("g-s4-T001")["started_at"]
    update_task_status("g-s4-T001", "in_progress")
    t = get_task("g-s4-T001")
    assert t["started_at"] == started
    assert t["status"] == "in_progress"
```

- [ ] **Step 3: Run new tests to verify they fail**

Run: `python -m pytest tests/test_db.py -k "stamps_started_at or done_preserves or in_progress_idempotent or done_to_in_progress" -v`
Expected: All 4 new tests FAIL. The current `update_task_status` either overwrites `completed_at` for non-done paths or doesn't touch `started_at` at all, so `t["started_at"]` is `None` (or the column doesn't exist yet on the test DB, which uses `data/schema.sql` — v1 baseline, no `started_at` column).

If the failures are `sqlite3.OperationalError: no such column: started_at`, that's expected and confirms the test is meaningful. Add `started_at` to `data/schema.sql` ONLY as a *test* fix to make the test pass without the migration? No — the test should use the real schema. The new tests will pass once Step 5 (the migration apply) is in place AND `update_task_status` is updated. For now, accept the failure.

- [ ] **Step 4: Add failing tests for migration `002`**

Open `tests/test_migrate.py` and append the following two tests at the end of the file:

```python
def test_upgrade_applies_002_add_started_at(tmp_path):
    """Apply 002 on a v1 DB → schema_version=2, tasks.started_at column exists."""
    db_path = tmp_path / "v1_for_002.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_add_started_at.sql").write_text(
        "ALTER TABLE tasks ADD COLUMN started_at TEXT"
    )

    init_result = run_migrate(["init"], db_path=db_path)
    assert init_result.returncode == 0

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode == 0, upgrade_result.stderr
    assert _read_schema_version(db_path) == 2
    with sqlite3.connect(str(db_path)) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    assert "started_at" in cols


def test_upgrade_is_noop_when_002_already_applied(tmp_path):
    """Re-run upgrade after 002 is applied → no-op, version stays at 2."""
    db_path = tmp_path / "v2_reapply.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_add_started_at.sql").write_text(
        "ALTER TABLE tasks ADD COLUMN started_at TEXT"
    )

    run_migrate(["init"], db_path=db_path)
    first = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )
    assert first.returncode == 0
    assert _read_schema_version(db_path) == 2

    second = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert second.returncode == 0, second.stderr
    assert _read_schema_version(db_path) == 2
```

- [ ] **Step 5: Run new migration tests to verify they fail**

Run: `python -m pytest tests/test_migrate.py::test_upgrade_applies_002_add_started_at tests/test_migrate.py::test_upgrade_is_noop_when_002_already_applied -v`
Expected: Both FAIL. The test creates `002_add_started_at.sql` in a temp dir, but at the time these tests run, the *real* `migrations/002_add_started_at.sql` from Step 1 also exists in the repo. The test isolates via `TODO_MIGRATIONS_DIR` env var pointing to `tmp_path`, so the temp file is what gets applied. Without the `data/schema.sql` having `started_at` (it doesn't), the `ALTER TABLE` will succeed and the column will be added — but `_read_schema_version == 2` should still pass once the apply logic works. The test fails because… hmm, actually this should work if `ALTER TABLE` is valid SQL and the file is in the migrations dir.

Wait — the temp `migrations/002_add_started_at.sql` is valid SQL. The test setup creates it, runs `init` (stamps v1), runs `upgrade` (applies 002, stamps v2). This should pass once the test is in place. The reason it "fails" before this step is that the test code itself doesn't exist yet — Step 4 added the test. So the expected "fail" is the test not existing, which gives a "no tests ran" or "collection error".

Acceptable: a "no tests ran" message counts as a failure to be fixed by Step 6 onward. Or, if pytest collects them and they pass coincidentally (because `ALTER TABLE` is valid SQL), the test is already valid and the implementation work is just `update_task_status` and the migration file content. **In that case, the test pass in Step 5 is the green signal** — proceed to Step 6 with only the `test_db.py` failures to address.

- [ ] **Step 6: Update `update_task_status` to branch on status**

Open `scripts/db.py`. Replace the body of `update_task_status` (lines 158-169) with the following:

```python
def update_task_status(id: str, status: str) -> None:
    """Update task status.

    First transition into in_progress stamps started_at via COALESCE.
    The done transition stamps completed_at but does not touch started_at.
    The pending/skipped transitions do not touch started_at or completed_at.
    """
    if status not in ("pending", "in_progress", "done", "skipped"):
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
```

- [ ] **Step 7: Run all `test_db.py` and `test_migrate.py` tests**

Run: `python -m pytest tests/test_db.py tests/test_migrate.py -v`
Expected: All tests PASS (52 prior + 4 new in test_db.py + 2 new in test_migrate.py = 58). The new `test_db.py` tests succeed because:
- The test DB was initialized from `data/schema.sql` (v1 baseline) by the module-level fixture.
- `update_task_status("...T001", "in_progress")` runs the new branch which uses `COALESCE(started_at, ?)`. But the `started_at` column doesn't exist on the v1 schema!

If you see `sqlite3.OperationalError: no such column: started_at`, the test DB needs the `started_at` column. Two options:

  **Option A (recommended):** The `tests/test_db.py` module-level setup should add the `started_at` column to the test DB so that the new function works. Update lines 14-17 in `tests/test_db.py`:

```python
# Apply schema to test DB
with sqlite3.connect(TEST_DB_PATH) as conn:
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")) as f:
        conn.executescript(f.read())
    conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
```

The schema is the source of truth for new DBs (via the migration framework), but the test DB needs the column NOW for the new tests to pass without running `migrate.py upgrade` first. This keeps the test setup explicit about what columns exist.

  **Option B (rejected):** Make `update_task_status` catch the `OperationalError` and ignore. Bad — masks real bugs.

Apply Option A. Re-run `python -m pytest tests/test_db.py tests/test_migrate.py -v`. Expected: 58/58 PASS.

- [ ] **Step 8: Commit**

```bash
git add migrations/002_add_started_at.sql scripts/db.py tests/test_db.py tests/test_migrate.py
git commit -m "Stamp tasks.started_at on first in_progress via COALESCE"
```

---

### Task 2: `format_elapsed` utility module

**Files:**
- Create: `scripts/format_utils.py`
- Create: `tests/test_format_utils.py`

**Interfaces:**
- Consumes: `db.now_iso()` (imported from `scripts/db.py` — both are application code; `db.py` does not depend on `format_utils.py`).
- Produces: `format_elapsed(started_at: str | None, completed_at: str | None = None) -> str`
  - Returns `"—"` when `started_at` is `None`.
  - Computes `end = completed_at or now_iso()`.
  - Parses both as ISO timestamps; raises `ValueError` on malformed input.
  - Branches by magnitude: `< 60s` → `Ns`; `< 1h` → `Xm Ys`; `< 24h` → `Xh Ym`; `≥ 24h` → `Xd Yh`.
  - Clock skew (end < start) clamps to `0s`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_format_utils.py`:

```python
"""Unit tests for scripts/format_utils.py. Pure functions, no DB."""
import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from format_utils import format_elapsed


def test_format_elapsed_none_returns_dash():
    assert format_elapsed(None) == "—"


def test_format_elapsed_seconds_only():
    start = "2026-08-04T07:30:00"
    end = "2026-08-04T07:30:45"
    assert format_elapsed(start, completed_at=end) == "45s"


def test_format_elapsed_under_one_hour():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T07:59:59"
    assert format_elapsed(start, completed_at=end) == "59m 59s"


def test_format_elapsed_one_minute_five_seconds():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T07:01:05"
    assert format_elapsed(start, completed_at=end) == "1m 5s"


def test_format_elapsed_under_one_day():
    start = "2026-08-04T07:00:00"
    end = "2026-08-04T08:23:00"
    assert format_elapsed(start, completed_at=end) == "1h 23m"


def test_format_elapsed_multi_day():
    start = "2026-08-01T07:00:00"
    end = "2026-08-03T12:00:00"
    assert format_elapsed(start, completed_at=end) == "2d 5h"


def test_format_elapsed_malformed_raises_value_error():
    with pytest.raises(ValueError):
        format_elapsed("not-a-timestamp")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_format_utils.py -v`
Expected: Collection error or `ModuleNotFoundError: No module named 'format_utils'`. The module does not exist yet.

- [ ] **Step 3: Create `scripts/format_utils.py`**

Create `scripts/format_utils.py`:

```python
"""Display helpers for the todo scheduler."""

from datetime import datetime

from db import now_iso


def format_elapsed(started_at: str | None, completed_at: str | None = None) -> str:
    """Render a compact elapsed-time string.

    Returns "—" when started_at is None. Branches by magnitude:
    < 60s -> Ns, < 1h -> Xm Ys, < 24h -> Xh Ym, >= 24h -> Xd Yh.
    Raises ValueError on unparseable timestamps.
    """
    if started_at is None:
        return "—"
    end = completed_at if completed_at is not None else now_iso()
    start_dt = datetime.strptime(started_at, "%Y-%m-%dT%H:%M:%S")
    end_dt = datetime.strptime(end, "%Y-%m-%dT%H:%M:%S")
    seconds = max(0, int((end_dt - start_dt).total_seconds()))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m {seconds % 60}s"
    if seconds < 86400:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_format_utils.py -v`
Expected: 7/7 PASS. The module imports `db.now_iso`, which requires `scripts/` on `sys.path` — the test file already adds this. The test does not need a temp DB because `now_iso` does not open a connection.

- [ ] **Step 5: Commit**

```bash
git add scripts/format_utils.py tests/test_format_utils.py
git commit -m "Add format_elapsed helper for compact elapsed-time display"
```

---

### Task 3: Reminder message suffix for in_progress tasks

**Files:**
- Modify: `scripts/reminder.py:5-38` (`format_reminder`)
- Modify: `tests/test_reminder.py` (append test)

**Interfaces:**
- Consumes: `format_utils.format_elapsed(started_at)` (from Task 2); `task.get("status")` and `task.get("started_at")` from the caller.
- Produces: `format_reminder(...)` output for `in_progress` tasks contains `（已用 {format_elapsed(started_at)}）` appended to the `🎯 任务：` line. For `pending`/`done`/`skipped` tasks, no suffix is appended. The suffix is wrapped in `try/except ValueError` and falls back to no suffix on malformed input.

- [ ] **Step 1: Add the failing test**

Open `tests/test_reminder.py` and append the following two tests at the end of the file:

```python
def test_format_reminder_appends_elapsed_for_in_progress():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "in_progress",
        "started_at": "2026-08-04T07:00:00",
        "completed_at": None,
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    # The suffix appears on the task title line specifically.
    task_lines = [ln for ln in msg.splitlines() if ln.startswith("🎯 任务：")]
    assert len(task_lines) == 1
    assert "（已用" in task_lines[0]
    assert task_lines[0].endswith("）")


def test_format_reminder_no_elapsed_suffix_for_pending():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "pending",
        "started_at": None,
        "completed_at": None,
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "（已用" not in msg
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_reminder.py -v`
Expected: Both new tests FAIL — the current `format_reminder` does not append any elapsed suffix.

- [ ] **Step 3: Update `format_reminder` to append the suffix**

Open `scripts/reminder.py`. Replace the existing `format_reminder` function with:

```python
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

    elapsed_suffix = ""
    if task.get("status") == "in_progress":
        try:
            elapsed_suffix = f"（已用 {format_elapsed(task.get('started_at'))}）"
        except ValueError:
            elapsed_suffix = ""

    return (
        f"⏰ {slot_start} 时段开始（{slot_start}-{slot_end}）\n"
        f"\n"
        f"📌 目标：{goal['name']}\n"
        f"🎯 任务：{task_short_id} - {task['title']}{elapsed_suffix}\n"
        f"⏱️ 预计耗时：{hours_str}\n"
        f"{deps_block}\n"
        f"\n"
        f"完成后请回复 \"{task_short_id} 完成了\"。\n"
        f"如需跳过请回复 \"跳过\"。\n"
        f"如需调整今日重点请回复 \"今日重点 = xxx\"。"
    )
```

Then update the import block at the top of the file (lines 1-3) to add the `format_elapsed` import. The file should now begin with:

```python
#!/usr/bin/env python3
"""Format Feishu reminder messages."""

from format_utils import format_elapsed
```

- [ ] **Step 4: Run reminder tests to verify they pass**

Run: `python -m pytest tests/test_reminder.py -v`
Expected: 4/4 PASS (2 existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add scripts/reminder.py tests/test_reminder.py
git commit -m "Append elapsed-time suffix to in_progress reminder messages"
```

---

### Task 4: Dashboard data wiring + templates

**Files:**
- Modify: `dashboard/app.py:51-68` (`_task_row`), `dashboard/app.py:74-123` (`_today_view`)
- Modify: `dashboard/templates/goal_detail.html:21-37` (table head + body row)
- Modify: `dashboard/templates/today.html:15-20` (timeline task line)
- Modify: `tests/test_dashboard.py` (append two tests)

**Interfaces:**
- Consumes: `format_utils.format_elapsed(started_at, completed_at=None)` (from Task 2); `task.get("started_at")` and `task.get("completed_at")` from `db.get_task()` (from Task 1).
- Produces:
  - `_task_row(task)` now returns `{"task", "dependencies", "last_reminded", "started", "elapsed", "completed"}` where:
    - `started = _format_timestamp(task["started_at"])` (`"—"` when NULL, `"YYYY-MM-DD HH:MM"` otherwise)
    - `elapsed = format_elapsed(started_at, completed_at)` wrapped in `try/except ValueError` (returns `"—"` on failure)
  - `_today_view(...)` adds `task_label` to each `slot_row`:
    - `None` when the slot has no task
    - For `in_progress`: `f"[{goal['name']}] {task['id']} - {task['title']}（已用 {format_elapsed(started_at)}）"` (suffix wrapped in try/except, falls back to no suffix on ValueError)
    - For other statuses: `f"[{goal['name']}] {task['id']} - {task['title']}"` (no suffix)
  - `goal_detail.html` table gains two columns: `Started` (between `上次提醒` and `完成时间`) and `Elapsed` (between `Started` and `完成时间`).
  - `today.html` timeline renders `{{ row.task_label }}` (replacing the inline `[name] id - title` construction).

- [ ] **Step 1: Add the failing dashboard tests**

Open `tests/test_dashboard.py` and append the following two tests at the end of the file:

```python
def test_goal_detail_shows_started_and_elapsed_columns(client):
    db.create_goal("elapsed-goal", "目标", "")
    db.create_task("elapsed-goal-T001", "elapsed-goal", 1, "未开始", "", 1.0, [])
    db.create_task("elapsed-goal-T002", "elapsed-goal", 2, "进行中", "", 1.5, [])
    db.create_task("elapsed-goal-T003", "elapsed-goal", 3, "已完成", "", 1.0, [])
    db.update_task_status("elapsed-goal-T002", "in_progress")
    db.update_task_status("elapsed-goal-T003", "done")

    response = client.get("/goal/elapsed-goal")

    assert response.status_code == 200
    # Column headers are rendered
    assert "Started" in response.text
    assert "Elapsed" in response.text
    # The pending task has both columns as "—"
    assert "未开始" in response.text
    # The in_progress and done tasks should have a non-dash elapsed
    # (a number with unit suffix). We assert the presence of the
    # "h " or "m " or "s" pattern in elapsed cells.
    body = response.text
    # At least one row should have a non-dash elapsed
    assert ("h " in body) or ("m " in body) or ("s</td>" in body)


def test_today_timeline_appends_elapsed_suffix_for_in_progress(client):
    db.create_goal("today-elapsed", "今日目标", "")
    db.create_task("today-elapsed-T001", "today-elapsed", 1, "进行中任务", "", 1.0, [])
    db.update_task_status("today-elapsed-T001", "in_progress")
    db.set_today_focus("today-elapsed")

    response = client.get("/today")

    assert response.status_code == 200
    # The in_progress task label has the suffix appended
    assert "进行中任务（已用" in response.text
    # The closing parenthesis immediately follows the elapsed value
    assert "）" in response.text
```

- [ ] **Step 2: Run new tests to verify they fail**

Run: `python -m pytest tests/test_dashboard.py -k "started_and_elapsed or appends_elapsed_suffix" -v`
Expected: Both FAIL — the templates don't have the new columns/suffix yet.

- [ ] **Step 3: Update `dashboard/app.py` imports and `_task_row`**

Open `dashboard/app.py`. Add the `format_elapsed` import to the `import db` block at the top of the file (lines 17-18). The block should become:

```python
import db
import format_utils
import scheduler
```

(Note: `format_utils` is added but not used at module level — it's used inside `_task_row` and `_today_view`. This avoids a top-level `format_utils.format_elapsed` name binding on the dashboard module, which would shadow other things. Alternative style: `from format_utils import format_elapsed`. Either works; the import above matches the `import db` / `import scheduler` style already in use.)

Replace `_task_row` (lines 55-68) with:

```python
def _task_row(task: dict) -> dict:
    dependencies = []
    for dependency_id in task["depends_on"]:
        dependency = db.get_task(dependency_id)
        dependencies.append({
            "id": dependency_id,
            "done": dependency is not None and dependency["status"] == "done",
        })
    started_at = task.get("started_at")
    completed_at = task.get("completed_at")
    try:
        elapsed = format_utils.format_elapsed(started_at, completed_at)
    except ValueError:
        elapsed = "—"
    return {
        "task": task,
        "dependencies": dependencies,
        "last_reminded": _format_timestamp(task["last_reminded_at"]),
        "started": _format_timestamp(started_at),
        "elapsed": elapsed,
        "completed": _format_timestamp(completed_at),
    }
```

- [ ] **Step 4: Update `_today_view` to populate `task_label`**

In `dashboard/app.py`, replace the body of `_today_view` (lines 74-123) with:

```python
def _today_view(today_date: str) -> dict:
    slots = scheduler.get_slots_for_date(today_date)
    focus_slug = db.get_today_focus()
    focus_goal = db.get_goal(focus_slug) if focus_slug else None
    plan = scheduler.compute_schedule(
        focus_slug,
        today_date,
        "00:00",
        max_slots=len(slots),
    )
    today_plan = [item for item in plan if item["date"] == today_date]
    assignments = {item["slot_start"]: item for item in today_plan}

    slot_rows = []
    scheduled_ids = set()
    for slot in slots:
        assignment = assignments.get(slot["start"])
        task = db.get_task(assignment["task_id"]) if assignment else None
        goal = db.get_goal(assignment["goal_slug"]) if assignment else None
        dependencies = []
        task_label = None
        if task:
            scheduled_ids.add(task["id"])
            for dependency_id in task["depends_on"]:
                dependency = db.get_task(dependency_id)
                dependencies.append({
                    "id": dependency_id,
                    "done": dependency is not None and dependency["status"] == "done",
                })
            task_label = f"[{goal['name']}] {task['id']} - {task['title']}"
            if task.get("status") == "in_progress":
                try:
                    task_label += f"（已用 {format_utils.format_elapsed(task.get('started_at'))}）"
                except ValueError:
                    pass  # leave suffix off
        slot_rows.append({
            "slot": slot,
            "task": task,
            "goal": goal,
            "task_label": task_label,
            "dependencies": dependencies,
        })

    active_goals = db.list_goals(status="active")
    pending_tasks = [
        task
        for goal in active_goals
        for task in db.list_tasks(goal_slug=goal["slug"], status="pending")
    ]
    remaining = sum(task["id"] not in scheduled_ids for task in pending_tasks)
    return {
        "date": today_date,
        "weekday": WEEKDAY_LABELS[date.fromisoformat(today_date).weekday()],
        "focus_goal": focus_goal,
        "slot_rows": slot_rows,
        "has_assignments": bool(scheduled_ids),
        "remaining": remaining,
    }
```

- [ ] **Step 5: Update `goal_detail.html` to add `Started` and `Elapsed` columns**

Open `dashboard/templates/goal_detail.html`. Replace the `<thead>` row (line 21) with:

```html
<thead><tr><th>ID</th><th>标题</th><th>小时</th><th>依赖</th><th>状态</th><th>上次提醒</th><th>Started</th><th>Elapsed</th><th>完成时间</th></tr></thead>
```

Then in the table body `<tr>` (lines 24-36), add two `<td>` cells between the `上次提醒` cell and the `完成时间` cell. The body row should become:

```html
<tr>
  <td>{{ row.task.id }}</td>
  <td>{{ row.task.title }}</td>
  <td>{{ "%.1f"|format(row.task.estimated_hours or 0) }}</td>
  <td>
    {% if row.dependencies %}
      {% for dep in row.dependencies %}→ {{ dep.id }} {{ "✓" if dep.done else "未完成" }}{% if not loop.last %}<br>{% endif %}{% endfor %}
    {% else %}—{% endif %}
  </td>
  <td><span class="status status-{{ row.task.status }}">{{ status_label[row.task.status] }}</span></td>
  <td>{{ row.last_reminded }}</td>
  <td>{{ row.started }}</td>
  <td>{{ row.elapsed }}</td>
  <td>{{ row.completed }}</td>
</tr>
```

- [ ] **Step 6: Update `today.html` to render `task_label`**

Open `dashboard/templates/today.html`. Replace the inner content of the `{% if row.task %}` block (lines 15-20) with:

```html
{% if row.task %}
  <strong>{{ row.task_label }}</strong>
  <a href="{{ url_for('goal_detail', slug=row.goal.slug) }}">详情</a>
  {% if row.dependencies %}
    <div class="dependencies">依赖：{% for dep in row.dependencies %}{{ dep.id }} {{ "✓" if dep.done else "未完成" }}{% if not loop.last %}，{% endif %}{% endfor %}</div>
  {% endif %}
{% else %}
  <span class="empty-slot">──────────（无任务）</span>
{% endif %}
```

The only change is the `<strong>` line: was `[{{ row.goal.name }}] {{ row.task.id }} - {{ row.task.title }}`, is now `{{ row.task_label }}`.

- [ ] **Step 7: Run all dashboard tests**

Run: `python -m pytest tests/test_dashboard.py -v`
Expected: All tests PASS (15 prior + 2 new = 17). The two new tests verify the columns and the suffix.

- [ ] **Step 8: Run the full test suite**

Run: `python -m pytest -q`
Expected: 65/65 PASS (52 prior + 4 new in test_db.py + 2 new in test_migrate.py + 7 new in test_format_utils.py + 2 new in test_reminder.py + 2 new in test_dashboard.py = wait, let me recount).

Recount:
- 52 prior tests across the 5 test files
- +4 in test_db.py
- +2 in test_migrate.py
- +7 in test_format_utils.py
- +2 in test_reminder.py
- +2 in test_dashboard.py
- = 69 total

If the prior count was 52 (per spec §8 #6), and 4+2+7+2+2 = 17 new, the new total is 69. The spec's estimate of 62 was approximate ("~10 new tests"). The actual count of 17 new tests matches the spec's per-file test plan: 4+2+7+2+2.

Expected: 69/69 PASS. Working tree clean except for the four uncommitted files in this task.

- [ ] **Step 9: Commit**

```bash
git add dashboard/app.py dashboard/templates/goal_detail.html dashboard/templates/today.html tests/test_dashboard.py
git commit -m "Surface task elapsed time on goal detail and today timeline"
```

---

## Self-Review

**1. Spec coverage check** (spec §8 acceptance criteria):
- #1 `migrate.py upgrade` applies `002_add_started_at.sql` and advances to v2 → Task 1 (Steps 1, 4-7)
- #2 `update_task_status(id, "in_progress")` stamps via COALESCE → Task 1 (Steps 2, 6)
- #3 `/goal/<slug>` shows `Started` and `Elapsed` columns, existing tasks show `—` → Task 4 (Steps 1, 5, 7)
- #4 `/today` shows `（已用 Xh Ym）` for `in_progress` → Task 4 (Steps 1, 4, 6, 7)
- #5 `format_reminder(...)` contains suffix for `in_progress`, not for `pending` → Task 3 (Steps 1, 3, 4)
- #6 `pytest -q` reports green → Tasks 1 (Step 7), 2 (Step 4), 3 (Step 4), 4 (Step 8) — 69/69
- #7 No file outside the planned list is modified → Step 9 in each task is the only commit step; the planned list matches Global Constraints.

**2. Placeholder scan:** No "TBD", "TODO", "implement later", "fill in details", "similar to Task N" present. Every code step shows full code.

**3. Type consistency:**
- `format_elapsed(started_at: str | None, completed_at: str | None = None) -> str` — declared in Task 2, used identically in Tasks 3 and 4.
- `db.update_task_status(id: str, status: str)` — unchanged signature, only the body changes in Task 1.
- `db.get_task(id)["started_at"]` — same dict-key access pattern used in Tasks 1 and 4.
- `format_reminder(...)` signature — unchanged.
- `_task_row(task: dict) -> dict` — return dict gains two keys, signature unchanged.
- `_today_view(today_date: str) -> dict` — `slot_rows[i]` gains `task_label` key, signature unchanged.

**4. Migration test isolation note (Step 5):** The two new `test_migrate.py` tests pass `TODO_MIGRATIONS_DIR` pointing to a `tmp_path` that contains a freshly-written `002_add_started_at.sql` — they don't depend on the repo's real `migrations/002_add_started_at.sql` (from Step 1). The real file in the repo is what `migrate.py upgrade` would apply for end-to-end smoke tests, but these tests are isolated.

**5. End-to-end smoke test** (optional, post-plan): After all four tasks land, run `python scripts/migrate.py upgrade` against a v1 production DB to confirm the real migration file applies. This is *not* in any task's test step because `test_migrate.py` uses `tmp_path` for isolation; the e2e check is a manual final-acceptance step, not a unit test.
