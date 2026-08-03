"""Tests for scripts/migrate.py. Uses subprocess for full env isolation."""
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MIGRATE_SCRIPT = REPO_ROOT / "scripts" / "migrate.py"
DATA_SCHEMA = REPO_ROOT / "data" / "schema.sql"


def _read_sqlite_table_names(db_path: Path) -> set[str]:
    """Return the set of user-table names in db_path."""
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    return {r[0] for r in rows}


def _read_schema_version(db_path: Path) -> int | None:
    """Return the schema_version row, or None if no schema_version table."""
    with sqlite3.connect(str(db_path)) as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()
        if not row:
            return None
        return conn.execute("SELECT version FROM schema_version").fetchone()[0]


def run_migrate(
    args: list[str],
    db_path: Path,
    migrations_dir: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke scripts/migrate.py with isolated env vars."""
    env = os.environ.copy()
    env.pop("TODO_DB_PATH", None)
    env.pop("TODO_MIGRATIONS_DIR", None)
    env["TODO_DB_PATH"] = str(db_path)
    if migrations_dir is not None:
        env["TODO_MIGRATIONS_DIR"] = str(migrations_dir)
    return subprocess.run(
        [sys.executable, str(MIGRATE_SCRIPT)] + list(args),
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )


def test_init_creates_schema_version_on_fresh_db(tmp_path):
    db_path = tmp_path / "fresh.db"

    result = run_migrate(["init"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    tables = _read_sqlite_table_names(db_path)
    assert "schema_version" in tables
    assert "goals" in tables
    assert "tasks" in tables
    assert "settings" in tables
    assert _read_schema_version(db_path) == 1
    assert "version 1" in result.stdout


def test_init_stamps_baseline_on_existing_db(tmp_path):
    db_path = tmp_path / "existing.db"
    # Simulate an existing DB created by db.py init: has goals/tasks/settings
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(DATA_SCHEMA.read_text())

    result = run_migrate(["init"], db_path=db_path)

    assert result.returncode == 0, result.stderr
    tables = _read_sqlite_table_names(db_path)
    # schema_version added, but no goals/tasks data was touched
    assert "schema_version" in tables
    assert _read_schema_version(db_path) == 1
    assert "version 1" in result.stdout


def test_init_is_idempotent(tmp_path):
    db_path = tmp_path / "twice.db"

    first = run_migrate(["init"], db_path=db_path)
    assert first.returncode == 0, first.stderr

    second = run_migrate(["init"], db_path=db_path)

    assert second.returncode == 0, second.stderr
    assert _read_schema_version(db_path) == 1
    assert "version 1" in second.stdout


def test_upgrade_rejects_db_without_init(tmp_path):
    db_path = tmp_path / "uninitialized.db"
    db_path.write_bytes(b"")  # exists but empty / not a SQLite DB

    result = run_migrate(["upgrade"], db_path=db_path)

    assert result.returncode == 1, result.stderr
    assert "migrate.py init" in result.stderr


def test_upgrade_is_noop_when_no_pending_migrations(tmp_path):
    db_path = tmp_path / "v1.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()

    init_result = run_migrate(["init"], db_path=db_path)
    assert init_result.returncode == 0

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode == 0, upgrade_result.stderr
    assert _read_schema_version(db_path) == 1
    assert "no migrations to apply" in upgrade_result.stdout.lower()


def test_upgrade_applies_pending_in_lexicographic_order(tmp_path):
    db_path = tmp_path / "ordering.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    # Two migrations: 003 runs after 002.
    (migrations_dir / "002_add_started_at.sql").write_text(
        "CREATE TABLE started_at_marker (id INTEGER);"
    )
    (migrations_dir / "003_add_priority.sql").write_text(
        "CREATE TABLE priority_marker (id INTEGER);"
    )

    init_result = run_migrate(["init"], db_path=db_path)
    assert init_result.returncode == 0

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode == 0, upgrade_result.stderr
    assert _read_schema_version(db_path) == 3
    tables = _read_sqlite_table_names(db_path)
    assert "started_at_marker" in tables
    assert "priority_marker" in tables
    # 003 must appear after 002 in the output
    out = upgrade_result.stdout
    assert out.index("002_add_started_at.sql") < out.index("003_add_priority.sql")


def test_upgrade_skips_already_applied(tmp_path):
    db_path = tmp_path / "skip.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "003_only.sql").write_text(
        "CREATE TABLE only_003_marker (id INTEGER);"
    )

    # Pre-stamp version=2 directly so 003 is the only pending one.
    run_migrate(["init"], db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE schema_version SET version = 2")
        conn.commit()

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode == 0, upgrade_result.stderr
    assert _read_schema_version(db_path) == 3
    assert "only_003_marker" in {r[0] for r in
        sqlite3.connect(str(db_path)).execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
