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
from datetime import date, datetime
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


def run_cli(
    args: list[str],
    db_path: Path,
    timer_file: Path | None = None,
    now: datetime | None = None,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke scripts/cli.py with isolated TODO_DB_PATH and (optionally)
    TODO_TEST_TIMER_FILE. Existing tests that don't pass timer_file see
    the same behavior as before. Accepts an optional `now` parameter for
    freezing time in the subprocess (requires support in cli.py). Accepts
    an optional `cwd` parameter to change the subprocess's working directory
    (defaults to REPO_ROOT for backwards compatibility).

    To protect the tracked REPO_ROOT/goals/index.md from being overwritten
    by the auto-trigger fired from CRUD subcommands, each subprocess gets
    a unique TODO_GOALS_DIR pointing at a tempdir when no cwd is given,
    or at <cwd>/goals when cwd IS given. The tempdir is cleaned up after."""
    env = os.environ.copy()
    env.pop("TODO_DB_PATH", None)
    env["TODO_DB_PATH"] = str(db_path)
    if timer_file is not None:
        env["TODO_TEST_TIMER_FILE"] = str(timer_file)
    else:
        env.pop("TODO_TEST_TIMER_FILE", None)
    if now is not None:
        env["TEST_NOW_DATETIME"] = now.isoformat()

    cleanup_goals_root: Path | None = None
    if cwd is not None:
        env["TODO_GOALS_DIR"] = str(cwd / "goals")
    else:
        # Default: point the subprocess's GOALS_DIR at an isolated temp
        # directory. The subprocess's working directory remains REPO_ROOT
        # so other repo-root-relative paths (config/schedule.json, etc.)
        # still resolve as before. This temp dir does NOT touch the real
        # tracked goals/index.md.
        tmp_goals_root = Path(tempfile.mkdtemp(prefix="cli-test-goals-"))
        env["TODO_GOALS_DIR"] = str(tmp_goals_root)
        cleanup_goals_root = tmp_goals_root

    try:
        return subprocess.run(
            [sys.executable, str(CLI_SCRIPT), *args],
            cwd=str(cwd) if cwd is not None else str(REPO_ROOT),
            env=env,
            capture_output=True,
            text=True,
        )
    finally:
        if cleanup_goals_root is not None:
            shutil.rmtree(cleanup_goals_root, ignore_errors=True)


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
    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    def _load_cli_module(cls):
        import importlib.util as _ilu
        _cli_spec = _ilu.spec_from_file_location("cli", str(CLI_SCRIPT))
        _cli_module = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(_cli_module)
        sys.modules["cli"] = _cli_module
        yield

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
    @pytest.fixture(autouse=True, scope="class")
    @classmethod
    def _load_cli_module(cls):
        import importlib.util as _ilu
        _cli_spec = _ilu.spec_from_file_location("cli", str(CLI_SCRIPT))
        _cli_module = _ilu.module_from_spec(_cli_spec)
        _cli_spec.loader.exec_module(_cli_module)
        sys.modules["cli"] = _cli_module
        yield

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


# -------------------- rebuild-timers integration tests --------------------

import tempfile
import shutil

import freezegun


def _seed_focus_and_tasks(db_path: Path) -> None:
    """Insert one active goal with 4 pending tasks so scheduler has work to do.

    4 tasks because a weekday has 4 slots (07:30, 12:00, 18:00, 21:00); with
    3 tasks the morning 5am test would only get 3 timers, not 4."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('g1', 'Goal One', '', 'active', 4, 0, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        for seq, title, hours in [
            (1, "task one", 1.0),
            (2, "task two", 1.0),
            (3, "task three", 1.0),
            (4, "task four", 1.0),
        ]:
            tid = f"g1-T{seq:03d}"
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
                "estimated_hours, depends_on, status, last_reminded_at, "
                "completed_at, created_at, updated_at) "
                f"VALUES ('{tid}', 'g1', {seq}, '{title}', '', {hours}, '[]', "
                "'pending', NULL, NULL, '2026-08-04T00:00:00', '2026-08-04T00:00:00')"
            )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES ('today_focus', 'g1')"
        )
        conn.commit()


class TestRebuildTimers:
    """End-to-end subprocess tests for `python scripts/cli.py rebuild-timers`."""

    def test_rebuild_timers_db_uninitialized(self, tmp_path):
        db_path = tmp_path / "fresh.db"
        # Don't init the DB.
        result = run_cli(["rebuild-timers"], db_path=db_path,
                         timer_file=tmp_path / "timers.json")
        assert result.returncode == 2, result.stderr
        assert "Run `python scripts/db.py init` first" in result.stderr
        assert result.stdout == ""

    @freezegun.freeze_time("2026-08-04 05:00:00")
    def test_rebuild_timers_fresh_morning_5am(self, tmp_path):
        # 2026-08-04 is a Tuesday (weekday): 4 slots.
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        assert len(timers) == 4
        starts = sorted(t["fire_at"][11:16] for t in timers)
        assert starts == ["07:30", "12:00", "18:00", "21:00"]
        # Human summary appears on stdout
        assert "Rebuilt timers for 2026-08-04" in result.stdout
        assert "added   4" in result.stdout

    @freezegun.freeze_time("2026-08-04 14:00:00")
    def test_rebuild_timers_partial_day_14pm(self, tmp_path):
        # 14:00 is after the 12:00 slot. Only 18:00 and 21:00 should be added.
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        starts = sorted(t["fire_at"][11:16] for t in timers)
        assert starts == ["18:00", "21:00"]

    @freezegun.freeze_time("2026-08-04 05:00:00")
    def test_rebuild_timers_idempotent(self, tmp_path):
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        first = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)
        assert first.returncode == 0
        n_after_first = len(json.loads(timer_file.read_text(encoding="utf-8")))

        second = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)
        assert second.returncode == 0, second.stderr
        n_after_second = len(json.loads(timer_file.read_text(encoding="utf-8")))
        assert n_after_first == n_after_second == 4
        # Second-run stdout should report added=0 removed=0 kept=4
        assert "added   0" in second.stdout
        assert "removed 0" in second.stdout
        assert "kept    4" in second.stdout

    @freezegun.freeze_time("2026-08-04 05:00:00")
    def test_rebuild_timers_no_focus(self, tmp_path):
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        # Seed a goal but no focus setting.
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g1', 'G1', '', 'active', 0, 0, "
                "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
            )
            conn.commit()
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        assert "no focus set" in result.stdout
        assert not timer_file.exists() or json.loads(
            timer_file.read_text(encoding="utf-8") or "[]"
        ) == []

    @freezegun.freeze_time("2026-08-04 22:00:00")
    def test_rebuild_timers_late_night_22pm(self, tmp_path):
        # 22:00 is after the last slot (21:00-23:00). No remaining slots.
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        assert "no remaining slots today" in result.stdout
        if timer_file.exists():
            timers = json.loads(timer_file.read_text(encoding="utf-8") or "[]")
            assert timers == []
        else:
            # If the file doesn't exist, that's also acceptable
            pass

    @freezegun.freeze_time("2026-08-04 05:00:00")
    def test_rebuild_timers_json_output(self, tmp_path):
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        now = datetime.now()

        result = run_cli(
            ["--json", "rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now
        )

        assert result.returncode == 0, result.stderr
        # stderr is silent on success
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["date"] == "2026-08-04"
        assert payload["summary"] == {"added": 4, "removed": 0, "kept": 0, "ignored": 0}
        assert len(payload["added"]) == 4
        assert payload["removed"] == []
        assert payload["ignored_foreign"] == []

    @freezegun.freeze_time("2026-08-04 14:00:00")
    def test_rebuild_timers_stale_timer_removed(self, tmp_path):
        """If the timer file already has a 18:00 timer for an old task (T099)
        and the DB's 18:00 plan now resolves to T001, the old timer is removed
        and a new one is added."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        # Pre-seed: 18:00 timer for stale task T099, plus 21:00 timer for T002
        # (still matches the planner's choice for 21:00 at 14pm). Both must
        # have task_id-encoded descriptions so the (slot_start, task_id) diff
        # recognizes them.
        timer_file.write_text(json.dumps([
            {
                "id": "test-old-18",
                "fire_at": "2026-08-04T18:00:00+08:00",
                "description": "Todo scheduler: 2026-08-04 18:00 evening - g1-T099",
            },
            {
                "id": "test-21",
                "fire_at": "2026-08-04T21:00:00+08:00",
                "description": "Todo scheduler: 2026-08-04 21:00 night - g1-T002",
            },
        ], ensure_ascii=False, indent=2), encoding="utf-8")
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        # 21:00 was kept (slot_start + task_id T002 still in plan). 18:00 was
        # removed (stale T099) and re-added with a new id for T001; the
        # test-old-18 id should be gone.
        ids = {t["id"] for t in timers}
        assert "test-old-18" not in ids
        # 21:00 still present
        assert "test-21" in ids
        # 18:00 still present (just a different id, now for T001)
        starts = sorted(t["fire_at"][11:16] for t in timers)
        assert starts == ["18:00", "21:00"]
        # Stdout reports 1 removed + 1 added
        assert "removed 1" in result.stdout
        assert "added   1" in result.stdout

    @freezegun.freeze_time("2026-08-04 14:00:00")
    def test_rebuild_timers_only_manages_own_timers(self, tmp_path):
        """A foreign timer (description not 'Todo scheduler: ...') is left
        untouched even if it falls in today's window."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        timer_file.write_text(json.dumps([
            {
                "id": "foreign-1",
                "fire_at": "2026-08-04T20:00:00+08:00",
                "description": "User manual reminder",
            },
        ], ensure_ascii=False, indent=2), encoding="utf-8")
        now = datetime.now()

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file, now=now)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        ids = {t["id"] for t in timers}
        assert "foreign-1" in ids  # untouched
        # The two scheduled slots (18:00, 21:00) were also added.
        assert len(timers) == 3
        # Stdout mentions the foreign timer was ignored
        assert "ignored" in result.stdout
        assert "User manual reminder" in result.stdout


# -------------------- goal list/show/update/delete/restore --------------------

# ----- helpers -----

def _seed_goals(db_path: Path, goals_spec):
    """Seed goals directly into a DB. goals_spec is list of dicts."""
    _init_db(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        for g in goals_spec:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES (?, ?, '', ?, 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')",
                (g["slug"], g["name"], g["status"]),
            )
            (db_path.parent / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
            (db_path.parent / "goals" / g["slug"] / "goal.md").write_text(
                f"# {g['name']}\n", encoding="utf-8"
            )
        conn.commit()


# ----- TestGoalListCli -----

class TestGoalListCli:
    def test_default_hides_archived(self, tmp_path):
        """Default `goal list` shows active/paused/completed but excludes archived."""
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "alive", "name": "Alive", "status": "active"},
            {"slug": "paus", "name": "Paused", "status": "paused"},
            {"slug": "comp", "name": "Completed", "status": "completed"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "alive" in result.stdout
        assert "paus" in result.stdout
        assert "comp" in result.stdout
        assert "dead" not in result.stdout

    def test_all_includes_archived(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "alive", "name": "Alive", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list", "--all"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "alive" in result.stdout
        assert "dead" in result.stdout

    def test_status_query_filters(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "a1", "name": "A1", "status": "active"},
            {"slug": "a2", "name": "A2", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(["goal", "list", "--status", "archived"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "a1" not in result.stdout
        assert "dead" in result.stdout

    def test_status_wins_over_all(self, tmp_path):
        """I1: `--all --status archived` returns only archived, not all goals."""
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "alive", "name": "Alive", "status": "active"},
            {"slug": "dead", "name": "Dead", "status": "archived"},
        ])
        result = run_cli(
            ["goal", "list", "--all", "--status", "archived"],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "dead" in result.stdout
        assert "alive" not in result.stdout

    def test_json_output_shape(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [
            {"slug": "x", "name": "X", "status": "active"},
        ])
        result = run_cli(["goal", "list", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["slug"] == "x"


# ----- TestGoalShowCli -----

class TestGoalShowCli:
    def test_show_existing(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "show", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "x" in result.stdout
        assert "active" in result.stdout

    def test_show_json(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "show", "x", "--json"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert data["slug"] == "x"
        assert data["status"] == "active"

    def test_show_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "show", "missing"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "missing" in result.stderr


# ----- TestGoalUpdateCli -----

class TestGoalUpdateCli:
    def test_update_status_triggers_sync(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "paused"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # sync-md auto-trigger wrote index.md with new status
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "已暂停" in index
        assert "[X](x/goal.md)" in index

    def test_update_to_archived_rejected(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "archived"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "delete" in result.stderr  # hint pointing to goal delete

    def test_update_noop_no_sync(self, tmp_path):
        """Updating to the same status should not write index.md (idempotent reapply).

        Uses mtime+size comparison: sync_index_md preserves arbitrary header
        content, so a textual sentinel ("SENTINEL still in body") would pass
        even if the sync fired. St_mtime_ns + st_size only stay put if no
        write happened at all.
        """
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        index_path = tmp_path / "goals" / "index.md"
        # Pre-create index.md with a sentinel; capture its stat fingerprint.
        index_path.write_text(
            "# SENTINEL — must remain unchanged\n", encoding="utf-8"
        )
        before = index_path.stat()
        result = run_cli(["goal", "update", "x", "--status", "active"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        after = index_path.stat()
        assert (after.st_mtime_ns, after.st_size) == (
            before.st_mtime_ns, before.st_size
        ), "noop `goal update` must not re-render index.md"

    def test_update_missing_exits_2(self, tmp_path):
        """M4: updating a missing slug must exit 2 (no silent no-op)."""
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "update", "nope", "--status", "paused"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "nope" in result.stderr

    def test_update_invalid_status_rejected(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "update", "x", "--status", "bogus"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestGoalDeleteCli -----

class TestGoalDeleteCli:
    def test_delete_archives_and_syncs(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # index.md must no longer contain the deleted goal
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" not in index
        # goal.md still on disk (soft delete preserves file)
        assert (tmp_path / "goals" / "x" / "goal.md").exists()

    def test_delete_idempotent_no_sync(self, tmp_path):
        """Second delete on an already-archived goal must not re-render index.md.

        Uses mtime+size comparison: sync_index_md preserves arbitrary header
        content, so a textual sentinel would survive a re-render and the
        assertion would pass even if the sync fired. st_mtime_ns + st_size
        only stay put if no write happened at all.
        """
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        # First delete archives
        r1 = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert r1.returncode == 0
        index_path = tmp_path / "goals" / "index.md"
        # Pre-write sentinel and capture its stat fingerprint.
        index_path.write_text(
            "# SENTINEL — second delete must not touch this\n", encoding="utf-8"
        )
        before = index_path.stat()
        # Second delete: already archived, no-op, no sync
        r2 = run_cli(["goal", "delete", "x"], db_path=db_path, cwd=tmp_path)
        assert r2.returncode == 0
        after = index_path.stat()
        assert (after.st_mtime_ns, after.st_size) == (
            before.st_mtime_ns, before.st_size
        ), "noop `goal delete` must not re-render index.md"

    def test_delete_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "delete", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestGoalRestoreCli -----

class TestGoalRestoreCli:
    def test_restore_archived_to_active(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "archived"}])
        result = run_cli(["goal", "restore", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "[X](x/goal.md)" in index
        assert "进行中" in index

    def test_restore_non_archived_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _seed_goals(db_path, [{"slug": "x", "name": "X", "status": "active"}])
        result = run_cli(["goal", "restore", "x"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "not archived" in result.stderr

    def test_restore_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["goal", "restore", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestTaskListCli -----

class TestTaskListCli:
    def _seed(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            for g in [{"slug": "g1", "name": "G1"}, {"slug": "g2", "name": "G2"}]:
                conn.execute(
                    "INSERT INTO goals (slug, name, description, status, "
                    "total_tasks, completed_tasks, created_at, updated_at) "
                    "VALUES (?, ?, '', 'active', 0, 0, '2026-08-05T00:00:00', "
                    "'2026-08-05T00:00:00')",
                    (g["slug"], g["name"]),
                )
                (tmp_path / "goals" / g["slug"]).mkdir(parents=True, exist_ok=True)
                (tmp_path / "goals" / g["slug"] / "goal.md").write_text(
                    f"# {g['name']}\n", encoding="utf-8"
                )
            tasks = [
                ("g1-T001", "g1", 1, "pending", "pending task"),
                ("g1-T002", "g1", 2, "done", "done task"),
                ("g2-T001", "g2", 1, "pending", "another pending"),
                ("g1-T003", "g1", 3, "archived", "archived task"),
            ]
            for tid, slug, seq, status, title in tasks:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, '', 1.0, '[]', ?, NULL, NULL, "
                    "'2026-08-05T00:00:00', '2026-08-05T00:00:00')",
                    (tid, slug, seq, title, status),
                )
            conn.commit()
        return db_path

    def test_default_hides_archived(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "g1-T001" in result.stdout
        assert "g1-T002" in result.stdout
        assert "g1-T003" not in result.stdout  # archived excluded

    def test_all_includes_archived(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--all"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T003" in result.stdout

    def test_goal_filter(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--goal", "g1"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T001" in result.stdout
        assert "g2-T001" not in result.stdout

    def test_status_query(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--status", "done"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T002" in result.stdout
        assert "g1-T001" not in result.stdout

    def test_status_archived_is_selectable(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--status", "archived"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "g1-T003" in result.stdout
        assert "g1-T001" not in result.stdout

    def test_status_wins_over_all(self, tmp_path):
        """I1: `--all --status archived` returns only archived tasks, not all."""
        db_path = self._seed(tmp_path)
        result = run_cli(
            ["task", "list", "--all", "--status", "archived"],
            db_path=db_path, cwd=tmp_path,
        )
        assert result.returncode == 0
        assert "g1-T003" in result.stdout
        assert "g1-T001" not in result.stdout
        assert "g1-T002" not in result.stdout

    def test_json_output(self, tmp_path):
        db_path = self._seed(tmp_path)
        result = run_cli(["task", "list", "--json"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert isinstance(data, list)
        # --json honours the same archived-hiding default as text output
        assert {t["id"] for t in data} == {"g1-T001", "g1-T002", "g2-T001"}

    def test_empty_list_reports_no_tasks(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "list"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0
        assert "(no tasks)" in result.stdout


# ----- TestTaskShowCli -----

class TestTaskShowCli:
    def _seed_one(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, 'hello task', '', 1.0, '[]', "
                "'pending', NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        return db_path

    def test_show_existing(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "show", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        assert "g-T001" in result.stdout
        assert "hello task" in result.stdout
        # raw status key and its label are both shown (mirrors `goal show`)
        assert "pending" in result.stdout
        assert "待办" in result.stdout

    def test_show_json(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "show", "g-T001", "--json"],
                         db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        data = json.loads(result.stdout)
        assert data["id"] == "g-T001"
        assert data["status"] == "pending"

    def test_show_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "show", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestTaskDeleteCli -----

class TestTaskDeleteCli:
    def _seed_one(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            for tid, status in [("g-T001", "pending"), ("g-T002", "done")]:
                conn.execute(
                    "INSERT INTO tasks (id, goal_slug, sequence, title, "
                    "description, estimated_hours, depends_on, status, "
                    "last_reminded_at, completed_at, created_at, updated_at) "
                    "VALUES (?, 'g', ?, '', '', 1.0, '[]', ?, NULL, NULL, "
                    "'2026-08-05T00:00:00', '2026-08-05T00:00:00')",
                    (tid, 1 if tid.endswith("001") else 2, status),
                )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        return db_path

    def test_delete_archives_and_updates_pct(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        # Archived tasks still count toward the index.md denominator, so the
        # goal reads 1 done / 2 total = 50%.
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        assert "完成率 50%" in index

    def test_delete_sets_status_archived(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        result = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = 'g-T001'"
            ).fetchone()
        assert row[0] == "archived"

    def test_delete_idempotent(self, tmp_path):
        db_path = self._seed_one(tmp_path)
        r1 = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert r1.returncode == 0
        index_path = tmp_path / "goals" / "index.md"
        # A no-op delete must not rewrite index.md at all. Asserting on the
        # file's content is not enough: sync_index_md preserves whatever
        # header it finds, so a sentinel would survive a re-render and the
        # assertion would pass even if the sync fired. Compare mtime+size,
        # which only stay put if no write happened.
        before = index_path.stat()
        r2 = run_cli(["task", "delete", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert r2.returncode == 0
        assert "already archived" in r2.stdout
        after = index_path.stat()
        assert (after.st_mtime_ns, after.st_size) == (
            before.st_mtime_ns, before.st_size
        ), "no-op `task delete` must not re-render index.md"

    def test_delete_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "delete", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2


# ----- TestTaskRestoreCli -----

class TestTaskRestoreCli:
    def test_restore_archived_to_pending(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, '', '', 1.0, '[]', 'archived', "
                "NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        (tmp_path / "goals" / "g").mkdir(parents=True, exist_ok=True)
        (tmp_path / "goals" / "g" / "goal.md").write_text("# G\n", encoding="utf-8")
        result = run_cli(["task", "restore", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 0, result.stderr
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT status FROM tasks WHERE id = 'g-T001'"
            ).fetchone()
        assert row[0] == "pending"
        index = (tmp_path / "goals" / "index.md").read_text(encoding="utf-8")
        # 0 of 1 done = 0% — but the goal is back in the active group
        assert "[G](g/goal.md)" in index
        assert "完成率 0%" in index

    def test_restore_non_archived_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                "INSERT INTO goals (slug, name, description, status, "
                "total_tasks, completed_tasks, created_at, updated_at) "
                "VALUES ('g', 'G', '', 'active', 0, 0, '2026-08-05T00:00:00', "
                "'2026-08-05T00:00:00')"
            )
            conn.execute(
                "INSERT INTO tasks (id, goal_slug, sequence, title, "
                "description, estimated_hours, depends_on, status, "
                "last_reminded_at, completed_at, created_at, updated_at) "
                "VALUES ('g-T001', 'g', 1, '', '', 1.0, '[]', 'pending', "
                "NULL, NULL, '2026-08-05T00:00:00', '2026-08-05T00:00:00')"
            )
            conn.commit()
        result = run_cli(["task", "restore", "g-T001"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
        assert "not archived" in result.stderr

    def test_restore_missing_exits_2(self, tmp_path):
        db_path = tmp_path / "todos.db"
        _init_db(db_path)
        result = run_cli(["task", "restore", "nope"], db_path=db_path, cwd=tmp_path)
        assert result.returncode == 2
