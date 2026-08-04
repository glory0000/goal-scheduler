# Claude Operational CLI — Design Spec

**Date:** 2026-08-04
**Status:** Approved (brainstorming complete; awaiting spec review and writing-plans)

## 1. Purpose & Background

The todo scheduler currently exposes its operations through three disjoint surfaces:

- **Feishu messages** to Claude: the README's "When to engage Claude" list — "T001 完成了", "今日重点 = a-stock-quant", "新目标：..." etc. Each requires Claude to interpret prose, then call into the `scripts/` library code with hand-written `python -c "..."` snippets or by directly editing the SQLite DB.
- **Shell helpers**: `dump_state.sh`, `simulate_reminder.sh`, `break_session.sh` (3 files). They each `cd` into the repo and run a single Python one-liner, then format output for the user.
- **Daily fallback cron** (`0b1c9c1`): uses a long natural-language prompt that asks Claude to re-derive the entire timer chain from scratch every morning. This is fragile and redundant — the same logic could be a single Python script.

There is no unified command-line interface. Every operational action requires either Claude in the loop or a bespoke shell wrapper. This spec adds **`scripts/cli.py`** with a stable, documented surface that both Claude and the user can invoke to perform the most common state changes and views, leaving the heavier automation (timer chain rebuild, md sync) to Items 2 and 5.

## 2. User Constraints (from brainstorming)

| Decision | Value |
|---|---|
| Scope (v1) | **State change + view**: `status` / `today` / `goal add` / `task add` / `task update` / `focus set/clear`. **Excludes:** `rebuild-timers` (Item 2), `sync-md` (Item 5), destructive operations (delete task, reorder), goal status changes (pause/resume). |
| Output format | **Human-readable default** with `--json` flag for structured output. Both rendered from the same underlying data; never mixed. |
| CLI framework | **argparse** (Python stdlib, no new dependencies). |
| Style of subcommand names | `status`, `today`, `goal add`, `task add`, `task update`, `focus set`, `focus clear` — bare verbs for top-level views, `noun verb` for state changes. |
| Argument style | Positional for required (id, slug, name, title); `--flag` for optional. Slugs and ids are positional; descriptions and metadata are flags. |

## 3. Architecture Overview

A single new entry point `scripts/cli.py` (~250 lines) routes subcommands via `argparse` subparsers. Each subcommand is a small function that:
1. Validates input against the constraints in §5.
2. Calls into existing `scripts/db.py` / `scripts/scheduler.py` for the actual work.
3. Renders output via `scripts/cli_output.py` (a new ~80-line helper module) — either as a human-readable table or as a JSON dict, depending on the `--json` flag.

```
Claude (Feishu msg) ──► parses ──►  python scripts/cli.py task update T013 done
User (terminal)      ─────────────►  python scripts/cli.py status

scripts/cli.py ──► scripts/db.py       (CRUD, no new functions)
              ──► scripts/scheduler.py (compute_schedule, no new functions)
              ──► scripts/cli_output.py (NEW: format_goal_table, format_task_table, format_today, …)
```

The CLI is a thin layer over the existing library. It does NOT introduce new DB operations or new scheduling logic — it only routes user input to the existing API and formats the response.

**Reuse boundary:**
- `db.py`, `scheduler.py`, `format_utils.py` — **unchanged**.
- `migrate.py` — unchanged. CLI subcommands do not run migrations; if the DB is not initialized, the CLI exits 2 with a hint to run `db.py init` / `migrate.py init`.

## 4. Core Flows

### 4.1 Subcommand: `status`

Show a one-screen snapshot of the system: active goals with progress, today's focus, and the next task the scheduler would assign.

```
$ python scripts/cli.py status
活跃目标 (2):
  a-stock-quant  7/15 完成  47%   [当前]  T012 实现回测引擎
  video-edit     0/3  完成  0%
今日重点: a-stock-quant
下一任务:  14:00  T013 - 跑通回测示例
```

JSON output (one object, top-level keys: `goals`, `focus`, `next_task`):
```
{"goals": [{"slug": "a-stock-quant", "total": 15, "completed": 7, "progress": 47, "current_task_id": "T012"}, ...], "focus": "a-stock-quant", "next_task": {"task_id": "T013", "slot_start": "14:00", "title": "跑通回测示例"}}
```

### 4.2 Subcommand: `today`

List today's free slots and the task assigned to each, plus a count of unscheduled pending tasks.

```
$ python scripts/cli.py today
2026-08-04 周二
今日重点: a-stock-quant
  07:30-09:00  (无任务)
  12:00-13:00  T013 - 跑通回测示例  [a-stock-quant]
  18:00-19:00  T014 - 写周报         [a-stock-quant]
今日剩余 2 个任务未安排
```

JSON: `{"date", "weekday", "focus_slug", "slot_rows": [{slot, task, goal}, ...], "remaining": int}`.

### 4.3 Subcommand: `goal add`

```
$ python scripts/cli.py goal add a-stock-quant "A股量化" --description "策略回测与实盘"
Goal 'a-stock-quant' created.
```

JSON: `{"slug", "name", "description", "status": "active", "created_at"}`.

### 4.4 Subcommand: `task add`

```
$ python scripts/cli.py task add T013 a-stock-quant 13 "跑通回测示例" \
    --hours 1.0 --depends-on T012
Task T013 created.
```

JSON: `{"id", "goal_slug", "sequence", "title", "estimated_hours", "depends_on": [...], "status": "pending"}`.

### 4.5 Subcommand: `task update`

```
$ python scripts/cli.py task update T013 done
Task T013 marked done at 2026-08-04T14:23:00.
```

For `in_progress` updates where `started_at` is being stamped (per the elapsed-time tracking feature from the prior spec), the message includes the timestamp:
```
$ python scripts/cli.py task update T013 in_progress
Task T013 marked in_progress at 2026-08-04T14:23:00.
```

JSON: `{"id", "status", "started_at"?, "completed_at"?, "updated_at"}`. Idempotent: re-applying the same status is a no-op, exit 0, same output.

### 4.6 Subcommand: `focus set` / `focus clear`

```
$ python scripts/cli.py focus set a-stock-quant
Focus set to 'a-stock-quant'.

$ python scripts/cli.py focus clear
Focus cleared.
```

JSON: `{"focus": "a-stock-quant" | null}`.

## 5. Error Handling

### Exit codes

| Code | Meaning |
|---|---|
| 0 | Success |
| 1 | Input error (bad args, invalid status, malformed slug, missing required) |
| 2 | DB error (connection failed, schema missing — i.e. `schema_version` table absent) |
| 3 | Resource not found (goal slug, task id, focus value) |

### Error output rules

- **All error messages go to stderr** (always human-readable, regardless of `--json`).
- On success, stdout carries the human text or the JSON, depending on the flag.
- **stdout is always empty on error paths** — no JSON mixing, no half-printed tables. Claude's parser sees a non-zero exit code, then reads stderr for the reason.
- The DB-untouched invariant: if the CLI exits non-zero, no rows were inserted/updated.

### Idempotency

| Action | Behavior on repeat / on duplicate |
|---|---|
| `goal add <existing-slug>` | Exit 1: "Goal 'a-stock-quant' already exists." No DB change. |
| `task add <existing-id>` | Exit 1: "Task 'T013' already exists." No DB change. |
| `task update <id> <same-status>` | Exit 0, no DB change, message: "Task T013 already done (no change)." |
| `task update <id> <new-status>` | State changes (status applied, `started_at` preserved per elapsed-time tracking semantics). Re-applying the same input is then a no-op (covered by the row above). |
| `focus set <slug>` (already set) | Exit 0, no-op, message: "Focus already 'a-stock-quant' (no change)." |
| `focus clear` (already null) | Exit 0, no-op, message: "Focus already unset (no change)." |

The CLI is **idempotent on re-application**: calling any of the above with the same input never produces a different outcome, regardless of starting state.

### Input validation (executed before any DB call)

| Field | Rule | On violation |
|---|---|---|
| `<slug>` | Match `^[a-z0-9][a-z0-9-]{0,62}$` | Exit 1, "Invalid slug: must match [a-z0-9][a-z0-9-]{0,62}." |
| `<task-id>` | Match `^<slug>-T\d{3,}$` where `<slug>` resolves to an existing goal | Exit 1 if format bad, exit 3 if goal doesn't exist |
| `<status>` | One of `pending`, `in_progress`, `done`, `skipped` | Exit 1, "Invalid status 'X'. Valid: pending, in_progress, done, skipped." |
| `--hours` | Float ≥ 0 | Exit 1, "Hours must be ≥ 0." |
| `--depends-on` (each) | Must be a valid task id that exists in the same goal | Exit 1, "Depends-on task 'T005' not found in goal 'a-stock-quant'." |

### DB uninitialized

When `schema_version` table does not exist:
```
$ python scripts/cli.py status
Error: database not initialized. Run `python scripts/db.py init` first.
```
Exit 2, stderr only, no JSON, no partial output.

## 6. Testing Strategy

**`tests/test_cli.py`** (new, ~24 tests, all subprocess-based):

Helper (mirrors `tests/test_migrate.py::run_migrate`):
```python
def run_cli(args, db_path, cwd=None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.pop("TODO_DB_PATH", None)
    env["TODO_DB_PATH"] = str(db_path)
    return subprocess.run(
        [sys.executable, "scripts/cli.py", *args],
        capture_output=True, text=True, env=env, cwd=cwd or REPO_ROOT,
    )
```

| Subcommand | Test names | What it asserts |
|---|---|---|
| `status` | `test_status_human_output`, `test_status_json_output`, `test_status_empty_db` | Each format renders the right shape; empty DB shows "(无活跃目标)" / empty JSON list |
| `today` | `test_today_human_output`, `test_today_json_output`, `test_today_no_assignments` | Slot list + focus + remaining count |
| `goal add` | `test_goal_add_success`, `test_goal_add_duplicate_rejected`, `test_goal_add_invalid_slug_format`, `test_goal_add_json` | Create happy path; dup → exit 1; bad slug → exit 1; JSON output is parseable |
| `task add` | `test_task_add_simple`, `test_task_add_with_dependencies`, `test_task_add_missing_required_arg`, `test_task_add_goal_not_found` | Happy paths; missing `--hours` is allowed (defaults to 0); bad goal slug → exit 3 |
| `task update` | `test_task_update_done`, `test_task_update_idempotent_no_op`, `test_task_update_invalid_status`, `test_task_update_task_not_found` | Done stamps `completed_at`; idempotent reapply → exit 0 with "no change" message; bad status → exit 1; not found → exit 3 |
| `focus` | `test_focus_set`, `test_focus_clear`, `test_focus_clear_when_already_empty` | Round-trip + idempotency |

**Cross-cutting** (`tests/test_cli.py`):
- `test_db_uninitialized_returns_exit_2` — empty `tmp_path / "fresh.db"`, run `status`, assert exit 2 and stderr contains "Run `python scripts/db.py init` first".
- `test_error_path_writes_to_stderr_only` — run a deliberately-failing command (`task update T999 done`), assert `result.stdout == ""` and `result.returncode == 3` and "not found" in `result.stderr`.
- `test_json_flag_outputs_parseable_json_on_success` — `status --json`, assert `json.loads(result.stdout)` succeeds and has expected keys.

**No changes to existing tests.** The 69 prior tests continue to pass; v1 target is 93/93 total (69 prior + 24 new: 21 per-subcommand + 3 cross-cutting).

## 7. Compatibility & Non-Goals

**Compatibility:**
- **Existing Feishu flow still works.** Claude can still interpret prose and call into `db.py` directly; the CLI is an *additional* surface, not a replacement. Migration of the prose-interpretation logic to CLI-routing is a separate concern (not in v1).
- **Shell helpers (`dump_state.sh` etc.) remain.** v1 does not delete them. The user can adopt `cli.py` at their own pace; legacy scripts are still useful for the casual cases they were written for. Future cleanup (deleting redundant shell helpers) is not in v1.
- **No DB schema changes.** The CLI is a consumer of the existing schema; `migrations/002_add_started_at.sql` is the most recent schema change and v1 of the CLI neither requires nor introduces a new one.
- **`migrate.py` is not invoked by the CLI.** If the DB is missing `schema_version`, the CLI exits 2 with a hint pointing the user at `db.py init` / `migrate.py init`. The user (or Claude) decides which bootstrap path to take.

**Non-goals (explicitly out of scope for v1):**
- `rebuild-timers` subcommand — belongs to Item 2 (reminder chain automation).
- `sync-md` subcommand — belongs to Item 5 (goals index auto-sync).
- Deleting a task (`task delete <id>`) — destructive, v2.
- Reordering tasks (`task reorder <id> <new-seq>`) — destructive, v2.
- Pausing/resuming a goal (`goal update <slug> --status paused`) — defer to v2; the underlying `db.update_goal_status` exists, but adding the CLI command is not in v1.
- Bulk operations (`task update --status done --goal <slug>`) — v2.
- Shell completion (`argcomplete`) — not enabled.
- Internationalization of messages — CLI messages are mixed Chinese/English (Chinese for user-facing summaries, English for status names and machine identifiers) per the project's existing convention; no i18n layer in v1.

## 8. Acceptance Criteria

1. `python scripts/cli.py status` on an initialized DB with ≥ 1 active goal and a focus set prints a human-readable snapshot that includes the active goal(s) with progress, today's focus, and the next task.
2. `python scripts/cli.py status --json` returns exit 0 with a single valid JSON object on stdout (parseable by `json.loads`), no output on stderr.
3. All 6 subcommand groups (`status`, `today`, `goal add`, `task add`, `task update`, `focus set/clear`) execute and exit 0 on the happy path.
4. On an uninitialized DB, every subcommand exits 2 and prints a single line on stderr that contains "Run `python scripts/db.py init` first" (or equivalent).
5. `python scripts/cli.py goal add a-stock-quant "X"` then `python scripts/cli.py goal add a-stock-quant "Y"` — the second invocation exits 1 with "already exists" in stderr and does not modify the DB.
6. `python scripts/cli.py task update <existing-id> <current-status>` exits 0 with a "no change" message and does not modify the DB (idempotency).
7. `python -m pytest -q` reports 93/93 (69 prior + 24 new CLI tests).
8. No file outside the planned list (`scripts/cli.py`, `scripts/cli_output.py`, `tests/test_cli.py`, plus the existing `README.md` "Common commands" section update to mention the CLI) is modified.
9. The CLI does not require any new Python dependency beyond stdlib.
