# Database Migrations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a forward-only SQL migration framework so future schema changes land in numbered `migrations/NNN_*.sql` files applied by `scripts/migrate.py`, with transactional safety and a single-row `schema_version` table.

**Architecture:** A standalone `scripts/migrate.py` (stdlib only, no `db.py` dependency) reads `TODO_DB_PATH` and `TODO_MIGRATIONS_DIR` env vars per invocation. `init` bootstraps the DB to v1 baseline; `upgrade` applies pending migration files in lexicographic order with one transaction per file.

**Tech Stack:** Python 3 stdlib (`sqlite3`, `argparse`, `re`, `pathlib`), pytest for testing, subprocess-based test isolation.

## Global Constraints

- **Stdlib only** — no Alembic, no third-party migration libraries. (Spec §2)
- **Two subcommands only:** `init` and `upgrade`. No flags beyond `--help`. (Spec §2)
- **Existing DB is v1 baseline.** No migration file applied for v1; `init` just stamps the row. (Spec §2)
- **Single-row version table** with columns `version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL`. (Spec §2)
- **Migration filename pattern:** `NNN_description.sql` where `NNN` is a three-digit zero-padded integer. Files not matching the pattern are skipped silently. (Spec §3)
- **Transactional apply:** one transaction per migration file; rollback on failure leaves `schema_version` at the prior value. (Spec §4 Flow 5, §5 row 5)
- **Idempotent:** `init` on a stamped DB exits 0 with current version; `upgrade` with no pending migrations exits 0. (Spec §4 Flow 4)
- **DB path env var:** `TODO_DB_PATH` (matches existing `db.py` convention). Migrations dir via `TODO_MIGRATIONS_DIR` (defaults to `<repo>/migrations/`). (Spec §2 + extension)
- **Subprocess-based tests:** tests invoke `migrate.py` as a subprocess so each test gets its own `TODO_DB_PATH`/`TODO_MIGRATIONS_DIR` without monkeypatching internals. (Spec §6)
- **`db.py init` continues to work** unchanged. Both paths bootstrap a fresh DB. (Spec §7)
- **No down-migrations.** Reverts are manual. (Spec §7)

---

### Task 1: Skeleton + `init` command (fresh + existing DB)

**Files:**
- Create: `migrations/.gitkeep`
- Create: `scripts/migrate.py`
- Create: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `TODO_DB_PATH` env var (default `data/todos.db`), `TODO_MIGRATIONS_DIR` env var (default `<repo>/migrations/`).
- Produces: `scripts/migrate.py init` — stamps `schema_version=1`; runs `data/schema.sql` only if `goals` table is absent.

- [ ] **Step 1: Create migrations directory with .gitkeep**

Create `migrations/.gitkeep` (empty file):

```bash
mkdir -p migrations
touch migrations/.gitkeep
```

- [ ] **Step 2: Write failing tests for `init` (fresh + existing DB)**

Create `tests/test_migrate.py`:

```python
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
```

- [ ] **Step 3: Run init tests and verify they fail**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: both tests fail because `scripts/migrate.py` does not exist yet.

- [ ] **Step 4: Implement `scripts/migrate.py` with `init` subcommand**

Create `scripts/migrate.py`:

```python
#!/usr/bin/env python3
"""Forward-only SQLite migration runner for data/todos.db.

Subcommands:
  init      Bootstrap DB and stamp schema_version=1.
  upgrade   Apply pending migrations from TODO_MIGRATIONS_DIR.

Env vars:
  TODO_DB_PATH          SQLite database file (default: data/todos.db).
  TODO_MIGRATIONS_DIR   Directory holding NNN_*.sql files
                        (default: <repo>/migrations/).
"""

import argparse
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _get_db_path() -> Path:
    return Path(os.environ.get("TODO_DB_PATH", "data/todos.db"))


def _get_migrations_dir() -> Path:
    custom = os.environ.get("TODO_MIGRATIONS_DIR")
    if custom:
        return Path(custom)
    return Path(__file__).resolve().parent.parent / "migrations"


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def cmd_init() -> int:
    db_path = _get_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if _table_exists(conn, "schema_version"):
            current = conn.execute("SELECT version FROM schema_version").fetchone()[0]
            print(f"DB already initialized at version {current}")
            return 0

        if not _table_exists(conn, "goals"):
            schema_path = Path(__file__).resolve().parent.parent / "data" / "schema.sql"
            conn.executescript(schema_path.read_text())

        conn.executescript(
            "CREATE TABLE schema_version ("
            "  version INTEGER PRIMARY KEY,"
            "  applied_at TEXT NOT NULL"
            ")"
        )
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (1, ?)",
            (_now_iso(),),
        )
        conn.commit()
        print("DB initialized at version 1")
        return 0
    finally:
        conn.close()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forward-only SQLite migration runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Bootstrap DB and stamp schema_version=1.")
    sub.add_parser("upgrade", help="Apply pending migrations from the migrations dir.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init()
    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable


if __name__ == "__main__":
    sys.exit(main())
```

Make the script executable:

```bash
chmod +x scripts/migrate.py
```

- [ ] **Step 5: Run init tests and verify they pass**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: both `test_init_creates_schema_version_on_fresh_db` and `test_init_stamps_baseline_on_existing_db` pass.

- [ ] **Step 6: Commit Task 1**

```bash
git add migrations/.gitkeep scripts/migrate.py tests/test_migrate.py
git commit -m "Add migrate.py skeleton with init subcommand"
```

---

### Task 2: `init` idempotency + `upgrade` guard + empty-migrations no-op

**Files:**
- Modify: `scripts/migrate.py`
- Modify: `tests/test_migrate.py`

**Interfaces:**
- Produces: `scripts/migrate.py upgrade` — must reject uninitialized DBs with exit 1 and a clear message; must report "no migrations to apply" when there are none.

- [ ] **Step 1: Append failing tests for idempotency and upgrade guard**

Append to `tests/test_migrate.py`:

```python
def test_init_is_idempotent(tmp_path):
    db_path = tmp_path / "twice.db"

    first = run_migrate(["init"], db_path=db_path)
    assert first.returncode == 0, first.stderr

    second = run_migrate(["init"], db_path=db_path)

    assert second.returncode == 0, second.stderr
    assert _read_schema_version(db_path) == 1
    assert "version 1" in second.stdout


def test_upgrade_rejects_db_without_init(tmp_path):
    db_path = tmp_path / "no_init.db"
    db_path.write_bytes(b"")  # exists but empty / not a SQLite DB

    result = run_migrate(["upgrade"], db_path=db_path)

    assert result.returncode == 1, result.stderr
    assert "init" in result.stderr.lower()


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
```

- [ ] **Step 2: Run new tests and verify they fail**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: the three new tests fail; `upgrade` subcommand is not implemented yet (argparse error), and the `init_is_idempotent` test may pass coincidentally if re-running prints the version, but the upgrade-related tests must fail.

- [ ] **Step 3: Implement `upgrade` skeleton (guard + no-op paths only)**

In `scripts/migrate.py`, replace `_build_parser` with:

```python
def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Forward-only SQLite migration runner.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", help="Bootstrap DB and stamp schema_version=1.")
    sub.add_parser("upgrade", help="Apply pending migrations from the migrations dir.")
    return parser
```

(unchanged — no new flags needed for the subcommand)

Add `cmd_upgrade` after `cmd_init`:

```python
_MIGRATION_FILENAME = re.compile(r"^(\d{3})_.+\.sql$")


def cmd_upgrade() -> int:
    db_path = _get_db_path()
    migrations_dir = _get_migrations_dir()

    if not db_path.exists():
        print(f"DB not found at {db_path}", file=sys.stderr)
        print("Run `migrate.py init` first", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        if not _table_exists(conn, "schema_version"):
            print("Run `migrate.py init` first", file=sys.stderr)
            return 1

        current = conn.execute("SELECT version FROM schema_version").fetchone()[0]

        if not migrations_dir.exists():
            print(f"No migrations to apply; already at version {current}")
            return 0

        pending = []
        for entry in sorted(migrations_dir.iterdir()):
            if not entry.is_file():
                continue
            m = _MIGRATION_FILENAME.match(entry.name)
            if m and int(m.group(1)) > current:
                pending.append((int(m.group(1)), entry))

        if not pending:
            print(f"No migrations to apply; already at version {current}")
            return 0

        # Apply pending migrations (Task 3 fills this in)
        for version, file_path in pending:
            sql = file_path.read_text()
            conn.executescript(sql)
            conn.execute(
                "UPDATE schema_version SET version = ?, applied_at = ?",
                (version, _now_iso()),
            )
        conn.commit()
        print(f"Migrations complete; now at version {pending[-1][0]}")
        return 0
    finally:
        conn.close()
```

Update `main` to dispatch `upgrade`:

```python
def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "init":
        return cmd_init()
    if args.command == "upgrade":
        return cmd_upgrade()
    parser.error(f"unknown command: {args.command}")
    return 2  # unreachable
```

- [ ] **Step 4: Run new tests and verify they pass**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: all 5 tests pass (2 from Task 1 + 3 new).

- [ ] **Step 5: Run full suite to confirm no regressions**

Run: `python -m pytest -q`
Expected: previous tests (db, scheduler, reminder, dashboard) all still pass; new migrate tests pass.

- [ ] **Step 6: Commit Task 2**

```bash
git add scripts/migrate.py tests/test_migrate.py
git commit -m "Add upgrade subcommand with init guard and no-op path"
```

---

### Task 3: Apply pending migrations in lexicographic order

**Files:**
- Modify: `tests/test_migrate.py`

**Interfaces:**
- Consumes: `cmd_upgrade()` already finds `pending` list in lexicographic order.
- Produces: verifies that 002 runs before 003, skips 002 when version=2.

- [ ] **Step 1: Append failing tests for ordering and skip-already-applied**

Append to `tests/test_migrate.py`:

```python
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
```

- [ ] **Step 2: Run new tests and verify ordering test fails**

Run: `python -m pytest tests/test_migrate.py -k "lexicographic or already_applied" -v`
Expected: tests pass (the implementation from Task 2 already handles lex order and skipping). If they fail, inspect the output and fix `cmd_upgrade()` until they pass.

If both pass immediately, that confirms Task 2's implementation already covers ordering; the commit message still captures the new test coverage.

- [ ] **Step 3: Run full migrate suite**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: 7 tests pass.

- [ ] **Step 4: Commit Task 3**

```bash
git add tests/test_migrate.py
git commit -m "Test upgrade applies migrations in lex order and skips applied"
```

---

### Task 4: Transactional rollback + data preservation

**Files:**
- Modify: `scripts/migrate.py`
- Modify: `tests/test_migrate.py`

**Interfaces:**
- Produces: `cmd_upgrade()` catches `sqlite3.Error` per migration, rolls back, re-raises; existing data tables untouched after upgrade.

- [ ] **Step 1: Append failing tests for rollback and data preservation**

Append to `tests/test_migrate.py`:

```python
def test_upgrade_rolls_back_on_failure(tmp_path):
    db_path = tmp_path / "rollback.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_bad.sql").write_text("THIS IS NOT VALID SQL")

    run_migrate(["init"], db_path=db_path)

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode != 0, upgrade_result.stdout
    assert _read_schema_version(db_path) == 1
    # No table named after the bad migration was created
    tables = _read_sqlite_table_names(db_path)
    assert "schema_version" in tables  # baseline preserved


def test_upgrade_does_not_touch_data_tables(tmp_path):
    db_path = tmp_path / "data_keep.db"
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    (migrations_dir / "002_add_column.sql").write_text(
        "ALTER TABLE tasks ADD COLUMN started_at TEXT"
    )

    # Init + seed data
    run_migrate(["init"], db_path=db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('g1', 'goal one', '', 'active', 1, 0, '2026-08-03T00:00:00', '2026-08-03T00:00:00')"
        )
        conn.execute(
            "INSERT INTO tasks (id, goal_slug, sequence, title, description, "
            "estimated_hours, depends_on, status, last_reminded_at, completed_at, "
            "created_at, updated_at) VALUES ('g1-T001', 'g1', 1, 't', '', 1.0, "
            "'[]', 'pending', NULL, NULL, '2026-08-03T00:00:00', '2026-08-03T00:00:00')"
        )
        conn.commit()

    upgrade_result = run_migrate(
        ["upgrade"], db_path=db_path, migrations_dir=migrations_dir
    )

    assert upgrade_result.returncode == 0, upgrade_result.stderr
    assert _read_schema_version(db_path) == 2
    with sqlite3.connect(str(db_path)) as conn:
        goal = conn.execute("SELECT name FROM goals WHERE slug='g1'").fetchone()
        task = conn.execute("SELECT title FROM tasks WHERE id='g1-T001'").fetchone()
        started_at = conn.execute(
            "SELECT started_at FROM tasks WHERE id='g1-T001'"
        ).fetchone()
    assert goal[0] == "goal one"
    assert task[0] == "t"
    assert started_at[0] is None  # new column present, but data untouched
```

- [ ] **Step 2: Run new tests and verify rollback test fails**

Run: `python -m pytest tests/test_migrate.py -k "rolls_back or does_not_touch" -v`
Expected: `test_upgrade_rolls_back_on_failure` fails — `cmd_upgrade()` does not currently catch `sqlite3.Error`. The data-preservation test may pass already because Task 2's implementation does not modify existing rows.

- [ ] **Step 3: Wrap migration apply in try/except with rollback**

In `scripts/migrate.py`, replace the body of the migration loop inside `cmd_upgrade`:

```python
        # Apply pending migrations one at a time; rollback on failure.
        for version, file_path in pending:
            sql = file_path.read_text()
            try:
                conn.executescript(sql)
                conn.execute(
                    "UPDATE schema_version SET version = ?, applied_at = ?",
                    (version, _now_iso()),
                )
            except sqlite3.Error as exc:
                conn.rollback()
                print(
                    f"Migration {file_path.name} failed: {exc}",
                    file=sys.stderr,
                )
                raise
        conn.commit()
        print(f"Migrations complete; now at version {pending[-1][0]}")
        return 0
```

Replace the existing loop in `cmd_upgrade` with this version. The `print` and `raise` keep the version pinned at `current` (because the failed `UPDATE` is rolled back) and propagate the exit code via the exception.

- [ ] **Step 4: Run new tests and verify they pass**

Run: `python -m pytest tests/test_migrate.py -v`
Expected: all 9 tests pass.

- [ ] **Step 5: Run full suite**

Run: `python -m pytest -q`
Expected: full suite green (db + scheduler + reminder + dashboard + 9 migrate tests).

- [ ] **Step 6: Commit Task 4**

```bash
git add scripts/migrate.py tests/test_migrate.py
git commit -m "Roll back failed migrations and preserve data tables"
```

---

### Task 5: Update README with migration usage

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add a "Migrations" section to README**

Read `README.md` first to find a good insertion point (after the existing "DB schema" or "Setup" section).

Insert (or merge into existing setup instructions) the following block:

```markdown
## Migrations

Schema changes land as numbered SQL files in `migrations/`:

```bash
python scripts/migrate.py init      # one-time: stamp schema_version=1
python scripts/migrate.py upgrade   # apply any pending migrations/
```

Add a new migration by creating `migrations/NNN_description.sql` where `NNN`
is the next three-digit version (e.g. `002_add_started_at.sql`). The runner
applies each file in a transaction; a failed file is rolled back and leaves
`schema_version` at the prior value.
```

- [ ] **Step 2: Commit README update**

```bash
git add README.md
git commit -m "Document migrate.py init and upgrade commands"
```

---

## Self-Review Checklist

After implementation, verify:

- [ ] All 9 spec-mandated tests exist in `tests/test_migrate.py` with the exact names from spec §6.
- [ ] No file outside the planned list was modified.
- [ ] `python scripts/db.py init` still works (regression — fresh DB still bootstraps via the existing path).
- [ ] `python -m pytest -q` reports green.
- [ ] README documents both subcommands.
- [ ] No `migrations/NNN_*.sql` files exist at v1 baseline (directory contains only `.gitkeep`).