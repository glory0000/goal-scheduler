# Reminder Chain Automation — Design Spec

**Date:** 2026-08-04
**Status:** Approved (brainstorming complete; awaiting spec review and writing-plans)

## 1. Purpose & Background

The reminder chain currently relies on a single piece of redundancy: a daily fallback cron (`5 0 * * *`) whose prompt is a long natural-language instruction that asks Claude to re-derive the entire timer chain from scratch every morning. This prompt is fragile (each step is prose-interpreted), redundant (the same logic could be a single Python script), and high-cardinality (every morning Claude does the same work — list cc-connect timers, compute today's slots, diff, add/remove — by hand).

The morning of 2026-08-04 demonstrated the current state: three pending timers (12:00, 18:00, 21:00) for `example-goal` exist in cc-connect. They were created by the morning cron via Claude's interpretation of the natural-language prompt. There is no guarantee that the same logic is applied tomorrow — Claude could re-interpret the prompt slightly differently, or miss a slot.

This spec adds a deterministic, idempotent `rebuild-timers` CLI subcommand that the daily cron calls instead of a long prompt. It computes the desired timer set for today's remaining slots and reconciles it with cc-connect's actual state — adding missing timers, removing stale ones, leaving matching ones untouched.

The per-slot reminder flow (timer fires → Claude session → `format_reminder` → send Feishu message → add next slot's timer) is **unchanged**. The only thing that changes is *who* runs the "morning rebuild" logic: a Python script instead of Claude re-deriving the chain.

## 2. User Constraints (from brainstorming)

| Decision | Value |
|---|---|
| Scope (v1) | **Only change the fallback cron.** Per-slot reminder flow stays as-is. Mid-session timer repair (via `break_session.sh`) also stays. |
| Form | **New CLI subcommand `rebuild-timers`** added to `scripts/cli.py` (extending Item 1's CLI). The cron prompt becomes a single shell command, not prose. |
| Effects | **Only add/remove cc-connect timers.** No message generation, no DB writes, no task status updates. |
| Time window | **Today only.** Slots in the past are ignored; slots in the future are reconciled. No lookahead to tomorrow. |
| CLI framework | **argparse** (consistent with Item 1). |
| Output format | **Human-readable default** with `--json` flag. Reuses Item 1's `to_json()` and renderer pattern. |
| Exit codes | **0 / 1 / 2** (0 success, 1 input/config error, 2 external dependency failure). Code 3 is not used (no "not found" semantics in rebuild-timers). |

## 3. Architecture Overview

Three files change, all small:

1. **`scripts/cli.py`** — gain a `rebuild-timers` subcommand (~80 lines). Reuses Item 1's `_require_initialized_db()`, `--json` flag, exit code conventions, and imports `to_json()` from `scripts/cli_output.py`. Adds a new module-level pure function `reconcile_timers(planned, actual, now) -> dict` that the diff algorithm lives in (testable in isolation).

2. **`scripts/cc_timers.py`** (new, ~50 lines) — thin wrapper around `cc-connect timer list / add / del`. Two backends:
   - **Production**: calls `cc-connect timer ...` via `subprocess.run`.
   - **Test**: when env var `TODO_TEST_TIMER_FILE` is set, reads/writes a JSON file at that path (so integration tests don't touch the real cc-connect daemon).

3. **The daily cron** (`5 0 * * *`) — prompt changes from the current long natural-language instruction to a single command: `python scripts/cli.py rebuild-timers`. Claude's morning role shrinks to: read the cron output, ack success or surface stderr on failure.

**Reuse boundary:**
- `db.py`, `scheduler.py`, `format_utils.py`, `reminder.py`, `migrate.py` — **unchanged**.
- `config/schedule.json` — unchanged (read-only consumer).
- No new DB schema, no new dependencies.

**Data flow on one `rebuild-timers` call:**

```
cron 5am  ──►  scripts/cli.py rebuild-timers
                      │
                      ├─► db.get_today_focus()            [read]
                      ├─► config/schedule.json            [read]
                      ├─► scheduler.compute_schedule(...) [read, per remaining slot]
                      ├─► cc_timers.list_today_remaining() [read cc-connect]
                      ├─► reconcile_timers(planned, actual, now)  [pure function]
                      ├─► cc_timers.add(slot) / .del(id)  [write cc-connect]
                      └─► render summary to stdout
```

## 4. Core Algorithm

### 4.1 Build the planned set

```python
now       = datetime.now().astimezone()
today     = now.date().isoformat()
now_hhmm  = now.strftime("%H:%M")
focus     = db.get_today_focus()
all_slots = schedule.for_date(today)   # from config/schedule.json

if focus is None:
    planned = []  # caller exits 0 with "no focus set" before scheduling
else:
    # Single call — scheduler.compute_schedule already tracks used_task_ids
    # internally across multiple slots in one invocation, so we get the
    # correct per-slot assignment without manual dedup.
    plan = scheduler.compute_schedule(
        focus, today, now_hhmm, max_slots=len(all_slots),
    )
    slots_by_start = {s["start"]: s for s in all_slots}
    planned = []
    for entry in plan:
        if entry["slot_start"] <= now_hhmm:           # past slot — skip
            continue
        slot = slots_by_start.get(entry["slot_start"])
        if slot is None:                              # unknown slot — skip
            continue
        planned.append({
            "slot_start": entry["slot_start"],
            "slot_end":   entry["slot_end"],
            "slot_label": slot["label"],
            "task_id":    entry["task_id"],
            "goal_slug":  entry["goal_slug"],
        })
```

**Key simplification:** the scheduler's per-invocation `used_task_ids` set already handles dedup across multiple slots. One `compute_schedule` call with `max_slots=len(slots)` is the correct shape — per-slot `max_slots=1` calls would re-pick the same task because each call has a fresh empty set.

### 4.2 Read the actual set

`cc_timers.list_today_remaining(today)` calls `cc-connect timer list`, parses output, filters to:
- description starts with `"Todo scheduler: "` (our own timers)
- `fire_at` is in the future relative to `now`
- date portion of description matches `today`

Timers that don't match these criteria are returned in a separate `foreign` list and left untouched.

### 4.3 Diff and apply

```python
def reconcile_timers(planned, actual) -> dict:
    planned_keys = {(p["slot_start"], p["task_id"]) for p in planned}
    actual_keys  = {(a["slot_start"],  a["task_id"])  for a in actual
                    if a.get("slot_start") is not None
                    and a.get("task_id") is not None}
    to_add    = [p for p in planned
                 if (p["slot_start"], p["task_id"]) not in actual_keys]
    to_remove = [a for a in actual
                 if a.get("slot_start") is not None
                 and a.get("task_id") is not None
                 and (a["slot_start"], a["task_id"]) not in planned_keys]
    return {"to_add": to_add, "to_remove": to_remove}
```

**Matching key:** `(slot_start, task_id)` tuple. The timer description encodes both pieces (see §4.5). Two timers at the same slot but different tasks are treated as different — a stale task gets its old timer removed and a new one added. This is what makes the "mid-day focus change" use case work: if a focus change causes a different task to fill a slot, the old timer is correctly identified as stale even though the slot itself is unchanged. Foreign timers (description not in our format) and pre-Findings-2 legacy timers (description without task_id) are ignored by the diff — the caller keeps them.

**Application order:** removals first, then adds. This minimizes the "partially-applied" state if a `cc_timers.add` or `.del` call fails mid-flight: at worst we have one fewer timer than expected, never an extra orphan.

### 4.4 Output

**Human (default):**

```
Rebuilt timers for 2026-08-04:
  added   2  (18:00 evening → T002, 21:00 night → T003)
  removed 0
  kept    0
  ignored 1  (foreign timer: "User manual reminder")
```

**JSON (`--json`):**

```json
{
  "date": "2026-08-04",
  "added":   [{"slot_start": "18:00", "task_id": "T002", ...}],
  "removed": [],
  "kept":    [],
  "ignored_foreign": [{"id": "...", "description": "User manual reminder"}],
  "summary": {"added": 2, "removed": 0, "kept": 0, "ignored": 1}
}
```

### 4.5 Timer prompt and description format

Each added timer uses the same prompt format the cron currently uses for per-slot triggers, with `task_id` appended to the first line so the description encodes the task:

```
Free slot 启动: <date> <slot_start> <slot_label> (<slot_start>-<slot_end>) - <task_id>.

Send a Feishu reminder for the next pending task. Steps:
1. cd to <repo_root>
2. Read data/todos.db: python -c "..."
3. Compute plan: python -c "..."
4. If plan has an entry: send reminder via reminder.format_reminder(...)
5. If no plan: send '今日 <slot_start> 无待办任务'
6. After sending, set up the next timer for the next free slot via: cc-connect timer add ...

Your reply IS the Feishu message to send. Reply in Chinese.
```

**Timer description (the value cc-connect stores as the timer's user-visible description):**

```
Todo scheduler: <date> <slot_start> <slot_label> - <task_id>
```

Example: `Todo scheduler: 2026-08-04 18:00 evening - g1-T002`. Two parse functions recover the pieces:

- `parse_slot_start_from_description(desc) -> "HH:MM" | None` — used to filter foreign timers (any description not matching this format is foreign).
- `parse_task_id_from_description(desc) -> "T<id>" | None` — used by the diff to match `(slot_start, task_id)` tuples.

**Pre-Item-2 legacy timers** (created by the old natural-language cron, description like `Todo scheduler: 2026-08-04 12:00 lunch` without `- <task_id>`) parse slot_start successfully but return `None` for task_id. They are excluded from the diff (the reconcile function only matches entries with both fields present), so legacy timers stay in cc-connect until they fire naturally and self-archive. After Item 2 ships, all newly-added timers have task_id encoded, so the diff operates normally on them.

**Production cron** passes both `--prompt` (the multi-line text above) and `--desc` (the `Todo scheduler: ...` string) to `cc-connect timer add`. The test backend stores the same `description` field that production would expose via `cc-connect timer list`.

The prompt template is moved from the cron's `--prompt` field into a `build_slot_prompt(date, slot, task_id)` function in `scripts/cli.py`. The per-slot runtime flow is unchanged — Claude still gets the same prompt and walks the same 6 steps. Only the *generator* of the prompt and description changes (cron → Python function).

## 5. Error Handling

### 5.1 Exit codes

| Code | Meaning | Trigger |
|---|---|---|
| 0 | Success (including "no-op" cases) | All diff ops succeeded; or no remaining slots; or no focus set; or no diff to apply |
| 1 | Input / config error | `config/schedule.json` missing or malformed; `TODO_SCHEDULE_FILE` env override points to bad path; argparse failure |
| 2 | External dependency failure | DB not initialized; DB connection failed; `cc-connect timer list/add/del` subprocess failed or returned non-zero; cc-connect output unparseable |
| 3 | Not used | rebuild-timers has no "not found" semantics — empty plan/slot is a normal state, not an error |

### 5.2 stdout / stderr rules

- Success → stdout carries either the human-readable summary or the JSON, depending on `--json`.
- Failure → stdout is empty, stderr carries exactly one human-readable line.
- Errors are **never** mixed into the JSON output. Claude's parser sees a non-zero exit code, then reads stderr for the reason.

### 5.3 Idempotency

`rebuild-timers` is idempotent by construction:
- After the first successful run, `actual` matches `planned` exactly.
- The second run computes `to_add=[]` and `to_remove=[]`, returns `kept=K`, exits 0.
- No external state change on no-op runs.

This property is testable: `rebuild_timers_idempotent` runs the CLI twice in the same process and asserts the second run's stdout contains "added 0, removed 0".

### 5.4 No-side-effects guarantee

| Phase | DB writes | cc-connect writes |
|---|---|---|
| Read focus, parse config, compute planned | none | none |
| Read actual via `cc_timers.list_today_remaining` | none | none (list is read-only) |
| Compute diff (`reconcile_timers`) | none | none |
| Apply (`cc_timers.add` / `.del`) | none | yes (this is the only mutating phase) |
| Render summary | none | none |

If any phase before "Apply" fails, **no** cc-connect writes occur — guaranteed by code structure. The DB is never written to in any phase.

### 5.5 Partial-failure handling during apply

If a `cc_timers.add` or `.del` call fails mid-apply (e.g., transient cc-connect failure on the 3rd timer):
- Continue with the remaining add/del operations.
- Collect all failures into a list.
- After all ops attempted, if any failed: print a summary of failures to stderr, exit 2.
- Acceptable partial state: at most one or two orphaned timers (rare since cc-connect is usually reliable). The next run of `rebuild-timers` will reconcile them.

### 5.6 Edge cases (all exit 0)

| Case | Behavior |
|---|---|
| `now` past the last slot of the day (e.g., 22:00 run) | planned is empty, actual is empty → stdout "no remaining slots today", summary `{added: 0, removed: 0, kept: 0}` |
| `db.get_today_focus()` returns `None` | planned is empty (compute_schedule returns `[]` without focus) → stdout "no focus set, no timers scheduled" |
| A slot has no task assignable | That slot is skipped (no timer added, no expectation of an existing timer) |
| A planned slot is also in actual (already has a timer) | `kept`, no action |
| An `actual` timer is for a past slot | Filtered out before the diff (treated as already-fired) — kept in cc-connect to let it naturally fire (Claude will no-op) |
| A slot is "right now" (e.g., running at 12:00:30) | `slot.start_time <= now` → considered past, skipped. Prevents a race where a timer is added for a slot that's about to fire naturally |
| `compute_schedule` raises for a slot | That slot is skipped, others continue. Summary records `skipped: [{slot_start, error}]` for visibility. Other slots still get timers. |
| Foreign timer in cc-connect (description not "Todo scheduler: ...") | Returned in `foreign` list, never in `to_remove`. Shown in stdout as "ignored N (foreign)" for transparency |

## 6. Testing Strategy

**Mock strategy:** `scripts/cc_timers.py` detects env var `TODO_TEST_TIMER_FILE`. When set, it reads/writes a JSON file at that path (each timer is `{"id": str, "fire_at": ISO8601, "description": str}`). This is a separate code path from production (`subprocess.run` to `cc-connect`). Tests set the env var to point at `tmp_path / "timers.json"`; production never sets it.

**Time mocking:** use `freezegun.freeze_time(...)` to pin `datetime.now()` per test. Add `freezegun` to dev dependencies (test-only).

**`tests/test_cli.py` extensions** (no new test file):

### A. Unit tests for `reconcile_timers` (6 tests, no subprocess)

Import `reconcile_timers` directly from `scripts.cli`, call with synthetic inputs.

| Test | Input | Expected |
|---|---|---|
| `test_reconcile_empty_inputs` | planned=[], actual=[] | `{to_add: [], to_remove: []}` |
| `test_reconcile_planned_with_no_actual` | planned=2 slots, actual=[] | `to_add=2, to_remove=0` |
| `test_reconcile_actual_with_no_planned` | planned=[], actual=2 | `to_add=0, to_remove=2` |
| `test_reconcile_full_match` | planned==actual | `to_add=0, to_remove=0` |
| `test_reconcile_ignores_foreign_actual` | actual has non-`"Todo scheduler:"` description | not in to_remove |
| `test_reconcile_ignores_past_actual` | actual slot_start < now | not in to_remove (already-filtered) |

### B. Integration tests for `rebuild-timers` (9 tests, subprocess)

| Test | Setup | Assertion |
|---|---|---|
| `test_rebuild_timers_db_uninitialized` | empty db file, no `db.py init` | exit 2, stderr "Run `python scripts/db.py init` first" |
| `test_rebuild_timers_fresh_morning_5am` | now=05:00, focus set, all 4 weekday slots | exit 0, summary added=4, timer file has 4 entries |
| `test_rebuild_timers_partial_day_14pm` | now=14:00, focus set | exit 0, added=2 (18:00, 21:00), 12:00 filtered as past |
| `test_rebuild_timers_idempotent` | run twice, same now | second run: added=0, removed=0, kept=4 |
| `test_rebuild_timers_no_focus` | focus=None | exit 0, stdout contains "no focus set", timer file empty |
| `test_rebuild_timers_late_night_22pm` | now=22:00 | exit 0, stdout "no remaining slots today" |
| `test_rebuild_timers_json_output` | run with `--json` | stdout is `json.loads`-parseable, has `date, added, removed, kept, ignored_foreign, summary` |
| `test_rebuild_timers_stale_timer_removed` | timer file has 18:00 for old task T005, DB now has T001 at 18:00 (T001, T002 exist but 21:00 is not pre-seeded) | exit 0, removed=1 (T005), added=2 (T001, T002); the old 18:00 timer id is gone |
| `test_rebuild_timers_only_manages_own_timers` | timer file has a "User manual timer" alongside a `Todo scheduler` timer | exit 0, the foreign timer survives; stdout mentions "ignored 1 foreign" |

**Total new tests:** ~19 (10 unit + 9 integration). Existing 93 + 19 = ~112. Pre-existing tests in `test_cli.py` and `test_migrate.py` continue to pass unchanged.

**Manual smoke test checklist** (run after the test suite passes):
1. `python scripts/cli.py rebuild-timers` at any time → see summary printed.
2. `cc-connect timer list` → confirm the timers in the file match what's there.
3. Wait for a slot to fire → confirm the Feishu reminder arrives.
4. Change focus mid-day → run `rebuild-timers` again → confirm old timers are removed, new ones added.
5. Manually `cc-connect timer add --at "2026-08-04T20:00" --prompt "..."` to inject a foreign timer → run `rebuild-timers` → confirm foreign timer survives.

## 7. Compatibility & Non-Goals

**Compatibility:**
- **Existing per-slot reminder flow unchanged.** Timer fires → Claude session runs the same 6 steps → Feishu message goes out → next slot's timer is added (either by the slot's own prompt logic, or by the next morning's `rebuild-timers`).
- **`break_session.sh` and `simulate_reminder.sh` still work.** They're not part of the rebuild path; `rebuild-timers` doesn't call them.
- **Cron syntax unchanged.** Same schedule (`5 0 * * *`), same session (the existing one), same `cc-connect` daemon. Only the prompt text inside the cron job changes.
- **No DB schema changes.** rebuild-timers only reads from `db.py` (focus + tasks); never writes.
- **No new runtime dependencies.** argparse, json, re, sqlite3, subprocess, pathlib, datetime are all stdlib. `freezegun` is dev-only (test).
- **The morning cron still goes through Claude.** Claude's role shrinks from "interpret a 30-line prompt" to "run a script and report the result". The cron session-mode and the Feishu delivery mechanism are unchanged.

**Non-goals (explicitly out of scope for v1):**
- Rebuilding tomorrow's timers (only today is in scope).
- Auto-rebuilding on focus change (user runs `rebuild-timers` manually if they want a mid-day rebuild; cron handles the morning case).
- Replacing the per-slot "Claude session does 6 steps" with a one-shot shell script (would change runtime behavior; out of scope).
- Auto-detecting cc-connect daemon failure and recovering (cc-connect failures → exit 2 + stderr; Claude surfaces to user).
- Multiple-day planning (e.g., "if I miss 18:00 today, also queue it for tomorrow 12:00").
- Adjusting the cron schedule itself (still `5 0 * * *`; the user can change it manually).
- Shell completion (`argcomplete`).
- Internationalization of CLI messages.

## 8. Acceptance Criteria

1. `python scripts/cli.py rebuild-timers` on an initialized DB with a focus set, at 05:00 local, prints a summary listing 4 added timers (or however many slots `config/schedule.json` has for that day) and exits 0.
2. `python scripts/cli.py rebuild-timers --json` returns exit 0 with a single valid JSON object on stdout (parseable by `json.loads`) and no output on stderr.
3. Running `rebuild-timers` twice in a row produces `added=0, removed=0, kept=N` on the second run (idempotency).
4. Running `rebuild-timers` at 14:00 (after the 12:00 slot) adds only the 18:00 and 21:00 timers; the 12:00 timer is not re-added.
5. Running `rebuild-timers` at 22:00 (after all slots) prints "no remaining slots today" and exits 0; no timers added.
6. Running `rebuild-timers` with focus unset prints "no focus set, no timers scheduled" and exits 0; no timers added.
7. Running `rebuild-timers` against a foreign timer (description not starting with "Todo scheduler: ") leaves the foreign timer in cc-connect and reports it in `ignored_foreign` / "ignored N foreign" stdout.
8. The daily cron `5 0 * * *` is updated: its prompt is the single command `python scripts/cli.py rebuild-timers`. The cron's session-mode and other parameters are unchanged.
9. On an uninitialized DB, `rebuild-timers` exits 2 and prints one line on stderr containing "Run `python scripts/db.py init` first". No cc-connect writes occur.
10. If `config/schedule.json` is missing or malformed (e.g., `TODO_SCHEDULE_FILE` env var points to a bad path), `rebuild-timers` exits 1 and prints a clear error on stderr. No cc-connect writes occur.
11. `python -m pytest -q` reports 112/112 (93 prior + ~19 new: 10 unit + 9 integration).
12. The only files modified or created are: `scripts/cli.py` (add `rebuild-timers` subcommand + helpers including `parse_task_id_from_description`), `scripts/cc_timers.py` (new), `tests/test_cli.py` (add `TestReconcileTimers`, `TestSlotPromptHelpers`, `TestRebuildTimers` classes), `requirements-dev.txt` or equivalent (add `freezegun`), and the cron registration command (one-liner to update the prompt).
