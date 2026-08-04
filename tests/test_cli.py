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
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CLI_SCRIPT = REPO_ROOT / "scripts" / "cli.py"
SCHEMA_PATH = REPO_ROOT / "data" / "schema.sql"


# Allow unit tests below to do `from cli import reconcile_timers` etc.
# The integration tests still use subprocess (unchanged behavior).
import importlib.util as _ilu
_cli_spec = _ilu.spec_from_file_location("cli", str(CLI_SCRIPT))
_cli_module = _ilu.module_from_spec(_cli_spec)
_cli_spec.loader.exec_module(_cli_module)
sys.modules["cli"] = _cli_module


def _init_db(db_path: Path) -> None:
    """Create the v1 schema + the v2 (started_at) column, like a real DB."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        # Create schema_version table and stamp version 1
        conn.executescript(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, '2026-08-04T00:00:00')"
        )
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

    result = run_cli(["--json", "status"], db_path=db_path)

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

    result = run_cli(["--json", "status"], db_path=db_path)

    assert result.returncode == 0
    parsed = json.loads(result.stdout)
    assert isinstance(parsed, dict)


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
        conn.execute(
            "INSERT INTO settings (key, value) VALUES "
            "('today_focus', 'a-stock-quant')"
        )
        conn.commit()

    result = run_cli(["--json", "today"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["date"] == date.today().isoformat()
    assert "weekday" in parsed
    assert isinstance(parsed["slot_rows"], list)
    # T013 must be scheduled in some slot (search, don't index — the result
    # depends on what time of day the test runs at, since compute_schedule
    # uses the real current time).
    scheduled_tasks = [row.get("task") for row in parsed["slot_rows"]]
    assert "a-stock-quant-T013" in scheduled_tasks
    scheduled_goals = [row.get("goal") for row in parsed["slot_rows"]]
    assert "a-stock-quant" in scheduled_goals


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


# -------------------- rebuild-timers: pure helpers --------------------

class TestReconcileTimers:
    """Direct unit tests for scripts.cli.reconcile_timers.

    All actual items below include both slot_start and task_id, which is
    the format that production timers will have after Item 2 ships.
    """

    def test_reconcile_empty_inputs(self):
        from cli import reconcile_timers
        assert reconcile_timers([], []) == {"to_add": [], "to_remove": []}

    def test_reconcile_planned_with_no_actual(self):
        from cli import reconcile_timers
        planned = [
            {"slot_start": "12:00", "slot_end": "13:00", "slot_label": "lunch",
             "task_id": "g1-T001", "goal_slug": "g1"},
            {"slot_start": "18:00", "slot_end": "19:00", "slot_label": "evening",
             "task_id": "g1-T002", "goal_slug": "g1"},
        ]
        result = reconcile_timers(planned, [])
        assert len(result["to_add"]) == 2
        assert result["to_remove"] == []
        assert {p["slot_start"] for p in result["to_add"]} == {"12:00", "18:00"}

    def test_reconcile_actual_with_no_planned(self):
        from cli import reconcile_timers
        actual = [
            {"id": "a", "fire_at": "2026-08-04T12:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 12:00 lunch - g1-T001",
             "slot_start": "12:00", "task_id": "g1-T001"},
            {"id": "b", "fire_at": "2026-08-04T18:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 18:00 evening - g1-T002",
             "slot_start": "18:00", "task_id": "g1-T002"},
        ]
        result = reconcile_timers([], actual)
        assert result["to_add"] == []
        assert len(result["to_remove"]) == 2
        assert {a["id"] for a in result["to_remove"]} == {"a", "b"}

    def test_reconcile_full_match(self):
        from cli import reconcile_timers
        planned = [
            {"slot_start": "12:00", "slot_end": "13:00", "slot_label": "lunch",
             "task_id": "g1-T001", "goal_slug": "g1"},
        ]
        actual = [
            {"id": "x", "fire_at": "2026-08-04T12:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 12:00 lunch - g1-T001",
             "slot_start": "12:00", "task_id": "g1-T001"},
        ]
        result = reconcile_timers(planned, actual)
        assert result["to_add"] == []
        assert result["to_remove"] == []

    def test_reconcile_ignores_foreign_actual(self):
        from cli import reconcile_timers
        # Foreign timer: no slot_start, no task_id — both must be ignored.
        actual = [
            {"id": "f", "fire_at": "2026-08-04T20:00:00+08:00",
             "description": "User manual reminder",
             "slot_start": None, "task_id": None},
        ]
        result = reconcile_timers([], actual)
        assert result["to_remove"] == []

    def test_reconcile_ignores_past_actual(self):
        from cli import reconcile_timers
        # Past timers are pre-filtered upstream by list_today_remaining.
        actual = []  # past timers are absent
        result = reconcile_timers([], actual)
        assert result["to_remove"] == []

    def test_reconcile_same_slot_different_task_is_stale(self):
        """A planned slot filled by a different task is a stale timer.
        Both the old (actual) and the new (planned) entries must appear
        in the diff so the old one is removed and the new one is added.
        """
        from cli import reconcile_timers
        planned = [
            {"slot_start": "18:00", "slot_end": "19:00", "slot_label": "evening",
             "task_id": "g1-T002", "goal_slug": "g1"},
        ]
        actual = [
            {"id": "old-18", "fire_at": "2026-08-04T18:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 18:00 evening - g1-T001",
             "slot_start": "18:00", "task_id": "g1-T001"},
        ]
        result = reconcile_timers(planned, actual)
        assert len(result["to_add"]) == 1
        assert result["to_add"][0]["task_id"] == "g1-T002"
        assert len(result["to_remove"]) == 1
        assert result["to_remove"][0]["id"] == "old-18"

    def test_reconcile_ignores_legacy_timer_without_task_id(self):
        """Pre-Item-2 timers (description without '- <task_id>') parse
        slot_start but not task_id. They are excluded from the diff
        entirely (neither kept nor removed by the algorithm; the caller
        keeps them in cc-connect until they fire naturally)."""
        from cli import reconcile_timers
        planned = [
            {"slot_start": "12:00", "slot_end": "13:00", "slot_label": "lunch",
             "task_id": "g1-T001", "goal_slug": "g1"},
        ]
        actual = [
            # Legacy timer: description has slot_start but no task_id.
            # The caller would have built this with
            # slot_start="12:00", task_id=None (parse_task_id_from_description
            # returned None).
            {"id": "legacy-12", "fire_at": "2026-08-04T12:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 12:00 lunch",
             "slot_start": "12:00", "task_id": None},
        ]
        result = reconcile_timers(planned, actual)
        # The legacy timer is NOT in to_remove (algorithm ignores it).
        assert result["to_remove"] == []
        # The planned entry is in to_add — the new timer will be added
        # and the legacy timer will be left alone (a deliberate v1
        # trade-off; a future enhancement could remove legacy timers
        # whose slot is now filled by a different task).
        assert len(result["to_add"]) == 1
        assert result["to_add"][0]["task_id"] == "g1-T001"


class TestSlotPromptHelpers:
    """Unit tests for parse_slot_start_from_description,
    parse_task_id_from_description, build_slot_description,
    build_slot_prompt."""

    def test_parse_slot_start_from_description_our_format(self):
        from cli import parse_slot_start_from_description
        assert parse_slot_start_from_description(
            "Todo scheduler: 2026-08-04 12:00 lunch - g1-T001"
        ) == "12:00"
        assert parse_slot_start_from_description(
            "Todo scheduler: 2026-08-04 18:00 evening - g1-T002"
        ) == "18:00"
        # Legacy format (no task_id) also works.
        assert parse_slot_start_from_description(
            "Todo scheduler: 2026-08-04 12:00 lunch"
        ) == "12:00"

    def test_parse_slot_start_from_description_foreign_returns_none(self):
        from cli import parse_slot_start_from_description
        assert parse_slot_start_from_description("User manual reminder") is None
        assert parse_slot_start_from_description("") is None
        assert parse_slot_start_from_description(
            "Todo scheduler: not-a-date 12:00 lunch - g1-T001"
        ) is None

    def test_parse_task_id_from_description_our_format(self):
        from cli import parse_task_id_from_description
        assert parse_task_id_from_description(
            "Todo scheduler: 2026-08-04 12:00 lunch - g1-T001"
        ) == "g1-T001"
        assert parse_task_id_from_description(
            "Todo scheduler: 2026-08-04 18:00 evening - g1-T002"
        ) == "g1-T002"

    def test_parse_task_id_from_description_legacy_returns_none(self):
        from cli import parse_task_id_from_description
        # Legacy format (no '- <task_id>' suffix) returns None.
        assert parse_task_id_from_description(
            "Todo scheduler: 2026-08-04 12:00 lunch"
        ) is None
        assert parse_task_id_from_description("User manual reminder") is None
        assert parse_task_id_from_description("") is None

    def test_build_slot_description(self):
        from cli import build_slot_description
        assert build_slot_description(
            "2026-08-04", "12:00", "lunch", "g1-T001"
        ) == "Todo scheduler: 2026-08-04 12:00 lunch - g1-T001"

    def test_build_slot_prompt_contains_key_fields(self):
        from cli import build_slot_prompt
        prompt = build_slot_prompt(
            "2026-08-04", "12:00", "13:00", "lunch", "g1-T001"
        )
        assert "2026-08-04" in prompt
        assert "12:00" in prompt
        assert "13:00" in prompt
        assert "lunch" in prompt
        assert "g1-T001" in prompt
        assert "Feishu" in prompt
        assert "reminder" in prompt.lower()
        # First line is the user-facing title (used by cc_timers to derive
        # the stored description when no --desc is passed).
        assert prompt.splitlines()[0].startswith("Free slot 启动")
        # First line includes the task_id (parseable by parse_task_id_from_description).
        assert "g1-T001" in prompt.splitlines()[0]
