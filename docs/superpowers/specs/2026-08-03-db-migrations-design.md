# Database Migrations — Design Spec

**Date:** 2026-08-03
**Status:** Design approved; written spec awaiting user review
**Author:** Claude (via brainstorming session)
**Parent project:** Todo Scheduler (`docs/superpowers/specs/2026-08-03-todo-scheduler-design.md`)

---

## 1. Purpose & Background

The Todo Scheduler database schema is defined by a single `data/schema.sql` that uses `CREATE TABLE IF NOT EXISTS`, so it is safe to re-apply on an existing database. The Web Dashboard and Claude operational scripts both add features that will eventually need new columns, indexes, or tables. Reusing `schema.sql` to land those changes would force users to choose between manual SQL execution and silent failure when tables already exist.

A migration framework gives every schema change a numbered file in `migrations/` that runs exactly once against a target database, in order, with a rollback boundary. Existing data is preserved; the framework stamps a baseline so that current databases do not require any migration to reach today's schema.

This is purely a developer/operator tool. It does not change runtime behavior of the dashboard or the reminder chain.

---

## 2. User Constraints

| Requirement | Decision |
|-------------|----------|
| Trigger | Explicit `python scripts/migrate.py <subcommand>` |
| File format | Numbered SQL files in `migrations/` |
| Version tracking | Single-row `schema_version(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)` |
| Existing DB baseline | Treated as v1 with no migration file applied |
| Subcommands | `init` and `upgrade` only |
| Architecture | Standalone `scripts/migrate.py` using `sqlite3` directly, no `db.py` dependency |
| Stdlib only | Continue to use Python stdlib only (no Alembic) |

---

## 3. Architecture Overview

### File Layout

```
todos/
├── migrations/                        # NEW
│   └── (empty at v1; 002+ added per change)
├── scripts/
│   ├── db.py                          # UNCHANGED
│   ├── scheduler.py                   # UNCHANGED
│   ├── reminder.py                    # UNCHANGED
│   ├── dump_state.sh                  # UNCHANGED
│   ├── simulate_reminder.sh           # UNCHANGED
│   ├── break_session.sh               # UNCHANGED
│   └── migrate.py                     # NEW
├── tests/
│   └── test_migrate.py                # NEW
└── data/
    ├── schema.sql                     # UNCHANGED: bootstrap for fresh DBs
    └── todos.db                       # UNCHANGED
```

### Components

| Component | Responsibility | Lines of code (est.) |
|-----------|----------------|----------------------|
| `migrate.py` | CLI entry, init/upgrade, schema_version table, file discovery, transactional apply | ~120 |
| `migrations/NNN_*.sql` | One per schema change, applied in order | as needed |
| `test_migrate.py` | Unit tests for init/upgrade, error paths | ~150 |

### Command Flow

```
python scripts/migrate.py init
  ↓
Read TODO_DB_PATH (default data/todos.db)
  ↓
Open SQLite connection (sqlite3.Row)
  ↓
Inspect schema_version table
  ├─ Exists → print current version, exit 0
  └─ Missing →
       ├─ Check `goals` table presence
       │   ├─ Exists (existing DB) → create schema_version(version=1)
       │   └─ Missing (fresh DB) → exec data/schema.sql, then create schema_version(version=1)
       └─ Print "DB initialized at version 1", exit 0

python scripts/migrate.py upgrade
  ↓
Read TODO_DB_PATH
  ↓
Open SQLite connection
  ↓
Read current version from schema_version
  ├─ Missing → print "Run `migrate.py init` first" and exit 1
  └─ Present →
       Scan migrations/NNN_*.sql files
       Filter NNN > current version, sort lexicographically
       For each pending migration:
         Open transaction
         ├─ executescript(file contents)
         ├─ update schema_version set version=NNN
         └─ commit
       Print list of applied migrations and final version, exit 0
```

### Version Storage

```sql
CREATE TABLE schema_version (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL
);
```

The table holds exactly one row under v1; future migrations update that single row to track the highest applied version.

### Migration File Naming

- Filename pattern: `NNN_description.sql` where `NNN` is a three-digit zero-padded integer (`002`, `003`, `010`, `099`).
- File content is plain SQL executed by `conn.executescript()`.
- No down-migration files (subcommands are init/upgrade only).
- `migrations/` starts empty; the v1 baseline is reached via `init`, not via a migration file.

---

## 4. Core Flows

### Flow 1: First-time setup on a fresh DB

```bash
python scripts/db.py init               # creates tables via data/schema.sql
python scripts/migrate.py init          # stamps schema_version=1
```

Or in a single step:

```bash
python scripts/migrate.py init          # detects no tables, runs schema.sql, stamps v1
```

### Flow 2: First-time setup on an existing DB (e.g. seeded example-goal)

```bash
python scripts/migrate.py init
# Detects existing tables, creates only schema_version, prints "DB initialized at version 1"
```

No data is migrated. `data/todos.db` is unchanged in its table contents.

### Flow 3: Apply a future migration

After landing `migrations/002_add_started_at.sql`:

```bash
python scripts/migrate.py upgrade
# Reads version=1, applies 002, sets version=2, prints "Applied 002, now at version 2"
```

### Flow 4: Re-running init or upgrade

- `init` on a stamped DB prints current version and exits 0 (idempotent).
- `upgrade` with no pending migrations prints "Already at version N" and exits 0.

### Flow 5: Recovery from a bad migration

If `migrations/002_add_started_at.sql` has a syntax error:

```bash
python scripts/migrate.py upgrade
# raises sqlite3.OperationalError, transaction rolls back, version stays at 1
# user fixes the SQL, re-runs upgrade
```

The DB never reaches a partially-migrated state.

---

## 5. Error Handling

| Scenario | Behavior |
|----------|----------|
| `init` when `schema_version` already exists | Print current version, exit 0 |
| `init` on fresh DB with `data/schema.sql` missing | Print error, exit 1 |
| `upgrade` before `init` | Print "Run `migrate.py init` first", exit 1 |
| `upgrade` with no `migrations/` directory | Print "No migrations to apply; already at version N", exit 0 |
| `upgrade` with one migration that fails | Roll back transaction, leave `schema_version` at the prior value, propagate exception, exit 1 |
| Migration filename not matching `NNN_*.sql` pattern | Skip silently (the convention is enforced by review, not by code) |
| `TODO_DB_PATH` not set and `data/todos.db` missing | Print error matching `FileNotFoundError`, exit 1 |
| `data/schema.sql` runs against a DB that already has the tables | Safe: every statement is `CREATE TABLE IF NOT EXISTS` / `CREATE INDEX IF NOT EXISTS` |

The migration runner does not invent DDL for missing tables. If a migration fails, the user must edit the SQL file and re-run `upgrade`. There is no auto-repair.

---

## 6. Testing Strategy

Tests live in `tests/test_migrate.py` and use a per-test temporary SQLite database (via `tmp_path` and `monkeypatch.setattr` on the DB path lookup inside `migrate.py`).

- `test_init_creates_schema_version_on_fresh_db` — empty DB → `init` creates tables from `data/schema.sql` and stamps version=1.
- `test_init_stamps_baseline_on_existing_db` — DB with `goals` table only → `init` leaves tables alone, stamps version=1.
- `test_init_is_idempotent` — `init` twice → second call prints current version, no error, no data change.
- `test_upgrade_rejects_db_without_init` — DB without `schema_version` → `upgrade` exits 1 with clear message.
- `test_upgrade_is_noop_when_no_pending_migrations` — version=1, empty `migrations/` → exit 0, no change.
- `test_upgrade_applies_pending_in_lexicographic_order` — version=1, files `002_a.sql` and `003_b.sql` in `migrations/` → both applied, version=3.
- `test_upgrade_skips_already_applied` — version=2, only `003_b.sql` present → 003 applied, version=3.
- `test_upgrade_rolls_back_on_failure` — file `002_bad.sql` with invalid SQL → exception raised, version remains 1, no table from 002 was created.
- `test_upgrade_does_not_touch_data_tables` — populate `goals`/`tasks` with seed data, run upgrade, assert rows are unchanged.

`TODO_DB_PATH` is set at collection time in the test module (before importing `migrate`) so importing the module does not touch `data/todos.db`. The migration runner reads `TODO_DB_PATH` from the environment on each command invocation, so the `monkeypatch.setenv` is unnecessary inside individual tests; per-test isolation comes from `tmp_path` plus a `run_migrate(cwd, db_path)` helper that sets the env var for the subprocess call.

---

## 7. Compatibility & Non-Goals

- `python scripts/db.py init` continues to work; both `db.py init` and `migrate.py init` are valid paths to bootstrap a fresh DB.
- The dashboard, reminder scripts, and Claude operational scripts are unchanged.
- No new runtime dependency is added (stdlib only).
- No downgrade path is provided. If a migration needs to be reverted, the user reverts by hand and re-applies.
- No automatic re-apply protection beyond `schema_version`. If a user edits a previously-applied SQL file, the runner will not notice. This is acceptable for a personal project; production tooling would store a file hash per applied version.

---

## 8. Acceptance Criteria

This design is accepted when:

1. User has approved all 5 design sections.
2. Spec file is committed.
3. User reviews the committed spec.
4. Implementation plan is written (via `writing-plans` skill).
5. Implementation lands with passing tests covering all flows above.
