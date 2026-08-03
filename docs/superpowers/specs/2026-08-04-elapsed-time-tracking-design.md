# Task Elapsed Time Tracking — Design Spec

**Date:** 2026-08-04
**Status:** Approved (brainstorming complete; awaiting spec review and writing-plans)

## 1. Purpose & Background

The todo scheduler currently has `created_at`, `updated_at`, `last_reminded_at`, and `completed_at` on each task, but no record of when a task was first picked up. Once a task moves to `in_progress`, the user cannot tell how long it has been running. This makes it hard to spot stuck tasks ("started 3 days ago, still going") and makes the dashboard and reminder messages feel static.

This spec adds first-start elapsed-time tracking to tasks, surfaced in three places: the goal detail view, the today timeline, and the Feishu reminder message.

## 2. User Constraints (from brainstorming)

| Decision | Value |
|---|---|
| Start semantics | **First in_progress**: stamp `started_at` only on the *first* transition into `in_progress`. Re-entering `in_progress` does not reset. |
| Display format | **Compact auto**: `Ns` (< 60s) / `Xm Ys` (< 1h) / `Xh Ym` (< 24h) / `Xd Yh` (≥ 24h); `—` when `started_at` is NULL. |
| Display locations | Dashboard `/goal/<slug>` (new columns), Dashboard `/today` (suffix), Feishu reminder message (suffix). **Excluded:** `goals/<slug>/goal.md` (would require additional accumulation semantics not chosen for v1). |

## 3. Architecture Overview

A single nullable column `tasks.started_at TEXT` (ISO timestamp) introduced via the new migrations framework. The DB-layer status update is taught to stamp the column on first transition into `in_progress`. A small new module `scripts/format_utils.py` exports `format_elapsed(started_at, completed_at=None) -> str`, used by the dashboard and reminder.

The migration framework (`scripts/migrate.py`, `migrations/002_add_started_at.sql`) is the canonical schema-change path going forward; this is the first user of the framework for a real schema change.

```
Claude  ───►  scripts/db.py::update_task_status  ──►  tasks.started_at
                │                                       │
                │ (in_progress)                         ▼
                └──► UPDATE tasks SET started_at = COALESCE(started_at, now())

Dashboard /goal/<slug>  ─┐
Dashboard /today        ├──► scripts/format_utils.format_elapsed  ──► display
Reminder message        ─┘
```

## 4. Core Flows

### 4.1 Schema migration

`migrations/002_add_started_at.sql`:
```sql
ALTER TABLE tasks ADD COLUMN started_at TEXT;
```

Run on a v1 DB: `python scripts/migrate.py upgrade` — `002_add_started_at.sql` is the only pending file; after apply, `schema_version` advances to 2 and existing tasks retain `started_at = NULL`.

### 4.2 Status update semantics

`update_task_status(id, status)` in `scripts/db.py` is updated:

| Transition | SQL |
|---|---|
| `*` → `in_progress` | `UPDATE tasks SET status=?, started_at = COALESCE(started_at, ?), updated_at=? WHERE id=?` |
| `*` → `done` | unchanged: `UPDATE tasks SET status=?, completed_at=?, updated_at=? WHERE id=?` |
| `*` → `pending` / `*` → `skipped` | `UPDATE tasks SET status=?, updated_at=? WHERE id=?` (do not touch `started_at` or `completed_at`) |

Notes:
- `COALESCE(started_at, ?)` implements the first-start semantic in SQL: if the column is already non-NULL, the existing value is preserved.
- The function does not validate or modify `completed_at` outside the `done` transition. The previous value (if any) is preserved on revert from `done` → `in_progress` per the user-chosen first-start semantics.

### 4.3 Elapsed formatting

`scripts/format_utils.py`:
```python
def format_elapsed(started_at: str | None, completed_at: str | None = None) -> str
```

Algorithm:
1. If `started_at` is `None`, return `—`.
2. Compute `end = completed_at or now_iso()`.
3. Parse both as ISO timestamps; raise `ValueError` if either is unparseable.
4. Compute `seconds = max(0, (end - start).total_seconds())`.
5. Branch by magnitude:
   - `seconds < 60` → `f"{int(seconds)}s"`
   - `seconds < 3600` → `f"{minutes}m {int(seconds % 60)}s"`
   - `seconds < 86400` → `f"{hours}h {int((seconds % 3600) // 60)}m"`
   - else → `f"{days}d {int((seconds % 86400) // 3600)}h"`

`now_iso()` is imported from `scripts/db.py` so the formatting uses the same clock as the rest of the codebase. (Acceptable: `format_utils` is allowed to depend on `db.py` because both are application code; the reverse dependency — `db.py` on `format_utils` — is not introduced.)

### 4.4 Display rules

| Context | Rule |
|---|---|
| `/goal/<slug>` table | Add columns `Started` (`started_at` as `YYYY-MM-DD HH:MM`, `—` if NULL) and `Elapsed` (`format_elapsed(started_at, completed_at)`, using `completed_at` for `done` tasks, NULL for others). |
| `/today` timeline | For each `in_progress` task, append `（已用 {format_elapsed(started_at)}）` to the task label. Other statuses unchanged. |
| Reminder message | Same suffix `（已用 {format_elapsed(started_at)}）` for `in_progress` tasks in `reminder.format_reminder`. |

The `done` case in `/today` and the reminder message does **not** append a suffix, because by the time a task is `done` it is no longer the active reminder target. (If the user later asks to show "X 已完成（用时 Yh Ym）", that is a separate enhancement.)

## 5. Error Handling

| Situation | Behavior |
|---|---|
| `migrate.py upgrade` finds no `002_add_started_at.sql` | No-op (idempotent). |
| `migrate.py upgrade` applied previously; rerun | No-op (existing tasks already have `started_at` column; `002` is no longer pending). |
| `started_at` exists but is malformed | `format_elapsed` raises `ValueError`. Callers in dashboard and reminder wrap in try/except and render `—` on failure (defensive — invalid timestamps should not crash a view). |
| `format_elapsed` called with `completed_at` earlier than `started_at` (clock skew) | `max(0, delta)` clamps to zero, so output is `0s`. No exception. |
| `update_task_status` called with a non-mapped status | Existing `ValueError` from the function is preserved. |
| Migration apply on a v0 (no `schema_version`) DB | Existing `upgrade` guard rejects with exit 1 and message "Run `migrate.py init` first" — unchanged. |

## 6. Testing Strategy

All tests use pytest; no new dependencies.

**`tests/test_format_utils.py`** (new, ~6 tests):
- `format_elapsed(None)` → `"—"`
- `format_elapsed("2026-08-04T07:30:00", completed_at="2026-08-04T07:30:45")` → `"45s"`
- `< 1h` boundary → `"59m 59s"`
- `< 1h` boundary (1m 5s) → `"1m 5s"`
- `format_elapsed(start, completed_at=start + 1h23m)` → `"1h 23m"`
- `>= 1d` → `"2d 5h"`
- malformed input → `ValueError`

**`tests/test_db.py`** (extend, ~3 new tests):
- `pending → in_progress` stamps `started_at`
- `in_progress → done` preserves `started_at`; sets `completed_at`
- `in_progress → pending → in_progress` does not update `started_at` (COALESCE)
- Reverting `done → in_progress` does not clear `started_at` or change it

**`tests/test_migrate.py`** (extend, ~2 new tests):
- Apply `002_add_started_at.sql` on a v1 DB → schema_version becomes 2, `tasks.started_at` column exists
- Re-run `migrate.py upgrade` after `002` is applied → no-op, schema_version stays 2

**`tests/test_dashboard.py`** (extend, ~2 new tests):
- `test_goal_detail_shows_started_and_elapsed_columns`: assert response contains `Started`, `Elapsed`, and the formatted string
- `test_today_timeline_appends_elapsed_suffix_for_in_progress`: assert response contains `（已用` for an `in_progress` task, but not for a `pending` one

**`tests/test_reminder.py`** (extend, ~1 new test):
- `test_reminder_message_appends_elapsed_for_in_progress`: format a reminder for an `in_progress` task with `started_at`; assert `（已用` appears; for a `pending` task, assert it does not.

## 7. Compatibility & Non-Goals

**Compatibility:**
- Existing tasks in production DBs have `started_at = NULL` after migration. Dashboard and reminder render `—` for them — no crash, no surprise.
- `python scripts/db.py init` continues to work. Both bootstrap paths produce a v1 DB.
- `migrate.py` is a no-op for already-migrated DBs.
- Reminder and dashboard changes are purely additive (new columns / appended suffix) — existing clients see the new info.

**Non-goals (explicitly out of scope):**
- Tracking time across `pending` ↔ `in_progress` cycles (e.g. "actual work time" excluding the `pending` periods) — would require an additional `accumulated_seconds` column and was rejected as v2.
- Surfacing elapsed time in `goals/<slug>/goal.md` (would require a sum across in-progress tasks) — not in this spec.
- Manually resetting `started_at` via a Claude command — no UI for v1.
- Changing the Feishu reminder text beyond appending the suffix.

## 8. Acceptance Criteria

1. `python scripts/migrate.py upgrade` on a v1 DB applies `002_add_started_at.sql` and advances `schema_version` to 2.
2. `update_task_status(id, "in_progress")` on a task with `started_at = NULL` stamps the current time; on a task with `started_at` already set, leaves it unchanged.
3. Dashboard `/goal/<slug>` shows `Started` and `Elapsed` columns for every task. Existing tasks show `—` in both.
4. Dashboard `/today` shows `（已用 Xh Ym）` after each `in_progress` task's label.
5. `reminder.format_reminder(...)` output contains `（已用 Xh Ym）` for `in_progress` tasks, and does not for `pending` tasks.
6. `python -m pytest -q` reports green (62 tests = 52 prior + ~10 new).
7. No file outside the planned list (`scripts/db.py`, `scripts/format_utils.py`, `scripts/reminder.py`, `dashboard/app.py`, `dashboard/templates/goal_detail.html`, `dashboard/templates/today.html`, `migrations/002_add_started_at.sql`, `tests/test_format_utils.py`, plus extensions to existing test files) is modified.
