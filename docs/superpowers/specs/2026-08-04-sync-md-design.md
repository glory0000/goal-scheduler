# Goals Index Auto-Sync (sync-md) — Design Spec

**Date:** 2026-08-04
**Status:** Approved (brainstorming complete; awaiting spec review and writing-plans)

## 1. Purpose & Background

`goals/index.md` is currently maintained by Claude by hand after every goal / task CRUD operation. The index format is fixed by spec §4.4 (`- [name](slug/goal.md) — 状态：X — 完成率 Y%`), but Claude has to re-derive it from SQLite state every time and overwrite the file. This is:

- **Error-prone.** Claude can forget to update the file, or update it with stale progress percentages (e.g., after a `task update done`).
- **Inconsistent.** The "完成率" in the index sometimes drifts from the actual `db.recompute_goal_counts()` value.
- **Redundant.** The derivation logic — list goals, list tasks per goal, count done / total, format per status group — is identical every time and could live in a deterministic function.

This spec adds a deterministic, idempotent `sync-md` CLI subcommand that derives `goals/index.md` from SQLite state, and wires it into the existing CRUD subcommands so the index stays in sync without Claude having to remember.

The per-goal progress section inside `goals/<slug>/goal.md` is **already** auto-maintained by `db.write_goal_md_progress()` (db.py:255) and is **out of scope** for this spec.

## 2. User Constraints (from brainstorming)

| Decision | Value |
|---|---|
| Scope (v1) | Auto-sync `goals/index.md` only. `goals/<slug>/goal.md` progress section is already handled by `db.write_goal_md_progress()`. |
| Trigger | **Both** manual CLI invocation (`python scripts/cli.py sync-md`) and automatic after successful `goal add` / `task add` / `task update`. `rebuild-timers` does NOT trigger sync (unrelated surface). |
| Preservation | Preserve everything before the first `- [` (link) line. Manual header text, comments, blank lines all kept. From the first `- [` onward, the file is fully regenerated. |
| List scope | **All goals** (active / paused / completed). Hidden goals are not in scope for v1 — every DB row is rendered. |
| Group order | Fixed: `active → paused → completed`. Empty groups emit no heading. |
| Sort | Within each group: ascending by `slug` (Python `sorted()`, Unicode codepoint order — slugs are `[a-z0-9-]` so this is plain ASCII). |
| Sections | `## 进行中`, `## 已暂停`, `## 已完成` as `## ` headings separating groups. |
| Orphan policy | DB row exists but `goals/<slug>/goal.md` is missing → render the link anyway, write stderr warning, exit 0. `goals/<slug>/` exists with no DB row → skip, write stderr warning, exit 0. |
| Approach | **In-process function call** in CRUD subcommands (Approach A). Pure function `render_index_md(goals, tasks_by_goal, header_text)` shared between CLI and auto-trigger. |
| Idempotency | Re-running with no DB change produces byte-identical output (after preserving the original header text). |
| DB writes | None. sync-md is read-only against SQLite. |
| CLI framework | `argparse` (consistent with Item 1 / Item 2). |
| Output format | Human-readable default with `--json` flag. |
| Exit codes | `0` = success (warnings allowed), `1` = input error (reserved, not triggered in v1), `2` = DB not initialized or I/O failure. |
| Test isolation | Subprocess-based, mirrors Item 1's `run_cli()` helper. CWD redirected to `tmp_path` so `goals/index.md` writes never touch the real repo. |
| Dependencies | None new. Stdlib only (`pathlib`, `re`, `json`). |

## 3. Architecture Overview

Three files change, all small:

1. **`scripts/sync_md.py`** (new, ~120 lines) — pure function `render_index_md(goals, tasks_by_goal, header_text) -> str`, helper `compute_completion_pct(tasks)`, file-I/O function `sync_index_md(goals_root: Path) -> SyncResult`. No DB access — callers pass in pre-loaded data. Pure functions are independently testable without touching the filesystem.

2. **`scripts/cli.py`** — gain a `sync-md` subcommand (~30 lines) and a shared helper `_autosync_index_md()` (~15 lines) that is invoked from the end of `subcommand_goal_add`, `subcommand_task_add`, and `subcommand_task_update` after the main operation succeeds. `_autosync_index_md()` captures exceptions and routes them to stderr without exiting.

3. **`tests/test_sync_md.py`** (new, ~250 lines) — three test classes:
   - `TestRenderIndexMd` (pure function, 6 tests)
   - `TestSyncIndexMd` (file I/O with `tmp_path`, 5 tests)
   - `TestSyncMdCli` (subprocess, 6 tests)
   - `TestAutosyncIntegration` (CLI invocation that verifies auto-trigger fires, 3 tests)

**Reuse boundary:**
- `db.py`, `scheduler.py`, `format_utils.py`, `reminder.py`, `migrate.py` — **unchanged**.
- `cli_output.py` — **unchanged**. sync-md uses its own simple renderers (the existing renderers are tied to status / today views, not index lists).
- `goals/index.md` existing file — **preserved** (its header content is read first, then the list section is regenerated).
- `config/schedule.json` — unchanged.

**Data flow on one `sync-md` call:**

```
CLI sync-md
    ↓ _require_initialized_db()
    ↓ db.list_goals()                  [read, no status filter]
    ↓ db.list_tasks()                  [read, all goals]
    ↓ build tasks_by_goal dict
    ↓ read goals/index.md (if exists) → header_text
    ↓ render_index_md(goals, tasks_by_goal, header_text) → str
    ↓ scan goals/ for orphan dirs → warnings list
    ↓ write goals/index.md (atomic: write to .tmp, rename)
    ↓ stdout: human summary or --json
```

## 4. Core Algorithm — `render_index_md`

### 4.1 Signature

```python
def render_index_md(
    goals: list[dict],
    tasks_by_goal: dict[str, list[dict]],
    header_text: str,
) -> str:
```

- `goals`: list from `db.list_goals()` — each dict has `slug`, `name`, `status` (and more). Status is one of `active` / `paused` / `completed`.
- `tasks_by_goal`: `{goal_slug: [task_dict, ...]}` from `db.list_tasks()` grouped by `goal_slug`. Each task dict has `status` (one of `pending` / `in_progress` / `done` / `skipped`).
- `header_text`: the preserved top-of-file content from `goals/index.md`. May be empty string if file didn't exist or had no content before the first link line.

### 4.2 Output structure

```
<header_text with trailing newline stripped>

## 进行中
- [<name>](<slug>/goal.md) — 状态：进行中 — 完成率 <P>%
<sorted by slug>

## 已暂停
- ... (only if paused group is non-empty)

## 已完成
- ... (only if completed group is non-empty)
```

- Each `- ` line uses the em-dash `—` (U+2014) consistent with the existing file.
- Status labels match existing index.md: `进行中` for `active`, `已暂停` for `paused`, `已完成` for `completed`.
- Empty groups emit nothing — no heading, no blank line.
- Final file ends with a single trailing `\n`.

### 4.3 `compute_completion_pct` helper

```python
def compute_completion_pct(tasks: list[dict]) -> int:
    """Integer percentage 0..100. 0 when no tasks."""
```

Counts only `done` toward completion. `pending` / `in_progress` / `skipped` all count as not-done. Returns `0` for empty task list (not `0%` of `0/0` ambiguity).

### 4.4 Sort

```python
def _group_and_sort(goals: list[dict]) -> list[list[dict]]:
    groups = {"active": [], "paused": [], "completed": []}
    for g in goals:
        groups[g["status"]].append(g)
    return [
        sorted(groups["active"], key=lambda g: g["slug"]),
        sorted(groups["paused"], key=lambda g: g["slug"]),
        sorted(groups["completed"], key=lambda g: g["slug"]),
    ]
```

### 4.5 Header split algorithm

`scripts/sync_md.py` exposes:

```python
def _split_header(content: str) -> tuple[str, int]:
    """Return (header_text, first_list_line_index).

    Header is everything before the first line matching
    r"^\\s*-\\s+\\[.*\\]\\(.*/goal\\.md\\)".
    list_line_index is 0-based; len(lines) if no match.
    """
```

- Lines are split with `splitlines(keepends=True)` so header byte content is preserved exactly.
- The detected first list line is **excluded** from `header_text`.
- Header text is returned with trailing `\n` stripped (so the caller can decide spacing).

### 4.6 File I/O (`sync_index_md`)

```python
@dataclass
class SyncResult:
    path: Path
    synced_count: int
    by_status: dict[str, int]   # {"active": 2, "paused": 1, "completed": 1}
    changed: list[str]          # slugs whose line differs from prior index
    unchanged: list[str]
    warnings: list[str]
    header_preserved: bool

def sync_index_md(goals_root: Path) -> SyncResult:
    """Read goals/<slug>/goal.md paths; derive and write index.md.

    Raises OSError on I/O failure (caller decides exit code).
    """
```

Atomic write: write to `goals/index.md.tmp` then `Path.replace()` to `goals/index.md`. Avoids partial-write corruption.

### 4.7 Change detection

To populate `changed` / `unchanged`, the function parses the existing `goals/index.md` (if any), extracts existing `- [name](slug/goal.md) — 状态：X — 完成率 Y%` lines, and compares per-slug against the freshly-rendered output. A slug is `changed` if its line differs (status / completion / link target) or if it appears in only one of the two sets.

This is what makes the human output prefix (`+` vs `~` vs ` `) and the `--json` `changed` list meaningful.

## 5. CLI Surface

### 5.1 Subcommand

```
python scripts/cli.py sync-md [--json]
```

No positional arguments. No flags beyond `--json`.

### 5.2 Human output (default)

```
Synced 3 goals to goals/index.md (active=2, paused=1, completed=1)
- +example-goal   (进行中 50%)
-  example-goal-2 (进行中 0%)
- ~paused-goal    (已暂停 33%)
- ~old-completed  (已完成 100%)

Warnings:
- goal dir 'goals/orphan/' has no DB row — skipped
```

Prefix legend: `+` newly added in DB, `~` exists but status / progress changed, ` ` unchanged. The `Warnings` block is only printed when there is at least one warning.

### 5.3 JSON output (`--json`)

```json
{
  "path": "goals/index.md",
  "synced_count": 3,
  "by_status": {"active": 2, "paused": 1, "completed": 1},
  "changed": ["example-goal", "paused-goal", "old-completed"],
  "unchanged": ["example-goal-2"],
  "warnings": ["goal dir 'goals/orphan/' has no DB row — skipped"],
  "header_preserved": true
}
```

### 5.4 Exit codes

| Scenario | Code | Notes |
|---|---|---|
| Success, no warnings | 0 | Normal path |
| Success, with warnings (orphans, missing files) | 0 | Warnings go to stderr |
| DB not initialized | 2 | Consistent with `status` / `today` / etc. |
| File I/O failure (disk full, permission denied) | 2 | DB was read but `index.md` write failed |
| Input error (no args / unknown flag) | 1 | argparse handles; consistent with sibling subcommands |

### 5.5 Auto-trigger integration

A single shared helper `_autosync_index_md()` is added to `scripts/cli.py`:

```python
def _autosync_index_md() -> None:
    """Sync goals/index.md after a successful CRUD op.

    Captures all exceptions and routes them to stderr. Never raises.
    Never exits. The calling subcommand has already succeeded.
    """
```

Called from the end of:

| Subcommand | Call location |
|---|---|
| `subcommand_goal_add` | After `db.create_goal()` returns, before stdout |
| `subcommand_task_add` | After `db.create_task()` returns, before stdout |
| `subcommand_task_update` | After `db.update_task_status()` when the status actually changed (skip no-op idempotent calls) |

`subcommand_rebuild_timers` does NOT call this helper (no DB write, no goal / task impact).

## 6. Compatibility & Non-Goals

**Compatibility:**
- **Existing `goals/index.md` content.** The header text is preserved byte-for-byte. Only the link list section is regenerated. If the user has manually edited a goal's line in the index, that edit is overwritten on the next sync (this is the intended behavior — DB is source of truth).
- **Existing CLI subcommands unchanged in exit codes and output shape.** sync-md follows the same conventions (`--json`, exit 0/1/2, errors to stderr).
- **No new Python dependency.** Pure stdlib.
- **Existing tests still pass.** Item 2's 116 tests must continue to pass without modification. sync-md tests are additive.
- **DB schema unchanged.** sync-md only reads from existing tables (`goals`, `tasks`).

**Non-goals (explicitly out of scope for v1):**
- `goal update <slug> --status <paused|completed>` — not in v1; when added later, its subcommand body will call `_autosync_index_md()` to keep index in sync.
- `goal delete <slug>` — destructive, deferred per Item 1 §7.
- `task delete <id>` / `task reorder` — destructive, deferred per Item 1 §7.
- Bi-directional sync (DB → md is the only direction in v1).
- Per-goal filter (`sync-md --goal <slug>`) — deferred; full sync is fast and not a bottleneck.
- Watching `goals/index.md` for external edits — single-writer model in v1 (CLI only).
- Style customisation (different separators, English labels, etc.) — Chinese labels and em-dash separator are fixed.

## 7. Acceptance Criteria

1. `python scripts/cli.py sync-md` on an initialized DB with at least one active goal exits 0, writes `goals/index.md` with the prescribed structure (preserved header + grouped list), and prints a human summary on stdout.
2. `python scripts/cli.py sync-md --json` exits 0 and emits a single valid JSON object matching the schema in §5.3.
3. Running `sync-md` twice in succession produces byte-identical `goals/index.md` (idempotency).
4. An existing `goals/index.md` whose first line is `# 目标索引` (custom header) keeps that header after sync; only the `- [...]` list section is regenerated.
5. Orphan directory `goals/<slug>/` with no DB row produces a stderr warning and is NOT included in the index.
6. A DB goal whose `goals/<slug>/goal.md` is missing still gets rendered with the link; stderr carries a warning naming the missing file.
7. On an uninitialized DB (`schema_version` table absent), `sync-md` exits 2 with the standard "Run `python scripts/db.py init` first." message.
8. After `python scripts/cli.py goal add <slug> "<name>"` succeeds, `goals/index.md` contains the new goal in the `## 进行中` section without the user running sync-md manually.
9. After `python scripts/cli.py task update <existing-id> done`, the corresponding goal's completion percentage in `goals/index.md` increases; after `done → in_progress` it decreases.
10. `python -m pytest -q` reports 116 + ~20 new = ~136 total tests, all passing.
11. No file outside the planned list (`scripts/sync_md.py`, `scripts/cli.py`, `tests/test_sync_md.py`, `README.md`) is modified.

## 8. Test Plan

| Test class | Test | Verifies |
|---|---|---|
| `TestRenderIndexMd` | `test_empty_goals_renders_only_header` | Header preserved, no headings when no goals |
| `TestRenderIndexMd` | `test_single_active_goal` | One goal, one section, one line |
| `TestRenderIndexMd` | `test_groups_in_order_active_paused_completed` | Group ordering invariant |
| `TestRenderIndexMd` | `test_empty_group_section_omitted` | No `## 已暂停` when paused is empty |
| `TestRenderIndexMd` | `test_completion_pct_with_zero_tasks` | Returns 0, not a ZeroDivisionError |
| `TestRenderIndexMd` | `test_sort_within_group_by_slug` | Deterministic ASCII ordering |
| `TestSyncIndexMd` | `test_creates_index_md_if_missing` | File created with rendered content |
| `TestSyncIndexMd` | `test_preserves_existing_header` | First 5 lines of input appear verbatim in output |
| `TestSyncIndexMd` | `test_overwrites_only_list_section` | Diff before/after affects only list region |
| `TestSyncIndexMd` | `test_warns_on_orphan_directory` | Stderr warning, index unchanged |
| `TestSyncIndexMd` | `test_warns_when_goal_md_missing` | Stderr warning, link still rendered |
| `TestSyncMdCli` | `test_human_output_default` | stdout matches §5.2 shape |
| `TestSyncMdCli` | `test_json_output_shape` | stdout matches §5.3 JSON |
| `TestSyncMdCli` | `test_db_uninitialized_exits_2` | Exit 2 + standard hint |
| `TestSyncMdCli` | `test_warnings_on_stderr` | Stderr lines present, stdout human |
| `TestSyncMdCli` | `test_warnings_dont_block_exit_0` | Warnings → exit 0 |
| `TestSyncMdCli` | `test_idempotent_second_run` | Two runs → byte-identical `index.md` |
| `TestAutosyncIntegration` | `test_goal_add_triggers_sync` | After `goal add`, `index.md` contains the new slug |
| `TestAutosyncIntegration` | `test_task_add_triggers_sync` | After `task add`, `index.md` lists the goal |
| `TestAutosyncIntegration` | `test_task_update_to_done_updates_pct` | After `task update done`, completion % changes |