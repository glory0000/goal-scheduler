# CRUD Completion (list/show/update/delete/restore) — Design Spec

**Date:** 2026-08-05
**Status:** Approved (brainstorming complete; awaiting spec review and writing-plans)

## 1. Purpose & Background

The current CLI (`scripts/cli.py`) only implements `goal add` and `task add` / `task update`. There is no way to list, inspect, change goal status, or remove anything via the CLI — those gaps have been explicitly deferred (Item 5 sync-md spec §6 non-goals; Item 1 §7 destructive operations).

This forces Claude to maintain `goals/index.md` by hand whenever a goal needs to be paused, completed, or removed — exactly the failure mode Item 5 was built to eliminate.

This spec adds the missing CRUD surface to the CLI (and minimal read-only dashboard updates), with soft-delete semantics so the operation is reversible, and wires the new write paths into `_autosync_index_md()` so `goals/index.md` stays in sync without manual intervention.

## 2. User Constraints (from brainstorming)

| Decision | Value |
|---|---|
| Delete semantics | **Soft delete** — sets `status='archived'`, not row removal |
| Task archive status | New task status `'archived'` (5th value, alongside pending/in_progress/done/skipped) |
| Goal archive status | New goal status `'archived'` (4th value, alongside active/paused/completed) |
| Update scope | `goal update <slug> --status <new>` — status only, no name/description/hours via CLI in v1 |
| Restore scope | `archived → active` (goal) / `archived → pending` (task); restore does not accept other target statuses in v1 |
| Restore idempotency | Non-archived `goal restore` / `task restore` → exit 2 (error) |
| List default | Hide archived by default; `--all` shows everything; `--status <key>` filters to one status |
| `goal update --status archived` | **Rejected** with exit 2; must use `goal delete` instead (avoids accidental archiving via update) |
| Sync-md archived behavior | Archived goals are excluded from `goals/index.md` rendering; archived goals whose `goal.md` is missing do NOT generate a warning (avoids post-archive noise) |
| Scheduler archived behavior | `list_eligible_tasks` filters out archived tasks; archived goals produce no eligible tasks |
| Dashboard scope | **Read-only, minimal**: index page adds archived filter + link; goal detail adds archived banner; new `/task/<id>` route. No POST routes, no update/delete buttons. |
| `rebuild-timers` archived behavior | Automatic via existing `reconcile_timers`: archived goals yield no planned slots, existing cc-connect timers get removed |
| CLI framework | `argparse` (consistent with Items 1 / 2 / 5) |
| Output format | Human-readable default with `--json` flag (consistent) |
| Exit codes | `0` = success, `1` = argparse input error, `2` = DB uninit / not found / illegal status |
| Test isolation | Reuses Item 5's `TODO_GOALS_DIR` env-overridable setup; no new infrastructure |
| Dependencies | None new. Stdlib only. |

## 3. Architecture Overview

Five files change (one new module-API surface, two existing files extended):

1. **`scripts/db.py`** — 4 new thin wrappers around the existing `update_*_status` primitives: `archive_goal`, `archive_task`, `restore_goal`, `restore_task`. No schema change. The DB layer remains the single source of truth for status semantics.

2. **`scripts/cli.py`** — 7 new subcommand bodies (4 for goal, 3 for task) + parser registrations + dispatch branches. The 4 mutating subcommands (`goal update` when status changes, `goal delete`, `goal restore`, `task delete`, `task restore`) call `_autosync_index_md()` after success. Note: `goal update` does NOT trigger sync when status is unchanged (idempotent reapply), matching the existing `task update` gate.

3. **`scripts/sync_md.py`** — `STATUS_LABELS` gains `"archived": "已归档"`. `_GROUP_ORDER` is unchanged (archived is never rendered into a `## <label>` group). `_group_and_sort` already excludes archived (because archived isn't in `_GROUP_ORDER`), so the render loop needs no change. `sync_index_md`'s `by_status` counter excludes archived. The "missing goal.md" warning is suppressed for archived goals.

4. **`scripts/scheduler.py`** — `list_eligible_tasks` skips tasks with `status='archived'` alongside the existing pending/in_progress filter.

5. **`dashboard/app.py` + `dashboard/templates/`** — read-only updates: index page gains `?all=1` / `?status=archived` filters; goal detail page shows an archived banner; new `/task/<id>` route + `task_detail.html` template.

**Reuse boundary:**
- `migrate.py` — **unchanged**. No schema change.
- `cli_output.py` — **unchanged**. The new subcommands use the same `_emit_error` / `to_json` conventions as siblings.
- `format_utils.py` / `reminder.py` — **unchanged**.
- `_autosync_index_md()` — **unchanged** signature; just called from 4 more sites.
- `cc_timers.py` — **unchanged**.

**Data flow on one `goal delete <slug>` call:**

```
CLI goal delete <slug>
    ↓ _validate_slug(slug)
    ↓ _require_initialized_db()
    ↓ db.archive_goal(slug)                    [UPDATE goals SET status='archived']
    ↓ _autosync_index_md()                     [re-render goals/index.md]
    ↓ stdout: human confirmation OR --json dict
```

## 4. Core Surfaces

### 4.1 db.py additions

```python
def archive_goal(slug: str) -> bool:
    """Set status='archived'. Returns True if changed, False if already archived (idempotent no-op)."""

def archive_task(task_id: str) -> bool:
    """Set status='archived'. Returns True if changed, False if already archived (idempotent no-op)."""

def restore_goal(slug: str) -> None:
    """Restore archived → active. Raises ValueError if not currently archived."""

def restore_task(task_id: str) -> None:
    """Restore archived → pending. Raises ValueError if not currently archived."""
```

`archive_*` return `bool` so the CLI can distinguish "actually changed" (sync-md needed) from "already archived" (no sync-md needed). Implementation reuses `update_goal_status` / `update_task_status` under the hood.

### 4.2 sync_md.py additions

- `STATUS_LABELS["archived"] = "已归档"` (new key, not in `_GROUP_ORDER`)
- `_group_and_sort` unchanged (archived already excluded by virtue of not being in `_GROUP_ORDER`)
- `sync_index_md` post-filter: drop any `goal["status"] == "archived"` rows before computing `by_status`
- `sync_index_md` warning suppression: skip the "goal has no goals/<slug>/goal.md" warning when the goal's status is archived

### 4.3 CLI subcommands

#### `goal` family (extends existing `goal add`)

| Subcommand | Body (key operations) | Triggers `_autosync_index_md`? |
|---|---|---|
| `goal add <slug> <name> [--description]` | Existing (unchanged) | Yes (already wired) |
| `goal list [--status X] [--all] [--json]` | New: call `db.list_goals(status=...)`; if neither `--all` nor `--status archived` is passed, force `status="active"` (i.e., exclude archived by default). | No |
| `goal show <slug> [--json]` | New: call `db.get_goal(slug)`; 404 if None | No |
| `goal update <slug> --status X [--json]` | New: reject if X is 'archived'; reject if X is not in {active, paused, completed}; call `db.update_goal_status`; detect no-op (same status); call `_autosync_index_md()` only on actual change | Yes, conditionally |
| `goal delete <slug> [--json]` | New: call `db.archive_goal`; if True, call `_autosync_index_md()` | Yes, conditionally |
| `goal restore <slug> [--json]` | New: call `db.restore_goal` (raises if not archived); call `_autosync_index_md()` | Yes (always, since restore only succeeds on real change) |

#### `task` family (extends existing `task add` / `task update`)

| Subcommand | Body (key operations) | Triggers `_autosync_index_md`? |
|---|---|---|
| `task add <id> <slug> <seq> <title> ...` | Existing (unchanged) | Yes (already wired) |
| `task list [--goal X] [--status X] [--all] [--json]` | New: call `db.list_tasks(goal_slug=..., status=...)`; if neither `--all` nor `--status archived` is passed, exclude archived by default | No |
| `task show <id> [--json]` | New: call `db.get_task(id)`; 404 if None | No |
| `task update <id> <new_status>` | Existing (unchanged) | Yes, conditionally (already wired) |
| `task delete <id> [--json]` | New: call `db.archive_task`; if True, call `_autosync_index_md()` | Yes, conditionally |
| `task restore <id> [--json]` | New: call `db.restore_task` (raises if not archived); call `_autosync_index_md()` | Yes |

#### Argparse structure

`goal` already has a `_subparsers` pattern. Extend `goal_sub` with `list`, `show`, `update`, `delete`, `restore`. Extend `task_sub` with `list`, `show`, `delete`, `restore`. `goal update` and `goal restore` and `task restore` accept `--json` like their siblings. Status flags use `choices=` to get argparse validation for free.

### 4.4 Dashboard additions

| Route | Change | Existing or new |
|---|---|---|
| `GET /` | Parse `?all=1` and `?status=<key>`; pass through to template | Existing |
| `GET /goal/<slug>` | Detect archived; pass `is_archived=True` to template | Existing |
| `GET /task/<id>` | New: fetch task + parent goal; render `task_detail.html`; 404 if missing | **New** |

Templates:
- `index.html` — add archived link in header; respect filter in the iteration
- `goal_detail.html` — add archived banner block
- `task_detail.html` — new; mirrors `goal_detail.html` structure but for a task

No POST routes. No forms. No CSRF.

## 5. Compatibility & Non-Goals

**Compatibility:**
- **Existing CLI subcommands unchanged in exit codes and output shape.** New subcommands follow Item 1's conventions (`--json`, exit 0/1/2, errors to stderr).
- **`goal add` and `task add` semantics unchanged.** Status defaults remain `active` / `pending`.
- **No DB schema change.** `status` columns are TEXT; adding `'archived'` to the allowed values does not require migration.
- **No new Python dependency.** Pure stdlib.
- **Existing tests still pass.** Item 5's 145 tests + 2 pre-existing `test_today_*` failures must remain unchanged.
- **`goals/index.md` existing content preserved.** Only the link-list section is regenerated; header bytes are unchanged.

**Non-goals (explicitly out of scope for v1):**
- `goal update --name/--description` — not in v1; name/description stays hand-edited in DB or `goals/<slug>/goal.md`.
- `task update <id> --title/--hours/--depends-on` — same reason.
- Hard delete (DB row removal) — not in v1; archived is the recoverable replacement.
- `goal restore --to paused|completed` — not in v1; restore always returns to `active` / `pending`.
- Bulk operations (`goal archive --pattern ...`, etc.) — not in v1.
- Dashboard POST routes for update/delete — deferred (keeps dashboard read-only).
- Bi-directional sync (md → DB) — deferred per Item 5 §6.
- Watching `goals/index.md` for external edits — deferred per Item 5 §6.
- Watching `goals/.archive/` (if we ever add one) — N/A in v1; archived goals remain in `goals/<slug>/`.
- Removing the archived goals' `goals/<slug>/goal.md` files — explicitly kept on disk (so a `restore` round-trip is meaningful and the file content is preserved).

## 6. Acceptance Criteria

1. `python scripts/cli.py goal list` on an initialized DB with mixed active/paused/archived goals returns only active+paused (no archived).
2. `python scripts/cli.py goal list --all` includes archived.
3. `python scripts/cli.py goal list --status archived` returns only archived.
4. `python scripts/cli.py goal show <slug>` on an existing goal returns the goal dict (and `--json` returns valid JSON); on a missing slug exits 2.
5. `python scripts/cli.py goal update <slug> --status paused` moves the goal to paused; on the next `sync-md` the index shows it under `## 已暂停`.
6. `python scripts/cli.py goal update <slug> --status archived` exits 2 with a hint pointing to `goal delete`.
7. `python scripts/cli.py goal delete <slug>` sets the goal to archived and removes it from `goals/index.md` on the next sync; the `goals/<slug>/goal.md` file remains on disk.
8. `python scripts/cli.py goal delete <slug>` on an already-archived goal is idempotent (exit 0, no sync fired).
9. `python scripts/cli.py goal restore <slug>` on an archived goal returns it to active and re-adds it to `goals/index.md`.
10. `python scripts/cli.py goal restore <slug>` on a non-archived goal exits 2.
11. `python scripts/cli.py task list [--goal X] [--status X] [--all]` works symmetrically with `goal list`.
12. `python scripts/cli.py task show <id>` returns the task dict; missing id exits 2.
13. `python scripts/cli.py task delete <id>` archives the task; the parent goal's completion percentage decreases on next sync.
14. `python scripts/cli.py task restore <id>` returns the task to pending.
15. After `python scripts/cli.py goal delete example-goal` and `python scripts/cli.py rebuild-timers`, no cc-connect timer for example-goal remains.
16. The dashboard `GET /` shows archived goals only when `?all=1` or `?status=archived` is in the query string.
17. The dashboard `GET /goal/<slug>` for an archived goal shows an archived banner.
18. The dashboard `GET /task/<id>` returns the task detail page; missing id returns 404.
19. `python -m pytest -q` reports 145 + ~40 new = ~185 total tests, all passing (the 2 pre-existing `test_today_*` failures remain unchanged).
20. No file outside the planned list is modified.

## 7. Test Plan

| Test class | Test | Verifies |
|---|---|---|
| `TestArchiveGoal` (test_db.py) | `test_archive_sets_status` | `archive_goal` flips active → archived |
| `TestArchiveGoal` | `test_archive_idempotent_returns_false` | Second call returns False |
| `TestArchiveGoal` | `test_archive_missing_raises` | Nonexistent slug raises |
| `TestRestoreGoal` | `test_restore_archived_to_active` | archived → active |
| `TestRestoreGoal` | `test_restore_non_archived_raises` | non-archived raises |
| `TestArchiveTask` / `TestRestoreTask` | mirror cases | symmetric |
| `TestGoalListCli` (test_cli.py) | `test_default_hides_archived` | AC #1 |
| `TestGoalListCli` | `test_all_includes_archived` | AC #2 |
| `TestGoalListCli` | `test_status_query_filters` | AC #3 |
| `TestGoalListCli` | `test_json_output_shape` | list shape |
| `TestGoalShowCli` | `test_show_existing` | AC #4 |
| `TestGoalShowCli` | `test_show_missing_exits_2` | AC #4 |
| `TestGoalUpdateCli` | `test_update_status_triggers_sync` | AC #5 |
| `TestGoalUpdateCli` | `test_update_to_archived_rejected` | AC #6 |
| `TestGoalUpdateCli` | `test_update_noop_no_sync` | idempotent no-sync |
| `TestGoalDeleteCli` | `test_delete_archives_and_syncs` | AC #7 |
| `TestGoalDeleteCli` | `test_delete_idempotent_no_sync` | AC #8 |
| `TestGoalDeleteCli` | `test_delete_missing_exits_2` | error path |
| `TestGoalRestoreCli` | `test_restore_archived_to_active` | AC #9 |
| `TestGoalRestoreCli` | `test_restore_non_archived_exits_2` | AC #10 |
| `TestTaskListCli` | default hide / `--all` / `--status` / `--goal` filters | AC #11 |
| `TestTaskShowCli` | show + missing | AC #12 |
| `TestTaskDeleteCli` | `test_delete_updates_pct` | AC #13 |
| `TestTaskRestoreCli` | `test_restore_to_pending` | AC #14 |
| `TestArchivedGoalInSync` (test_sync_md.py) | `test_archived_excluded_from_index` | sync-md behavior |
| `TestArchivedGoalInSync` | `test_archived_excluded_from_by_status` | by_status counter |
| `TestArchivedGoalInSync` | `test_restore_makes_goal_reappear` | round-trip |
| `TestArchivedGoalInSync` | `test_archived_with_missing_goal_md_no_warning` | warning suppression |
| `TestArchivedExclusion` (test_scheduler.py) | `test_list_eligible_tasks_excludes_archived` | scheduler |
| `TestArchivedInDashboard` (test_dashboard.py) | `test_index_default_hides_archived` | AC #16 |
| `TestArchivedInDashboard` | `test_index_show_all_includes_archived` | AC #16 |
| `TestArchivedInDashboard` | `test_index_status_query` | AC #16 |
| `TestArchivedInDashboard` | `test_goal_detail_archived_banner` | AC #17 |
| `TestTaskDetail` | `test_task_detail_basic` | AC #18 |
| `TestTaskDetail` | `test_task_detail_archived_banner` | archived task UI |
| `TestTaskDetail` | `test_task_detail_404` | AC #18 error path |
| End-to-end (manual smoke in README) | `goal delete → rebuild-timers` removes cc-connect timer | AC #15 |

**Test isolation:** Reuses Item 5's `TODO_GOALS_DIR` env override in `tests/test_cli.py:run_cli`. All subprocess tests get an isolated `goals/` dir automatically. No new infrastructure needed.

**No mocks:** All tests drive real subprocesses, real SQLite DBs, real `goals/` filesystem, and (for dashboard) real Flask test client + real DB.