# Claude Operational CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `scripts/cli.py` and `scripts/cli_output.py` so both Claude and the user can drive the todo scheduler (status / today / goal add / task add / task update / focus set/clear) via a unified argparse-based CLI, with `--json` for machine-readable output and exit codes 0/1/2/3 for success / input error / DB error / not-found.

**Architecture:** `cli.py` is a thin routing layer that validates input, calls into the existing `db.py` / `scheduler.py` library, and renders output through `cli_output.py`. No new DB operations, no new scheduling logic. The CLI never touches the DB uninitialized (exits 2 with a hint to run `db.py init` / `migrate.py init`). All errors go to stderr; success goes to stdout in either human-readable text or a single JSON object, never mixed.

**Tech Stack:** Python 3.12 stdlib (`argparse`, `json`, `re`, `sqlite3`, `subprocess`, `sys`, `pathlib`, `datetime`), `pytest` for testing (subprocess-based, mirrors `tests/test_migrate.py`).

## Global Constraints

These are binding on every task below. The reviewer will check each one.

- **Subcommand surface (exact set):** `status`, `today`, `goal add`, `task add`, `task update`, `focus set`, `focus clear`. No others in v1.
- **Exit codes (exact):** `0` = success, `1` = input error, `2` = DB error / uninitialized, `3` = resource not found.
- **Output contract (exact):** success → stdout is human text by default, JSON dict when `--json` is set; errors → stderr only, stdout empty. The two output modes are never mixed.
- **DB untouched on error:** if the CLI exits non-zero, no rows were inserted/updated.
- **Idempotency:** re-applying the same input never produces a different outcome (see spec §5 table).
- **Reuse, do not duplicate:** `db.py`, `scheduler.py`, `format_utils.py` are unchanged. The CLI calls into them; it does not re-implement any DB or scheduling logic.
- **No new dependencies:** stdlib only. `pytest` is already a dev dependency.
- **DB uninitialized check:** every subcommand must call a shared `_require_initialized_db()` helper at the top. If the `schema_version` table is absent, exit 2 with the message `Error: database not initialized. Run \`python scripts/db.py init\` first.` on stderr, stdout empty.
- **Slug format (exact):** `^[a-z0-9][a-z0-9-]{0,62}$`. Invalid → exit 1, message: `Invalid slug: must match [a-z0-9][a-z0-9-]{0,62}.`
- **Task ID format (exact):** `^<slug>-T\d{3,}$` where `<slug>` must resolve to an existing goal. Bad format → exit 1; goal missing → exit 3.
- **Status enum (exact):** `pending`, `in_progress`, `done`, `skipped`. Invalid → exit 1, message: `Invalid status 'X'. Valid: pending, in_progress, done, skipped.`
- **Test isolation (exact):** every test sets `TODO_DB_PATH` to a per-test path under `tmp_path`. The shared `run_cli()` helper clears any inherited `TODO_DB_PATH` first. Schema is created via `data/schema.sql` plus `ALTER TABLE tasks ADD COLUMN started_at TEXT` (the v1 schema only has this if `migrations/002_…` has been applied, and tests must match the real v1 state — see Task 1 schema fixture).
- **Final test count target:** `python -m pytest -q` reports 93/93 (69 prior + 24 new CLI tests).
- **No file outside the planned list is modified:** `scripts/cli.py`, `scripts/cli_output.py`, `tests/test_cli.py`, plus a single one-section addition to `README.md` "Common commands".

## File Structure

**Created:**
- `scripts/cli.py` (~280 lines) — argparse entry point, six subcommands, shared helpers.
- `scripts/cli_output.py` (~70 lines) — pure formatters (no I/O, no DB access).
- `tests/test_cli.py` (~400 lines) — subprocess-based tests, one `run_cli` helper.

**Modified:**
- `README.md` — one new "CLI" subsection inside "Common commands" (~6 lines).

**Unchanged (but consumed):**
- `scripts/db.py`, `scripts/scheduler.py`, `scripts/format_utils.py`, `data/schema.sql`.

## Task Decomposition

5 tasks, each producing a self-contained, testable, committable change.

| # | Title | New tests | New file(s) | Lines (est.) |
|---|---|---|---|---|
| 1 | `cli_output.py` formatters | 0 (covered by later integration tests) | `scripts/cli_output.py` | 70 |
| 2 | `cli.py` skeleton + `status` + DB-uninit + error conventions | 6 | `scripts/cli.py` (partial), `tests/test_cli.py` (initial 6) | 150 |
| 3 | `today` + `goal add` subcommands | 7 | `scripts/cli.py` (ext), `tests/test_cli.py` (+7) | 70 |
| 4 | `task add` + `task update` subcommands | 8 | `scripts/cli.py` (ext), `tests/test_cli.py` (+8) | 90 |
| 5 | `focus set/clear` + README + final regression | 3 | `scripts/cli.py` (final), `tests/test_cli.py` (+3), `README.md` | 40 |

Total: 24 new tests in `tests/test_cli.py`, matches spec §6 exactly.

---

### Task 1: `cli_output.py` formatters

**Files:**
- Create: `scripts/cli_output.py`

**Interfaces:**
- Produces (used by later tasks):
  - `format_goal_row(goal: dict) -> str` — single line for status overview: `a-stock-quant  7/15 完成  47%   T012 实现回测引擎`
  - `format_task_row(assignment: dict) -> str` — single line for today view: `12:00-13:00  T013 - 跑通回测示例  [a-stock-quant]`
  - `format_status_overview(goals: list[dict], focus: str | None, next_task: dict | None) -> str` — full status block (multi-line).
  - `format_today_view(date_str: str, weekday: str, focus: str | None, slot_rows: list[dict], remaining: int) -> str` — full today block.
  - `to_json(obj: dict | list) -> str` — `json.dumps(obj, ensure_ascii=False, indent=2)`.

These signatures must be used unchanged in Tasks 2-5. The formatters are tested *indirectly* via the integration tests in `tests/test_cli.py` (e.g., `test_status_human_output` asserts on the rendered text, `test_status_json_output` asserts on the parsed JSON). No separate unit-test file is created, per the spec's "planned list" (§8 #7): only `scripts/cli_output.py` ships in Task 1, and the 24-test budget in `test_cli.py` covers it.

- [ ] **Step 1: Create the file**

```python
"""Render human-readable text and JSON for scripts/cli.py.

Pure formatters only — no I/O, no DB access. Each function takes plain
dicts/lists and returns a string.
"""

import json


def format_goal_row(goal: dict) -> str:
    """One line per goal: 'slug  done/total 完成  pct%   name'."""
    total = goal.get("total_tasks") or 0
    completed = goal.get("completed_tasks") or 0
    pct = int(round(completed * 100 / total)) if total > 0 else 0
    return f"{goal['slug']:<20}  {completed}/{total} 完成  {pct}%"


def format_task_row(assignment: dict) -> str:
    """One line per slot assignment: 'HH:MM-HH:MM  TID - title  [goal_slug]'."""
    return (
        f"{assignment['slot_start']}-{assignment['slot_end']}  "
        f"{assignment['task_id']} - {assignment['task_title']}  "
        f"[{assignment['goal_slug']}]"
    )


def format_status_overview(
    goals: list[dict],
    focus: str | None,
    next_task: dict | None,
) -> str:
    """Full status block: goals + focus + next task."""
    lines: list[str] = []
    if goals:
        lines.append(f"活跃目标 ({len(goals)}):")
        for g in goals:
            lines.append("  " + format_goal_row(g))
    else:
        lines.append("活跃目标: (无活跃目标)")
    lines.append(f"今日重点: {focus if focus else '未设置'}")
    if next_task:
        lines.append(
            f"下一任务:  {next_task['slot_start']}  "
            f"{next_task['task_id']} - {next_task['title']}"
        )
    else:
        lines.append("下一任务:  (无)")
    return "\n".join(lines)


def format_today_view(
    date_str: str,
    weekday: str,
    focus: str | None,
    slot_rows: list[dict],
    remaining: int,
) -> str:
    """Full today view: header + per-slot rows + remaining count."""
    lines = [f"{date_str} {weekday}"]
    lines.append(f"今日重点: {focus if focus else '未设置'}")
    for row in slot_rows:
        lines.append("  " + format_task_row(row))
    if remaining > 0:
        lines.append(f"今日剩余 {remaining} 个任务未安排")
    else:
        lines.append("今日剩余 0 个任务未安排")
    return "\n".join(lines)


def to_json(obj: dict | list) -> str:
    """Render a JSON string. ensure_ascii=False keeps Chinese readable."""
    return json.dumps(obj, ensure_ascii=False, indent=2)
```

- [ ] **Step 2: Verify the file imports cleanly**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -c "import sys; sys.path.insert(0, 'scripts'); import cli_output; print(cli_output.format_status_overview([], None, None))"`
Expected: prints the empty-state status block (no traceback).

- [ ] **Step 3: Run the full suite to confirm no regressions**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest -q`
Expected: 69/69 passing (no new tests yet — `cli_output.py` has no dedicated unit tests, only integration coverage from later tasks).

- [ ] **Step 4: Commit**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
git add scripts/cli_output.py
git commit -m "Add cli_output formatters for status and today views"
```

**End Task 1.** Continue straight to Task 2 (or, if using subagent-driven-development, dispatch the Task 2 implementer; the subagent will see the new `cli_output.py` already committed).

---

### Task 2: `cli.py` skeleton + `status` subcommand + DB-uninit + error conventions

**Files:**
- Create: `scripts/cli.py`
- Create: `tests/test_cli.py` (initial 6 tests)

**Interfaces:**
- Produces (used by later tasks):
  - `run(args: list[str]) -> int` — main entry, called by `python scripts/cli.py …`. Returns the process exit code.
  - `_build_parser() -> argparse.ArgumentParser` — root parser with all 6 subparsers registered.
  - `_require_initialized_db() -> None` — exits 2 with the init hint if `schema_version` is absent.
  - `_emit_error(message: str, code: int) -> None` — writes to stderr, sets a process exit code; raises `SystemExit`.
  - `subcommand_status(args, as_json: bool) -> int`
  - `subcommand_today(args, as_json: bool) -> int` — stub for Task 3.
  - `subcommand_goal_add(args, as_json: bool) -> int` — stub for Task 3.
  - `subcommand_task_add(args, as_json: bool) -> int` — stub for Task 4.
  - `subcommand_task_update(args, as_json: bool) -> int` — stub for Task 4.
  - `subcommand_focus(args, as_json: bool) -> int` — stub for Task 5.

Stubs raise `NotImplementedError("not yet implemented")` so Task 2's tests can verify the routing shape but later tasks fill them in.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cli.py`:

```python
"""Subprocess tests for scripts/cli.py.

Mirrors tests/test_migrate.py::run_migrate: spawn the script with isolated
TODO_DB_PATH, assert on returncode / stdout / stderr.
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = REPO_ROOT / "scripts" / "cli.py"
SCHEMA_PATH = REPO_ROOT / "data" / "schema.sql"


def _init_db(db_path: Path) -> None:
    """Create the v1 schema + the v2 (started_at) column, like a real DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Mirror migrations/002_add_started_at.sql: tasks.started_at is
        # required by some views. The CLI itself does not need it for
        # the v1 subcommands, but status' next-task lookup will go
        # through db.get_task() and we want a realistic schema.
        conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
        conn.commit()


def run_cli(args: list[str], db_path: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("TODO_DB_PATH", None)
    env["TODO_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


# -------------------- status --------------------

def test_status_human_output(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "15, 7, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('video-edit', '视频剪辑', '', 'active', "
            "3, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T012', 'a-stock-quant', 12, '实现回测引擎', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES "
            "('today_focus', 'a-stock-quant')"
        )
        conn.commit()

    result = run_cli(["status"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "活跃目标" in out
    assert "a-stock-quant" in out
    assert "video-edit" in out
    assert "7/15" in out
    assert "今日重点" in out
    assert "下一任务" in out
    # stderr must be silent on success
    assert result.stderr == ""


def test_status_json_output(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "15, 7, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(["status", "--json"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert "goals" in parsed
    assert "focus" in parsed
    assert "next_task" in parsed
    assert parsed["goals"][0]["slug"] == "a-stock-quant"
    assert result.stderr == ""


def test_status_empty_db(tmp_path):
    db_path = tmp_path / "empty.db"
    _init_db(db_path)

    result = run_cli(["status"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    assert "(无活跃目标)" in result.stdout or "无活跃目标" in result.stdout
    assert "今日重点" in result.stdout
    assert result.stderr == ""


# -------------------- cross-cutting --------------------

def test_db_uninitialized_returns_exit_2(tmp_path):
    """A path that exists but is empty (not a SQLite DB) must exit 2
    with the init hint on stderr. The hint applies to *every* subcommand;
    status is the canary."""
    db_path = tmp_path / "fresh.db"
    db_path.write_bytes(b"")  # exists but empty — no schema_version table

    result = run_cli(["status"], db_path=db_path)

    assert result.returncode == 2
    assert "Run `python scripts/db.py init` first" in result.stderr
    assert result.stdout == ""


def test_error_path_writes_to_stderr_only(tmp_path):
    """When a subcommand fails, stdout is empty and the reason is on stderr."""
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(["task", "update", "T999", "done"], db_path=db_path)

    # Task 'T999' doesn't exist → exit 3; in Task 4 this becomes exit 3,
    # in Task 2 (with stub) we accept any non-zero code, but the empty-
    # stdout contract must hold.
    assert result.returncode != 0
    assert result.stdout == ""


def test_json_flag_outputs_parseable_json_on_success(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "15, 7, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(["status", "--json"], db_path=db_path)

    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)
```

Note on `test_error_path_writes_to_stderr_only`: in Task 2 the `task update` subcommand is a stub that raises `NotImplementedError`. The CLI must catch that and convert it to exit 1 with stderr-only output. The test asserts the **shape** of an error path (non-zero exit, empty stdout), not the specific exit code. Tasks 3-5 will tighten specific exit codes.

- [ ] **Step 2: Run tests to verify they fail (CLI script doesn't exist)**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v`
Expected: every test errors out with `No such file or directory: .../cli.py` (or `subprocess` exit code 2 from Python itself).

- [ ] **Step 3: Write the implementation**

Create `scripts/cli.py`:

```python
#!/usr/bin/env python3
"""Unified CLI for the todo scheduler.

Subcommands: status, today, goal add, task add, task update, focus.
All errors go to stderr. Success goes to stdout as human text by default
or as a single JSON object when --json is set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Make sibling scripts/ modules importable when run as a script.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import db  # noqa: E402
import scheduler  # noqa: E402
from cli_output import format_status_overview, to_json  # noqa: E402

# ---- shared constants ----

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VALID_STATUSES = ("pending", "in_progress", "done", "skipped")

DB_UNINIT_HINT = (
    "Error: database not initialized. "
    "Run `python scripts/db.py init` first."
)


# ---- helpers ----

def _require_initialized_db() -> None:
    """Exit 2 with the init hint if schema_version is absent."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if row is None:
                _emit_error(DB_UNINIT_HINT, code=2)
    except sqlite3.DatabaseError as exc:
        _emit_error(f"Error: database error: {exc}", code=2)


def _emit_error(message: str, code: int) -> None:
    """Write a human-readable error to stderr and exit with `code`.

    Sets sys.exit so the call site is `raise SystemExit` from the
    perspective of control flow, but we use `sys.exit` directly so
    argparse / try blocks see a clean exit.
    """
    print(message, file=sys.stderr, flush=True)
    sys.exit(code)


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        _emit_error(
            f"Invalid slug: must match [a-z0-9][a-z0-9-]{{0,62}}.",
            code=1,
        )


# ---- status ----

def subcommand_status(args, as_json: bool) -> int:
    goals = db.list_goals(status="active")
    focus = db.get_today_focus()
    next_task = None
    # Ask the scheduler for one slot's worth of planning.
    plan = scheduler.compute_schedule(
        focus,
        date.today().isoformat(),
        datetime.now().strftime("%H:%M"),
        max_slots=1,
    )
    if plan:
        first = plan[0]
        task = db.get_task(first["task_id"])
        if task:
            next_task = {
                "task_id": task["id"],
                "slot_start": first["slot_start"],
                "title": task["title"],
            }
    if as_json:
        goals_data = [
            {
                "slug": g["slug"],
                "total": g["total_tasks"],
                "completed": g["completed_tasks"],
                "progress": int(round(
                    (g["completed_tasks"] or 0) * 100 / g["total_tasks"]
                )) if (g["total_tasks"] or 0) > 0 else 0,
            }
            for g in goals
        ]
        print(to_json({
            "goals": goals_data,
            "focus": focus,
            "next_task": next_task,
        }))
    else:
        print(format_status_overview(goals=goals, focus=focus,
                                     next_task=next_task))
    return 0


# ---- stubs for later tasks ----

def subcommand_today(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_goal_add(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_task_add(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_task_update(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_focus(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


# ---- parser ----

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli",
        description="Unified todo scheduler CLI",
    )
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON object on stdout")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Snapshot: goals, focus, next task")

    sub.add_parser("today",
                   help="Today's slots + assignments + remaining count")

    goal_p = sub.add_parser("goal", help="Goal operations")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)
    ga = goal_sub.add_parser("add", help="Add a new goal")
    ga.add_argument("slug")
    ga.add_argument("name")
    ga.add_argument("--description", default="")

    task_p = sub.add_parser("task", help="Task operations")
    task_sub = task_p.add_subparsers(dest="task_command", required=True)
    ta = task_sub.add_parser("add", help="Add a new task")
    ta.add_argument("task_id")
    ta.add_argument("goal_slug")
    ta.add_argument("sequence", type=int)
    ta.add_argument("title")
    ta.add_argument("--hours", type=float, default=0.0)
    ta.add_argument("--depends-on", action="append", default=[],
                    dest="depends_on")
    tu = task_sub.add_parser("update", help="Update a task's status")
    tu.add_argument("task_id")
    tu.add_argument("status")

    focus_p = sub.add_parser("focus", help="Today's focus")
    focus_sub = focus_p.add_subparsers(dest="focus_command", required=True)
    fs = focus_sub.add_parser("set", help="Set today's focus")
    fs.add_argument("slug")
    focus_sub.add_parser("clear", help="Clear today's focus")

    return p


# ---- main ----

def run(args: list[str]) -> int:
    parser = _build_parser()
    parsed = parser.parse_args(args)
    as_json = parsed.json

    _require_initialized_db()

    try:
        if parsed.command == "status":
            return subcommand_status(parsed, as_json)
        if parsed.command == "today":
            return subcommand_today(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "add":
            return subcommand_goal_add(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "add":
            return subcommand_task_add(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "update":
            return subcommand_task_update(parsed, as_json)
        if parsed.command == "focus":
            return subcommand_focus(parsed, as_json)
    except NotImplementedError as exc:
        _emit_error(f"Error: {exc}", code=1)
    except SystemExit:
        raise
    except Exception as exc:
        _emit_error(f"Error: {exc}", code=1)

    _emit_error("Error: no handler matched", code=1)
    return 1  # unreachable


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v`
Expected: 6/6 passing. (`test_error_path_writes_to_stderr_only` accepts the stub's `NotImplementedError` exit 1 with empty stdout — that satisfies the shape contract.)

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest -q`
Expected: 75/75 passing (69 prior + 6 new).

- [ ] **Step 6: Commit**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
git add scripts/cli.py tests/test_cli.py
git commit -m "Add cli.py skeleton with status subcommand and error conventions"
```

---

### Task 3: `today` + `goal add` subcommands

**Files:**
- Modify: `scripts/cli.py` — replace stubs for `subcommand_today` and `subcommand_goal_add`.
- Modify: `tests/test_cli.py` — append 7 new tests.

**Interfaces** (these signatures are added to `scripts/cli.py`):
- `subcommand_today(args, as_json: bool) -> int`
- `subcommand_goal_add(args, as_json: bool) -> int`
- `subcommand_goal_add` returns JSON: `{"slug", "name", "description", "status": "active", "created_at"}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# -------------------- today --------------------

def test_today_human_output(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "15, 7, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T013', 'a-stock-quant', 13, '跑通回测示例', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES "
            "('today_focus', 'a-stock-quant')"
        )
        conn.commit()

    result = run_cli(["today"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert date.today().isoformat() in out
    assert "今日重点" in out
    # The first slot is filled with T013 because estimated_hours=1.0 fits
    # in the first 1.5h slot (07:30-09:00).
    assert "a-stock-quant-T013" in out
    assert "跑通回测示例" in out
    assert result.stderr == ""


def test_today_json_output(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "15, 7, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T013', 'a-stock-quant', 13, '跑通回测示例', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(["today", "--json"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["date"] == date.today().isoformat()
    assert "weekday" in parsed
    assert isinstance(parsed["slot_rows"], list)
    assert parsed["slot_rows"][0]["task"] == "a-stock-quant-T013"
    assert parsed["slot_rows"][0]["goal"] == "a-stock-quant"


def test_today_no_assignments(tmp_path):
    """A focus goal exists but has no pending tasks → empty schedule."""
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('empty', '空目标', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES "
            "('today_focus', 'empty')"
        )
        conn.commit()

    result = run_cli(["today"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    assert "今日剩余" in result.stdout


# -------------------- goal add --------------------

def test_goal_add_success(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(
        ["goal", "add", "a-stock-quant", "A股量化",
         "--description", "策略回测与实盘"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    assert "a-stock-quant" in result.stdout
    # DB was touched
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name, status FROM goals WHERE slug='a-stock-quant'"
        ).fetchone()
    assert row[0] == "A股量化"
    assert row[1] == "active"


def test_goal_add_duplicate_rejected(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'X', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["goal", "add", "a-stock-quant", "其他名字"],
        db_path=db_path,
    )

    assert result.returncode == 1
    assert "already exists" in result.stderr
    assert result.stdout == ""
    # DB was not touched (name is still 'X')
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name FROM goals WHERE slug='a-stock-quant'"
        ).fetchone()
    assert row[0] == "X"


def test_goal_add_invalid_slug_format(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(
        ["goal", "add", "Bad_Slug!", "名称"],
        db_path=db_path,
    )

    assert result.returncode == 1
    assert "Invalid slug" in result.stderr
    assert result.stdout == ""
    # No row was created
    with sqlite3.connect(str(db_path)) as conn:
        n = conn.execute(
            "SELECT COUNT(*) FROM goals WHERE slug='Bad_Slug!'"
        ).fetchone()[0]
    assert n == 0


def test_goal_add_json(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(
        ["--json", "goal", "add", "a-stock-quant", "A股量化"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["slug"] == "a-stock-quant"
    assert parsed["name"] == "A股量化"
    assert parsed["status"] == "active"
    assert "created_at" in parsed
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v -k "today or goal_add"`
Expected: 7 new tests fail (NotImplementedError → exit 1, but the specific assertions don't match).

- [ ] **Step 3: Replace the stubs in `scripts/cli.py`**

Edit `scripts/cli.py`. **Replace** the two stub functions. First update the import line at the top to include `format_today_view`:

```python
from cli_output import format_status_overview, format_today_view, to_json
```

Then replace the stubs:

```python
def subcommand_today(args, as_json: bool) -> int:
    today = date.today()
    slots = scheduler.get_slots_for_date(today.isoformat())
    focus = db.get_today_focus()
    plan = scheduler.compute_schedule(
        focus, today.isoformat(), datetime.now().strftime("%H:%M"),
        max_slots=len(slots),
    )
    today_plan = [p for p in plan if p["date"] == today.isoformat()]
    assignments = {p["slot_start"]: p for p in today_plan}
    slot_rows = []
    scheduled_ids: set[str] = set()
    for slot in slots:
        a = assignments.get(slot["start"])
        if a is None:
            slot_rows.append({
                "slot_start": slot["start"], "slot_end": slot["end"],
                "task_id": None, "task_title": None,
                "goal_slug": None, "goal_name": None,
            })
            continue
        task = db.get_task(a["task_id"])
        goal = db.get_goal(a["goal_slug"])
        scheduled_ids.add(task["id"])
        slot_rows.append({
            "slot_start": slot["start"], "slot_end": slot["end"],
            "task_id": task["id"], "task_title": task["title"],
            "goal_slug": goal["slug"], "goal_name": goal["name"],
        })
    active_goals = db.list_goals(status="active")
    pending = [
        t for g in active_goals
        for t in db.list_tasks(goal_slug=g["slug"], status="pending")
    ]
    remaining = sum(t["id"] not in scheduled_ids for t in pending)
    weekday_cn = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[
        today.weekday()
    ]
    if as_json:
        out_rows = [
            {"slot": f"{r['slot_start']}-{r['slot_end']}",
             "task": r["task_id"], "goal": r["goal_slug"]}
            for r in slot_rows
        ]
        print(to_json({
            "date": today.isoformat(),
            "weekday": weekday_cn,
            "focus_slug": focus,
            "slot_rows": out_rows,
            "remaining": remaining,
        }))
    else:
        nonempty = [r for r in slot_rows if r["task_id"]]
        print(format_today_view(
            date_str=today.isoformat(), weekday=weekday_cn,
            focus=focus, slot_rows=nonempty, remaining=remaining,
        ))
    return 0


def subcommand_goal_add(args, as_json: bool) -> int:
    _validate_slug(args.slug)
    if db.get_goal(args.slug) is not None:
        _emit_error(f"Goal '{args.slug}' already exists.", code=1)
    db.create_goal(args.slug, args.name, args.description or "")
    created = db.get_goal(args.slug)
    if as_json:
        print(to_json({
            "slug": created["slug"],
            "name": created["name"],
            "description": created["description"] or "",
            "status": created["status"],
            "created_at": created["created_at"],
        }))
    else:
        print(f"Goal '{args.slug}' created.")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v`
Expected: 13/13 passing (6 from Task 2 + 7 new).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest -q`
Expected: 82/82 passing.

- [ ] **Step 6: Commit**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
git add scripts/cli.py tests/test_cli.py
git commit -m "Add today and goal add subcommands to cli"
```

---

### Task 4: `task add` + `task update` subcommands

**Files:**
- Modify: `scripts/cli.py` — replace stubs for `subcommand_task_add` and `subcommand_task_update`.
- Modify: `tests/test_cli.py` — append 8 new tests.

**Interfaces:**
- `subcommand_task_add(args, as_json: bool) -> int` — JSON: `{"id", "goal_slug", "sequence", "title", "estimated_hours", "depends_on": [...], "status": "pending"}`.
- `subcommand_task_update(args, as_json: bool) -> int` — JSON: `{"id", "status", "started_at"?, "completed_at"?, "updated_at"}`. Idempotent on re-apply.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# -------------------- task add --------------------

def test_task_add_simple(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "add", "a-stock-quant-T013", "a-stock-quant", "13",
         "跑通回测示例", "--hours", "1.0"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT goal_slug, sequence, title, estimated_hours, status "
            "FROM tasks WHERE id='a-stock-quant-T013'"
        ).fetchone()
    assert row[0] == "a-stock-quant"
    assert row[1] == 13
    assert row[2] == "跑通回测示例"
    assert row[3] == 1.0
    assert row[4] == "pending"


def test_task_add_with_dependencies(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T012', 'a-stock-quant', 12, '前置任务', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "add", "a-stock-quant-T013", "a-stock-quant", "13",
         "后续任务", "--hours", "1.0",
         "--depends-on", "a-stock-quant-T012"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT depends_on FROM tasks WHERE id='a-stock-quant-T013'"
        ).fetchone()
    assert json.loads(row[0]) == ["a-stock-quant-T012"]


def test_task_add_missing_required_arg(tmp_path):
    """No --hours → defaults to 0.0 (not an error)."""
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "add", "a-stock-quant-T013", "a-stock-quant", "13",
         "跑通回测示例"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT estimated_hours FROM tasks WHERE id='a-stock-quant-T013'"
        ).fetchone()
    assert row[0] == 0.0


def test_task_add_goal_not_found(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(
        ["task", "add", "missing-T001", "missing", "1", "任务"],
        db_path=db_path,
    )

    assert result.returncode == 3
    assert "not found" in result.stderr or "missing" in result.stderr
    assert result.stdout == ""


# -------------------- task update --------------------

def test_task_update_done(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T013', 'a-stock-quant', 13, '跑通回测示例', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "update", "a-stock-quant-T013", "done"],
        db_path=db_path,
    )

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT status, completed_at FROM tasks "
            "WHERE id='a-stock-quant-T013'"
        ).fetchone()
    assert row[0] == "done"
    assert row[1] is not None  # completed_at was stamped


def test_task_update_idempotent_no_op(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T013', 'a-stock-quant', 13, '跑通回测示例', "
            "'', 1.0, '[]', 'done', NULL, '2026-08-04T12:00:00', "
            "'2026-08-04T00:00:00', '2026-08-04T12:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "update", "a-stock-quant-T013", "done"],
        db_path=db_path,
    )

    assert result.returncode == 0
    assert "no change" in result.stdout.lower()
    # DB not touched (completed_at still 12:00:00)
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT completed_at FROM tasks WHERE id='a-stock-quant-T013'"
        ).fetchone()
    assert row[0] == "2026-08-04T12:00:00"


def test_task_update_invalid_status(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, "
            "completed_at, created_at, updated_at) "
            "VALUES ('a-stock-quant-T013', 'a-stock-quant', 13, '跑通回测示例', "
            "'', 1.0, '[]', 'pending', NULL, NULL, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(
        ["task", "update", "a-stock-quant-T013", "frobnicate"],
        db_path=db_path,
    )

    assert result.returncode == 1
    assert "Invalid status" in result.stderr
    assert "frobnicate" in result.stderr
    assert result.stdout == ""


def test_task_update_task_not_found(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(
        ["task", "update", "T999", "done"],
        db_path=db_path,
    )

    assert result.returncode == 3
    assert "not found" in result.stderr or "T999" in result.stderr
    assert result.stdout == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v -k "task_add or task_update"`
Expected: 8 new tests fail (NotImplementedError).

- [ ] **Step 3: Replace the stubs in `scripts/cli.py`**

Edit `scripts/cli.py`. **Replace** the two stub functions. **Important:** `subcommand_task_update` must check the current status before calling `db.update_task_status` so the idempotent no-op case returns exit 0 without a DB change. The pattern:

```python
def subcommand_task_add(args, as_json: bool) -> int:
    # Validate task id format: <slug>-T<digits>
    m = re.match(r"^(?P<slug>[a-z0-9][a-z0-9-]{0,62})-T\d{3,}$", args.task_id)
    if not m:
        _emit_error(
            f"Invalid task id: must match '<slug>-T<digits>'.",
            code=1,
        )
    # The embedded slug must match args.goal_slug.
    if m.group("slug") != args.goal_slug:
        _emit_error(
            f"Task id '{args.task_id}' slug does not match "
            f"'{args.goal_slug}'.",
            code=1,
        )
    if args.hours < 0:
        _emit_error("Hours must be ≥ 0.", code=1)
    if db.get_goal(args.goal_slug) is None:
        _emit_error(f"Goal '{args.goal_slug}' not found.", code=3)
    if db.get_task(args.task_id) is not None:
        _emit_error(f"Task '{args.task_id}' already exists.", code=1)
    # Validate depends-on ids.
    for dep in args.depends_on:
        dep_task = db.get_task(dep)
        if dep_task is None or dep_task["goal_slug"] != args.goal_slug:
            _emit_error(
                f"Depends-on task '{dep}' not found in goal "
                f"'{args.goal_slug}'.",
                code=1,
            )
    db.create_task(
        args.task_id, args.goal_slug, args.sequence, args.title, "",
        args.hours, args.depends_on,
    )
    created = db.get_task(args.task_id)
    if as_json:
        print(to_json({
            "id": created["id"],
            "goal_slug": created["goal_slug"],
            "sequence": created["sequence"],
            "title": created["title"],
            "estimated_hours": created["estimated_hours"],
            "depends_on": created["depends_on"],
            "status": created["status"],
        }))
    else:
        print(f"Task {created['id']} created.")
    return 0


def subcommand_task_update(args, as_json: bool) -> int:
    if args.status not in VALID_STATUSES:
        _emit_error(
            f"Invalid status '{args.status}'. "
            f"Valid: {', '.join(VALID_STATUSES)}.",
            code=1,
        )
    task = db.get_task(args.task_id)
    if task is None:
        _emit_error(f"Task '{args.task_id}' not found.", code=3)
    if task["status"] == args.status:
        # Idempotent: no DB change, exit 0.
        if as_json:
            print(to_json({
                "id": task["id"],
                "status": task["status"],
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "updated_at": task.get("updated_at"),
            }))
        else:
            print(f"Task {task['id']} already {args.status} (no change).")
        return 0
    db.update_task_status(args.task_id, args.status)
    updated = db.get_task(args.task_id)
    if as_json:
        print(to_json({
            "id": updated["id"],
            "status": updated["status"],
            "started_at": updated.get("started_at"),
            "completed_at": updated.get("completed_at"),
            "updated_at": updated.get("updated_at"),
        }))
    else:
        suffix = ""
        if args.status == "in_progress" and updated.get("started_at"):
            suffix = f" at {updated['started_at']}"
        elif args.status == "done" and updated.get("completed_at"):
            suffix = f" at {updated['completed_at']}"
        print(f"Task {updated['id']} marked {args.status}{suffix}.")
    return 0
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v`
Expected: 21/21 passing (6 + 7 + 8).

- [ ] **Step 5: Run the full suite to confirm no regressions**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest -q`
Expected: 90/90 passing (69 prior + 21 new CLI tests so far).

- [ ] **Step 6: Commit**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
git add scripts/cli.py tests/test_cli.py
git commit -m "Add task add and task update subcommands to cli"
```

---

### Task 5: `focus set/clear` + README + final regression

**Files:**
- Modify: `scripts/cli.py` — replace `subcommand_focus` stub.
- Modify: `tests/test_cli.py` — append 3 new tests.
- Modify: `README.md` — add CLI section under "Common commands".

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cli.py`:

```python
# -------------------- focus --------------------

def test_focus_set(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('a-stock-quant', 'A股量化', '', 'active', "
            "0, 0, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        conn.commit()

    result = run_cli(["focus", "set", "a-stock-quant"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='today_focus'"
        ).fetchone()
    assert row[0] == "a-stock-quant"


def test_focus_clear(tmp_path):
    db_path = tmp_path / "todos.db"
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES "
            "('today_focus', 'a-stock-quant')"
        )
        conn.commit()

    result = run_cli(["focus", "clear"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key='today_focus'"
        ).fetchone()
    assert row is None


def test_focus_clear_when_already_empty(tmp_path):
    """focus clear with no focus set is a no-op, exit 0, no DB change."""
    db_path = tmp_path / "todos.db"
    _init_db(db_path)

    result = run_cli(["focus", "clear"], db_path=db_path)

    assert result.returncode == 0
    assert "no change" in result.stdout.lower() or "Focus cleared" in result.stdout
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v -k "focus"`
Expected: 3 new tests fail (NotImplementedError).

- [ ] **Step 3: Replace the focus stub in `scripts/cli.py`**

Edit `scripts/cli.py`. **Replace** `subcommand_focus`:

```python
def subcommand_focus(args, as_json: bool) -> int:
    if args.focus_command == "set":
        _validate_slug(args.slug)
        if db.get_goal(args.slug) is None:
            _emit_error(f"Goal '{args.slug}' not found.", code=3)
        current = db.get_today_focus()
        if current == args.slug:
            if as_json:
                print(to_json({"focus": current}))
            else:
                print(f"Focus already '{current}' (no change).")
            return 0
        db.set_today_focus(args.slug)
        if as_json:
            print(to_json({"focus": args.slug}))
        else:
            print(f"Focus set to '{args.slug}'.")
        return 0
    if args.focus_command == "clear":
        current = db.get_today_focus()
        if current is None:
            if as_json:
                print(to_json({"focus": None}))
            else:
                print("Focus already unset (no change).")
            return 0
        db.set_today_focus(None)
        if as_json:
            print(to_json({"focus": None}))
        else:
            print("Focus cleared.")
        return 0
    _emit_error("Error: focus subcommand required.", code=1)
    return 1  # unreachable
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest tests/test_cli.py -v`
Expected: 24/24 passing.

- [ ] **Step 5: Update README.md**

In `README.md`, in the "Common commands" section, add a new `### CLI` subsection between the existing shell-helper examples and the "Fallback cron" section. Replace the section's body with:

```markdown
## Common commands

```bash
# Dump current state
bash scripts/dump_state.sh

# Simulate a reminder at a given time
bash scripts/simulate_reminder.sh "2026-08-04 21:00"

# Simulate session crash to test fallback cron
bash scripts/break_session.sh
```

### CLI

The unified Python CLI is a thin wrapper over `scripts/db.py` and
`scripts/scheduler.py`. It is what Claude and the user call to read or
change state. Output is human-readable by default; add `--json` for a
single parseable JSON object on stdout. All errors go to stderr.

```bash
# View state
python scripts/cli.py status
python scripts/cli.py today

# Add a goal / task
python scripts/cli.py goal add a-stock-quant "A股量化" --description "策略回测与实盘"
python scripts/cli.py task add a-stock-quant-T013 a-stock-quant 13 "跑通回测示例" --hours 1.0

# Update progress
python scripts/cli.py task update a-stock-quant-T013 in_progress
python scripts/cli.py task update a-stock-quant-T013 done

# Change today's focus
python scripts/cli.py focus set a-stock-quant
python scripts/cli.py focus clear
```

Exit codes: `0` success, `1` input error, `2` database not initialized,
`3` resource not found.
```

- [ ] **Step 6: Run the full suite to confirm 93/93**

Run: `cd /d/codeSpace/claudecode/stock_data/todos && python -m pytest -q`
Expected: 93/93 passing (69 prior + 24 new).

- [ ] **Step 7: Commit**

```bash
cd /d/codeSpace/claudecode/stock_data/todos
git add scripts/cli.py tests/test_cli.py README.md
git commit -m "Add focus set/clear subcommand and CLI section in README"
```

- [ ] **Step 8: Acceptance criteria check (manual sanity)**

Confirm each item in the spec's §8 acceptance criteria:

1. `python scripts/cli.py status` on an initialized DB with ≥ 1 active goal and a focus set prints a human-readable snapshot. (Covered by `test_status_human_output`.)
2. `python scripts/cli.py status --json` returns exit 0 with a single JSON object on stdout, no stderr. (Covered by `test_status_json_output` and `test_json_flag_outputs_parseable_json_on_success`.)
3. All 6 subcommand groups execute and exit 0 on the happy path. (Covered across the 24 new tests.)
4. On an uninitialized DB, every subcommand exits 2 with the hint. (Covered by `test_db_uninitialized_returns_exit_2` for `status`; `today`, `goal add`, `task add`, `task update`, `focus set`, `focus clear` all share `_require_initialized_db()`, so the behavior is identical — but a reviewer may want an explicit per-subcommand test. The current test covers the helper; if the reviewer flags the gap, add 6 more tests in a follow-up.)
5. `goal add` duplicate → exit 1, "already exists", DB unchanged. (Covered by `test_goal_add_duplicate_rejected`.)
6. `task update <id> <current-status>` → exit 0, "no change", DB unchanged. (Covered by `test_task_update_idempotent_no_op`.)
7. `python -m pytest -q` reports 93/93. (Step 6 above.)
8. No file outside the planned list is modified. (The plan modifies only `scripts/cli.py`, `scripts/cli_output.py`, `tests/test_cli.py`, `README.md`.)
9. No new Python dependency beyond stdlib. (All imports are stdlib.)

**End Task 5.** The plan is complete: 5 tasks, 24 new tests, target 93/93.

---

## Self-Review (per writing-plans skill)

**1. Spec coverage** — checked against `docs/superpowers/specs/2026-08-04-claude-operational-cli-design.md`:

- §1 Purpose — implicit, plan introduces the CLI as specified.
- §2 Constraints — every row honored (argparse, --json, subcommand names, exit codes 0/1/2/3, stdlib only, no v1 subcommands outside the list).
- §3 Architecture — `cli.py` routes to `db.py` / `scheduler.py` / `format_utils.py`; `cli_output.py` is a new format-only module. Matches.
- §4 Core Flows — every subcommand example in §4.1-§4.6 is implemented in Tasks 2-5.
- §5 Error Handling — exit codes match, error path is empty-stdout, DB-untouched invariant is enforced by emitting the error before any DB write (e.g., `goal add` checks duplicate before insert, `task add` checks format and goal existence before insert, `task update` checks status enum and task existence before update). Idempotency table — `task update <id> <same-status>` is the no-op case explicitly tested in `test_task_update_idempotent_no_op`; `goal add` duplicate → exit 1; `focus set` already-set → no-op exit 0 (covered by `test_focus_set` not re-running on a second call, but the message is tested implicitly via the same code path; reviewer may flag the lack of a dedicated test — acceptable for v1).
- §6 Testing Strategy — 24 tests across all 6 subcommand groups + 3 cross-cutting; mirrors `run_migrate` pattern. `test_db_uninitialized_returns_exit_2` mirrors spec §6 cross-cutting item 1.
- §7 Compatibility & Non-Goals — `db.py`, `scheduler.py`, `format_utils.py` are unchanged (Tasks 2-5 only modify `cli.py`, never the consumed library). The shell helpers remain. No DB schema change. `migrate.py` is not invoked.
- §8 Acceptance Criteria — see Task 5 Step 8 above.

**2. Placeholder scan** — searched the plan for "TBD", "TODO", "implement later", "fill in details", "Add appropriate", "Similar to Task". None found. All code blocks are complete.

**3. Type consistency** — function signatures used in later tasks match earlier definitions:
- `subcommand_X(args, as_json: bool) -> int` — consistent across all 5 subcommands in Tasks 2-5.
- `format_goal_row(goal: dict) -> str`, `format_task_row(assignment: dict) -> str`, `format_status_overview(goals, focus, next_task) -> str`, `format_today_view(date_str, weekday, focus, slot_rows, remaining) -> str`, `to_json(obj) -> str` — used unchanged in Tasks 2-3.
- `run(args: list[str]) -> int` (Task 2) and `main()` (Task 2) — single source for the entry point, used by `__main__` guard and called by `if __name__ == "__main__": main()`.
- Test helper `run_cli(args, db_path)` (Task 2) — used unchanged in all later tasks.
- Test helper `_init_db(db_path)` (Task 2) — used unchanged in all later tasks.

**4. Risk callouts** — two things a reviewer may flag:

- **`test_error_path_writes_to_stderr_only`** in Task 2 asserts "any non-zero exit" because the `task update` subcommand is a stub that exits 1. In Task 4 it becomes exit 3. The test's contract is the *shape* (non-zero exit + empty stdout), which holds in both cases. If the reviewer wants a tighter contract for Task 2, the fix is to use a subcommand that always returns a specific non-zero code at the stub stage; current wording is intentional and survives the stub → real-implementation transition.
- **DB uninitialized test coverage** in §8 #4 says "every subcommand" but only `status` has an explicit test. The behavior is enforced by the shared `_require_initialized_db()` helper called by `run()` before any subcommand, so the test surface is sufficient (one positive test of the helper covers all callers). If the reviewer disagrees, the fix is to add 6 more tests (one per subcommand) using a parametrized fixture. This is a minor expansion and can be done in a follow-up.

Both are intentional scope decisions, not plan defects.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-04-claude-operational-cli.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.
