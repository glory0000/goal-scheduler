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
