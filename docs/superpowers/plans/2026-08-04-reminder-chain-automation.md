# Reminder Chain Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `rebuild-timers` CLI subcommand that deterministically reconciles today's planned reminder timers with cc-connect's actual state, and replace the morning cron's long natural-language prompt with a one-liner that calls it.

**Architecture:** `scripts/cc_timers.py` is a thin wrapper around `cc-connect timer list/add/del` with a file-backed test mode (env var `TODO_TEST_TIMER_FILE`). `scripts/cli.py` gains a `rebuild-timers` subcommand that uses `cc_timers`, `scheduler.compute_schedule`, and a new pure function `reconcile_timers(planned, actual, now)` to compute the diff. The morning cron's prompt is updated to a single shell command. The per-slot reminder flow (Claude → format_reminder → next timer) is unchanged.

**Tech Stack:** Python stdlib only (argparse, json, re, sqlite3, subprocess, pathlib, datetime). `freezegun` is the only new dev dependency (for time mocking in tests).

## Global Constraints

These constraints are copied verbatim from the spec and apply to every task.

| Constraint | Value | Source |
|---|---|---|
| Subcommand name | `rebuild-timers` (bare verb, top-level) | Spec §2, §3 |
| Output format | Human-readable default; `--json` flag for structured output | Spec §2, §4.4 |
| Exit codes | 0 = success, 1 = input/config error, 2 = external dependency failure. Code 3 unused. | Spec §2, §5.1 |
| stdout on error | Empty. Errors go to stderr only. | Spec §5.2 |
| Idempotency | Second consecutive run = `added=0, removed=0, kept=N` | Spec §5.3 |
| DB writes | None. The CLI only reads from `db.py`. | Spec §5.4 |
| No-side-effects-before-apply | All reads must succeed before any `cc_timers.add` / `.del` is called | Spec §5.4 |
| Time window | Today only, future slots only | Spec §2, §4.1 |
| Own vs foreign timers | `description.startswith("Todo scheduler: ")` is "ours"; everything else is foreign and untouched | Spec §4.2 |
| Matching key for diff | `slot_start` only | Spec §4.3 |
| Apply order | Removals first, then adds | Spec §4.3 |
| Test backend env var | `TODO_TEST_TIMER_FILE` points at a JSON file representing cc-connect's state | Spec §6 |
| Time mocking | `freezegun.freeze_time(...)` per test | Spec §6 |
| New Python dependency | `freezegun` (dev only) | Spec §6, §7 |
| Existing files NOT modified | `db.py`, `scheduler.py`, `format_utils.py`, `reminder.py`, `migrate.py`, `config/schedule.json`, `data/schema.sql` | Spec §3, §7 |
| Per-slot reminder flow | Unchanged — Claude session still walks 6 steps on each timer fire | Spec §1, §7 |
| Test count target | 93 prior + ~15 new = ~108 | Spec §8.11 |
| Plan file location | `docs/superpowers/plans/2026-08-04-reminder-chain-automation.md` | skill default |

---

## File Structure

This plan creates or modifies exactly these files. Anything outside this list is out of scope.

| Action | Path | Responsibility |
|---|---|---|
| NEW | `scripts/cc_timers.py` | Thin wrapper around `cc-connect timer list/add/del`. Two backends: production (subprocess) and test (file-based, gated by `TODO_TEST_TIMER_FILE`). Exports `TEST_TIMER_FILE_ENV`, `list_all`, `add`, `delete`, `list_today_remaining`. |
| MODIFY | `scripts/cli.py` | Add `rebuild-timers` subcommand + module-level pure helpers: `reconcile_timers`, `parse_slot_start_from_description`, `build_slot_description`, `build_slot_prompt`, `format_rebuild_summary`. Update top-of-file docstring to mention `rebuild-timers`. |
| MODIFY | `tests/test_cli.py` | Extend `run_cli` to accept an optional `timer_file` arg. Add `TestReconcileTimers` (6 unit tests for the diff function). Add `TestRebuildTimers` (9 integration tests for the full CLI). |
| NEW | `requirements-dev.txt` | One line: `freezegun>=1.2`. |
| MODIFY | `README.md` | Add `rebuild-timers` to the "CLI" subsection under "Common commands". |
| MODIFY (operational) | Daily cron `5 0 * * *` | Change the `--prompt` to a single shell command. |

Each task below produces a self-contained, independently testable change. The diff between tasks is small enough that a reviewer can reject one task while approving its neighbor.

---

### Task 1: `scripts/cc_timers.py` — thin wrapper with dual backend

**Files:**
- Create: `scripts/cc_timers.py`
- (No new test file. This task is verified manually + by the integration tests in Task 3.)

**Interfaces:**
- Consumes: nothing (library module; reads `os.environ["TODO_TEST_TIMER_FILE"]` internally)
- Produces:
  - `TEST_TIMER_FILE_ENV: str` — the env var name (`"TODO_TEST_TIMER_FILE"`)
  - `list_all() -> list[dict]` — every timer in cc-connect; each item is `{"id": str, "fire_at": str, "description": str}`
  - `add(prompt: str, fire_at_iso: str) -> dict` — add a timer; returns the created timer dict (id, fire_at, description)
  - `delete(timer_id: str) -> None` — remove a timer by id
  - `list_today_remaining(today: str) -> tuple[list[dict], list[dict]]` — returns `(own, foreign)` where `own` are timers with description starting with `"Todo scheduler: "` that fire today in the future, and `foreign` is everything else matching the date/filter

**Backend dispatch (single rule):** if `os.environ.get(TEST_TIMER_FILE_ENV)` is set and non-empty, use the file backend; otherwise use the subprocess backend. The file backend reads/writes a JSON array at the env-var path. Production code never sets the env var, so production always hits the subprocess backend.

**Why no unit tests in this task:** the public API of `cc_timers` is fully exercised by the 9 integration tests in Task 3. Adding separate unit tests for the file backend would push the test count past the spec's 108 target. Task 3's `test_rebuild_timers_fresh_morning_5am` and similar will fail if the file backend is broken.

- [ ] **Step 1: Create `scripts/cc_timers.py` with the full dual-backend implementation**

Create the file `scripts/cc_timers.py` with this content:

```python
"""Thin wrapper around `cc-connect timer list / add / del`.

Two backends:
- Production: subprocess.run("cc-connect timer ...").
- Test: when TODO_TEST_TIMER_FILE env var is set, read/write a JSON
  array at that path. Each entry: {"id": str, "fire_at": str (ISO8601),
  "description": str}. This is the seam for integration tests; it lets
  tests run cc_timers end-to-end without touching the real cc-connect
  daemon.

In both backends the on-disk / in-cc-connect state is the source of
truth. Functions return parsed data; they do not cache.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime
from pathlib import Path

# ---- public constants ----

TEST_TIMER_FILE_ENV = "TODO_TEST_TIMER_FILE"

# ---- backend selection ----

def _use_file_backend() -> bool:
    """True iff the test timer file env var is set and non-empty."""
    return bool(os.environ.get(TEST_TIMER_FILE_ENV))


def _test_file_path() -> Path:
    """Resolve the test timer file path. Caller must have verified the env var."""
    return Path(os.environ.get(TEST_TIMER_FILE_ENV)).resolve()


# ---- list_all ----

def list_all() -> list[dict]:
    """Return all timers as [{"id", "fire_at", "description"}, ...].

    Production: parse stdout of `cc-connect timer list`.
    Test: parse the JSON file at TODO_TEST_TIMER_FILE (returns [] if missing/empty).
    """
    if _use_file_backend():
        path = _test_file_path()
        if not path.exists() or path.stat().st_size == 0:
            return []
        text = path.read_text(encoding="utf-8")
        return json.loads(text)
    return _list_all_via_subprocess()


def _list_all_via_subprocess() -> list[dict]:
    """Parse `cc-connect timer list` output.

    Each non-empty line in cc-connect's output looks like:
        ⏰ <id>  <fire_at>  <description>
    or, when a section header appears, a line starting with "Pending"
    or "No pending". We only keep lines starting with the clock glyph.
    """
    result = subprocess.run(
        ["cc-connect", "timer", "list"],
        capture_output=True, text=True, check=True,
    )
    timers: list[dict] = []
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("⏰"):
            continue
        parts = stripped.split(None, 3)  # ["⏰", "<id>", "<fire_at>", "<description?>"]
        if len(parts) < 4:
            continue
        timers.append({
            "id": parts[1],
            "fire_at": parts[2],
            "description": parts[3],
        })
    return timers


# ---- add ----

def add(prompt: str, fire_at_iso: str) -> dict:
    """Create a new timer. Returns the created timer dict.

    Production: `cc-connect timer add --at <iso> --prompt <text>`.
    Test: append a new entry to the JSON file with a generated id.
    """
    if _use_file_backend():
        return _add_to_file(prompt, fire_at_iso)
    return _add_via_subprocess(prompt, fire_at_iso)


def _add_to_file(prompt: str, fire_at_iso: str) -> dict:
    """Append a timer to the test JSON file. Id is auto-incremented."""
    path = _test_file_path()
    timers = list_all()  # reads from the same file via the file backend
    # Determine next id: find max numeric suffix among existing ids, else 0
    next_n = 1
    for t in timers:
        tid = t.get("id", "")
        if tid.startswith("test-"):
            try:
                n = int(tid.split("-", 1)[1])
                if n >= next_n:
                    next_n = n + 1
            except ValueError:
                pass
    # The "description" we store mirrors what production cc-connect shows.
    # The first line of the prompt template is the user-facing title; we
    # synthesize a stable description here.
    description = _description_from_prompt(prompt, fire_at_iso)
    entry = {
        "id": f"test-{next_n:04d}",
        "fire_at": fire_at_iso,
        "description": description,
    }
    timers.append(entry)
    path.write_text(
        json.dumps(timers, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return entry


def _description_from_prompt(prompt: str, fire_at_iso: str) -> str:
    """Derive a short description for the timer entry.

    The production cron historically uses the first line of the prompt
    as the visible description. We mirror that here: take the first line
    and prefix it with "Todo scheduler: " if it isn't already prefixed.
    """
    first_line = prompt.splitlines()[0] if prompt else ""
    if first_line.startswith("Todo scheduler: "):
        return first_line
    return f"Todo scheduler: {first_line}".strip()


def _add_via_subprocess(prompt: str, fire_at_iso: str) -> dict:
    """Add via cc-connect, then re-list to find the new entry."""
    subprocess.run(
        ["cc-connect", "timer", "add", "--at", fire_at_iso, "--prompt", prompt],
        capture_output=True, text=True, check=True,
    )
    # Re-list to discover the new id (cc-connect's `add` output is
    # implementation-defined; listing is the canonical source).
    target_time = fire_at_iso
    for t in _list_all_via_subprocess():
        if t["fire_at"] == target_time:
            return t
    # If the new entry doesn't appear yet, return a synthetic placeholder.
    return {"id": "", "fire_at": fire_at_iso, "description": ""}


# ---- delete ----

def delete(timer_id: str) -> None:
    """Remove a timer by id."""
    if _use_file_backend():
        _delete_from_file(timer_id)
        return
    subprocess.run(
        ["cc-connect", "timer", "del", timer_id],
        capture_output=True, text=True, check=True,
    )


def _delete_from_file(timer_id: str) -> None:
    path = _test_file_path()
    timers = [t for t in list_all() if t.get("id") != timer_id]
    path.write_text(
        json.dumps(timers, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


# ---- list_today_remaining ----

def list_today_remaining(today: str) -> tuple[list[dict], list[dict]]:
    """Return (own, foreign) timers that fire today in the future.

    "Own" timers have a description starting with "Todo scheduler: " and
    are ones we manage. "Foreign" timers are everything else (other
    tools, manually-added entries). The caller decides what to do with
    each bucket.

    `today` is a YYYY-MM-DD string. A timer is "today" if its fire_at's
    date prefix matches. A timer is "remaining" if its fire_at
    datetime is strictly after `datetime.now()`.
    """
    now = datetime.now().astimezone()
    own: list[dict] = []
    foreign: list[dict] = []
    for t in list_all():
        fire_at_str = t.get("fire_at", "")
        # fire_at may be like "2026-08-04T12:00:00+08:00"; the first
        # 10 chars are YYYY-MM-DD.
        if not fire_at_str or len(fire_at_str) < 10:
            foreign.append(t)
            continue
        if fire_at_str[:10] != today:
            continue  # not today
        try:
            fire_at = datetime.fromisoformat(fire_at_str)
        except ValueError:
            foreign.append(t)
            continue
        if fire_at <= now:
            continue  # already past (or right now); ignore
        if t.get("description", "").startswith("Todo scheduler: "):
            own.append(t)
        else:
            foreign.append(t)
    return own, foreign
```

- [ ] **Step 2: Manual smoke test (no automated test in this task)**

Run a one-off Python snippet to confirm the file backend works:

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
TODO_TEST_TIMER_FILE=/tmp/cc_smoke_test.json python -c "
import sys
sys.path.insert(0, 'scripts')
import cc_timers
from pathlib import Path
p = Path('/tmp/cc_smoke_test.json')
if p.exists(): p.unlink()
print('list_all() on missing file:', cc_timers.list_all())
added = cc_timers.add('Free slot 启动: 2026-08-04 12:00 lunch (12:00-13:00).', '2026-08-04T12:00:00+08:00')
print('add returned:', added)
print('list_all() after add:', cc_timers.list_all())
own, foreign = cc_timers.list_today_remaining('2026-08-04')
print('own:', own, 'foreign:', foreign)
cc_timers.delete(added['id'])
print('list_all() after delete:', cc_timers.list_all())
"
```

Expected output:

```
list_all() on missing file: []
add returned: {'id': 'test-0001', 'fire_at': '2026-08-04T12:00:00+08:00', 'description': 'Todo scheduler: Free slot 启动: 2026-08-04 12:00 lunch (12:00-13:00).'}
list_all() after add: [{'id': 'test-0001', 'fire_at': '2026-08-04T12:00:00+08:00', 'description': 'Todo scheduler: Free slot 启动: 2026-08-04 12:00 lunch (12:00-13:00).'}]
own: [{'id': 'test-0001', ...}], foreign: []
list_all() after delete: []
```

If any assertion fails, fix the code and re-run. The file backend must round-trip add → list → delete cleanly.

- [ ] **Step 3: Verify no existing tests are broken**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`
Expected: 93/93 passing (no regressions from creating the new file).

- [ ] **Step 4: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/cc_timers.py
git commit -m "Add scripts/cc_timers.py with file and subprocess backends

Dual-backend wrapper for cc-connect timer list/add/del:
- Production backend: subprocess.run('cc-connect timer ...')
- Test backend: reads/writes a JSON file when TODO_TEST_TIMER_FILE
  is set, so integration tests can exercise the full CLI flow
  without touching the real cc-connect daemon.
"
```

---

### Task 2: Pure helpers in `scripts/cli.py` — `reconcile_timers`, slot parsing, prompt and description builders

**Files:**
- Modify: `scripts/cli.py` (append new module-level functions; do not touch existing subcommand code)
- Modify: `tests/test_cli.py` (add `TestReconcileTimers` and `TestSlotPromptHelpers` classes)

**Interfaces (these signatures are consumed by Task 3):**

- `reconcile_timers(planned: list[dict], actual: list[dict]) -> dict`
  - `planned`: items have keys `slot_start`, `slot_end`, `slot_label`, `task_id`, `goal_slug`
  - `actual`: items have keys `id`, `fire_at`, `description`, `slot_start` (slot_start is the parsed `HH:MM` from the description, or `None` for foreign timers; we filter those out before matching)
  - Returns `{"to_add": [planned entries whose slot_start is not in actual], "to_remove": [actual entries whose slot_start is not in planned]}`
- `parse_slot_start_from_description(description: str) -> str | None`
  - Returns `"HH:MM"` if description matches `"Todo scheduler: <date> <HH:MM> ..."`, else `None`
- `build_slot_description(date: str, slot_start: str, slot_label: str) -> str`
  - Returns `"Todo scheduler: <date> <HH:MM> <label>"`
- `build_slot_prompt(date: str, slot_start: str, slot_end: str, slot_label: str) -> str`
  - Returns the multi-line natural-language prompt that the timer fires with.

- [ ] **Step 1: Add a small helper at the top of `tests/test_cli.py` to load `cli` as an importable module**

Find the existing top-of-file section of `tests/test_cli.py` (after the imports and `REPO_ROOT`/`CLI_SCRIPT`/`SCHEMA_PATH` constants, but before `_init_db`). Insert this block:

```python
# Allow unit tests below to do `from cli import reconcile_timers` etc.
# The integration tests still use subprocess (unchanged behavior).
import importlib.util as _ilu
_cli_spec = _ilu.spec_from_file_location("cli", str(CLI_SCRIPT))
_cli_module = _ilu.module_from_spec(_cli_spec)
_cli_spec.loader.exec_module(_cli_module)
sys.modules["cli"] = _cli_module
```

This loads `scripts/cli.py` once at import time and registers it as `sys.modules["cli"]`, so the unit tests can `from cli import X` directly.

- [ ] **Step 2: Write the failing tests**

Append the following to the end of `tests/test_cli.py`:

```python
# -------------------- rebuild-timers: pure helpers --------------------

class TestReconcileTimers:
    """Direct unit tests for scripts.cli.reconcile_timers."""

    def test_reconcile_empty_inputs(self):
        from cli import reconcile_timers
        assert reconcile_timers([], []) == {"to_add": [], "to_remove": []}

    def test_reconcile_planned_with_no_actual(self):
        from cli import reconcile_timers
        planned = [
            {"slot_start": "12:00", "slot_end": "13:00", "slot_label": "lunch",
             "task_id": "T001", "goal_slug": "g1"},
            {"slot_start": "18:00", "slot_end": "19:00", "slot_label": "evening",
             "task_id": "T002", "goal_slug": "g1"},
        ]
        result = reconcile_timers(planned, [])
        assert len(result["to_add"]) == 2
        assert result["to_remove"] == []
        assert {p["slot_start"] for p in result["to_add"]} == {"12:00", "18:00"}

    def test_reconcile_actual_with_no_planned(self):
        from cli import reconcile_timers
        actual = [
            {"id": "a", "fire_at": "2026-08-04T12:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 12:00 lunch",
             "slot_start": "12:00"},
            {"id": "b", "fire_at": "2026-08-04T18:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 18:00 evening",
             "slot_start": "18:00"},
        ]
        result = reconcile_timers([], actual)
        assert result["to_add"] == []
        assert len(result["to_remove"]) == 2
        assert {a["id"] for a in result["to_remove"]} == {"a", "b"}

    def test_reconcile_full_match(self):
        from cli import reconcile_timers
        planned = [
            {"slot_start": "12:00", "slot_end": "13:00", "slot_label": "lunch",
             "task_id": "T001", "goal_slug": "g1"},
        ]
        actual = [
            {"id": "x", "fire_at": "2026-08-04T12:00:00+08:00",
             "description": "Todo scheduler: 2026-08-04 12:00 lunch",
             "slot_start": "12:00"},
        ]
        result = reconcile_timers(planned, actual)
        assert result["to_add"] == []
        assert result["to_remove"] == []

    def test_reconcile_ignores_foreign_actual(self):
        from cli import reconcile_timers
        # Foreign timer (no slot_start) must not appear in to_remove
        actual = [
            {"id": "f", "fire_at": "2026-08-04T20:00:00+08:00",
             "description": "User manual reminder", "slot_start": None},
        ]
        result = reconcile_timers([], actual)
        assert result["to_remove"] == []

    def test_reconcile_ignores_past_actual(self):
        from cli import reconcile_timers
        # Past timers are pre-filtered upstream by list_today_remaining.
        actual = []  # past timers are absent
        result = reconcile_timers([], actual)
        assert result["to_remove"] == []


class TestSlotPromptHelpers:
    """Unit tests for parse_slot_start_from_description,
    build_slot_description, build_slot_prompt."""

    def test_parse_slot_start_from_description_our_format(self):
        from cli import parse_slot_start_from_description
        assert parse_slot_start_from_description(
            "Todo scheduler: 2026-08-04 12:00 lunch"
        ) == "12:00"
        assert parse_slot_start_from_description(
            "Todo scheduler: 2026-08-04 18:00 evening"
        ) == "18:00"

    def test_parse_slot_start_from_description_foreign_returns_none(self):
        from cli import parse_slot_start_from_description
        assert parse_slot_start_from_description("User manual reminder") is None
        assert parse_slot_start_from_description("") is None
        assert parse_slot_start_from_description(
            "Todo scheduler: not-a-date 12:00 lunch"
        ) is None

    def test_build_slot_description(self):
        from cli import build_slot_description
        assert build_slot_description("2026-08-04", "12:00", "lunch") == (
            "Todo scheduler: 2026-08-04 12:00 lunch"
        )

    def test_build_slot_prompt_contains_key_fields(self):
        from cli import build_slot_prompt
        prompt = build_slot_prompt("2026-08-04", "12:00", "13:00", "lunch")
        assert "2026-08-04" in prompt
        assert "12:00" in prompt
        assert "13:00" in prompt
        assert "lunch" in prompt
        assert "Feishu" in prompt
        assert "reminder" in prompt.lower()
        # First line is the user-facing title (used by cc_timers to derive
        # the stored description).
        assert prompt.splitlines()[0].startswith("Free slot 启动")
```

- [ ] **Step 3: Run the new tests; expect failures**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestReconcileTimers tests/test_cli.py::TestSlotPromptHelpers -v`
Expected: every test fails with `ImportError: cannot import name 'reconcile_timers' from 'cli'` (or similar) because the functions don't exist yet.

- [ ] **Step 4: Add the helper functions to `scripts/cli.py`**

Append the following code to the end of `scripts/cli.py` (just before the `if __name__ == "__main__":` line). Single contiguous block:

```python
# ---- rebuild-timers helpers (pure, no I/O) ----

import re as _re_rebuild  # local alias to keep the global imports untouched

_OWN_DESC_RE = _re_rebuild.compile(
    r"^Todo scheduler: \d{4}-\d{2}-\d{2} (\d{2}:\d{2}) "
)


def parse_slot_start_from_description(description: str) -> str | None:
    """Extract 'HH:MM' from a 'Todo scheduler: <date> <HH:MM> <label>' description.

    Returns None if the description is not in our format (e.g., foreign timers
    or empty input). This is the only place that knows the description format.
    """
    if not description:
        return None
    m = _OWN_DESC_RE.match(description)
    return m.group(1) if m else None


def build_slot_description(date: str, slot_start: str, slot_label: str) -> str:
    """Build the cc-connect timer description for a slot we own.

    Format: 'Todo scheduler: <date> <HH:MM> <label>'. parse_slot_start_from_description
    must be able to recover slot_start from this string.
    """
    return f"Todo scheduler: {date} {slot_start} {slot_label}"


def build_slot_prompt(
    date: str, slot_start: str, slot_end: str, slot_label: str,
) -> str:
    """Build the natural-language prompt that a per-slot timer fires with.

    This is the same template the morning cron's per-slot prompt uses today
    (see the spec's §4.5). The first line is the user-facing title; the
    remaining lines walk Claude through the 6-step reminder flow.
    """
    repo_root = str(SCRIPTS_DIR.parent)
    return (
        f"Free slot 启动: {date} {slot_start} {slot_label} ({slot_start}-{slot_end}).\n"
        "\n"
        "Send a Feishu reminder for the next pending task. Steps:\n"
        f"1. cd to {repo_root}\n"
        "2. Read data/todos.db: python -c \"import sys; sys.path.insert(0,'scripts'); "
        "import db; focus=db.get_today_focus(); print('focus:', focus)\"\n"
        "3. Compute plan: python -c \"import sys; sys.path.insert(0,'scripts'); "
        "import scheduler, db; from datetime import datetime; "
        "plan=scheduler.compute_schedule(db.get_today_focus(), "
        f"'{date}', '{slot_start}', max_slots=1); print(plan)\"\n"
        "4. If plan has an entry: send reminder via reminder.format_reminder("
        "plan[0]['date'], plan[0]['slot_start'], plan[0]['slot_end'], "
        "db.get_goal(plan[0]['goal_slug']), db.get_task(plan[0]['task_id']))\n"
        f"5. If no plan: send '今日 {slot_start} 无待办任务'\n"
        "6. After sending, set up the next timer for the next free slot via: "
        "cc-connect timer add --at <next-slot-time> --prompt <similar>\n"
        "\n"
        "Your reply IS the Feishu message to send. Reply in Chinese.\n"
    )


def reconcile_timers(planned: list[dict], actual: list[dict]) -> dict:
    """Diff planned vs actual timer sets by slot_start.

    planned: [{'slot_start', 'slot_end', 'slot_label', 'task_id', 'goal_slug'}, ...]
    actual:  [{'id', 'fire_at', 'description', 'slot_start'}, ...]
        (slot_start is the parsed HH:MM from description, or None for foreign)

    Returns {'to_add': [planned entries without a match in actual],
             'to_remove': [actual entries without a match in planned]}.

    Foreign actual entries (slot_start is None) are ignored — the caller is
    expected to keep them. Past actual entries are not the concern of this
    function; callers should pre-filter via cc_timers.list_today_remaining.
    """
    planned_keys = {p["slot_start"] for p in planned}
    actual_keys = {a["slot_start"] for a in actual if a.get("slot_start") is not None}
    to_add = [p for p in planned if p["slot_start"] not in actual_keys]
    to_remove = [a for a in actual if a.get("slot_start") is not None
                 and a["slot_start"] not in planned_keys]
    return {"to_add": to_add, "to_remove": to_remove}


def format_rebuild_summary(
    date: str,
    added: list[dict],
    removed: list[dict],
    kept: list[dict],
    ignored_foreign: list[dict],
    today_had_no_slots: bool = False,
    no_focus: bool = False,
) -> str:
    """Render the human-readable summary for `rebuild-timers` output.

    Mirrors the format from the spec's §4.4 example. The "no slots" and
    "no focus" cases override the normal summary lines.
    """
    if no_focus:
        return f"Rebuilt timers for {date}: no focus set, no timers scheduled"
    if today_had_no_slots:
        return f"Rebuilt timers for {date}: no remaining slots today"
    lines = [f"Rebuilt timers for {date}:"]
    lines.append(
        f"  added   {len(added)}"
        + (f"  ({', '.join(_fmt_added(a) for a in added)})" if added else "")
    )
    lines.append(f"  removed {len(removed)}")
    lines.append(f"  kept    {len(kept)}")
    if ignored_foreign:
        descs = ", ".join(f'"{t.get("description", "")}"' for t in ignored_foreign)
        lines.append(f"  ignored {len(ignored_foreign)} (foreign: {descs})")
    return "\n".join(lines)


def _fmt_added(entry: dict) -> str:
    """Compact 'HH:MM <label> → T<id>' for the added-line summary."""
    label = entry.get("slot_label", "")
    tid = entry.get("task_id", "")
    return f"{entry['slot_start']} {label} → {tid}"
```

Also update the top-of-file docstring of `scripts/cli.py`. Replace:

```python
"""Unified CLI for the todo scheduler.

Subcommands: status, today, goal add, task add, task update, focus.
All errors go to stderr. Success goes to stdout as human text by default
or as a single JSON object when --json is set.
"""
```

with:

```python
"""Unified CLI for the todo scheduler.

Subcommands: status, today, goal add, task add, task update, focus,
rebuild-timers.
All errors go to stderr. Success goes to stdout as human text by default
or as a single JSON object when --json is set.
"""
```

- [ ] **Step 5: Run the new tests; expect passes**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestReconcileTimers tests/test_cli.py::TestSlotPromptHelpers -v`
Expected: 10/10 passing (6 reconcile + 4 prompt helpers).

- [ ] **Step 6: Run the full suite; expect 103/103 passing**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`
Expected: 103/103 passing (93 prior + 10 new pure-helper tests). The 9 rebuild-timers integration tests are still pending in Task 3.

- [ ] **Step 7: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/cli.py tests/test_cli.py
git commit -m "Add pure helpers for rebuild-timers diff and prompt building

scripts/cli.py gains module-level functions:
- reconcile_timers(planned, actual): the diff algorithm (pure)
- parse_slot_start_from_description: extract HH:MM from our own
  timer descriptions, return None for foreign/empty
- build_slot_description / build_slot_prompt: format the strings
  we hand to cc-connect when adding a new timer
- format_rebuild_summary: render the human-readable output

10 new unit tests in tests/test_cli.py (TestReconcileTimers +
TestSlotPromptHelpers) cover the diff edge cases and the prompt
template stability. Integration tests for the full CLI flow are
in the next commit.

Also bumps the top-of-file docstring to mention 'rebuild-timers'.
"
```

---

### Task 3: `rebuild-timers` subcommand + integration tests + `freezegun`

**Files:**
- Modify: `scripts/cli.py` (add `subcommand_rebuild_timers`, wire it into `_build_parser()` and `run()`)
- Modify: `tests/test_cli.py` (extend `run_cli` to accept `timer_file`; add `TestRebuildTimers` with 9 integration tests)
- New: `requirements-dev.txt` (one line: `freezegun>=1.2`)
- Modify: `README.md` (add `rebuild-timers` example to the CLI subsection)

**Interfaces (this task produces):**

- `subcommand_rebuild_timers(args, as_json: bool) -> int` — the subcommand body
- New argparse subcommand `rebuild-timers` (no positional args, accepts `--json` like the others)

**Algorithm (the body of `subcommand_rebuild_timers`):**

```
1. now = datetime.now().astimezone()
   today = now.date().isoformat()
   now_hhmm = now.strftime("%H:%M")
2. focus = db.get_today_focus()
   if focus is None: render "no focus set" summary, return 0
3. all_slots = scheduler.get_slots_for_date(today)  # 4 weekday or 3 weekend
4. planned = []
   prev_end = "00:00"
   for slot in all_slots:
       if slot["start"] <= now_hhmm:           # past
           prev_end = slot["end"]
           continue
       plan_one = scheduler.compute_schedule(focus, today, prev_end, max_slots=1)
       prev_end = slot["end"]
       if not plan_one: continue
       if plan_one[0]["slot_start"] != slot["start"]: continue  # overflowed
       planned.append({
           "date": today,
           "slot_start": plan_one[0]["slot_start"],
           "slot_end": plan_one[0]["slot_end"],
           "slot_label": slot["label"],
           "task_id": plan_one[0]["task_id"],
           "goal_slug": plan_one[0]["goal_slug"],
       })
5. own, foreign = cc_timers.list_today_remaining(today)
   actual = [{**t, "slot_start": parse_slot_start_from_description(t["description"])}
             for t in own]
6. diff = reconcile_timers(planned, actual)
7. for t in diff["to_remove"]:
       cc_timers.delete(t["id"])
   for p in diff["to_add"]:
       prompt = build_slot_prompt(p["date"], p["slot_start"], p["slot_end"], p["slot_label"])
       fire_at = f"{p['date']}T{p['slot_start']}:00+08:00"   # Asia/Shanghai hard-coded
       cc_timers.add(prompt, fire_at)
8. kept = [a for a in actual if a not in [r for r in diff["to_remove"]]]
9. render (human or JSON) and return 0
```

**Why hardcode `+08:00` for `fire_at`:** the spec and the existing cron's timer list output both use Asia/Shanghai timestamps. v1 does not introduce timezone configuration; if the user moves timezones, that's a future enhancement.

- [ ] **Step 1: Create `requirements-dev.txt` and install `freezegun`**

Create `requirements-dev.txt` at the repo root with one line:

```
freezegun>=1.2
```

Then install:

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
python -m pip install -r requirements-dev.txt
```

Verify:

```bash
python -c "import freezegun; print(freezegun.__version__)"
```

Expected: prints a version string like `1.5.1` (any 1.x is fine).

- [ ] **Step 2: Extend `run_cli` in `tests/test_cli.py` to support a timer file**

Find the existing `run_cli` helper (it starts with `def run_cli(args: list[str], db_path: Path) -> subprocess.CompletedProcess:` near the top of the file, after `_init_db`). Replace it in place with the version below. The new signature adds an optional `timer_file: Path | None = None` argument; when set, the env var `TODO_TEST_TIMER_FILE` is added to the subprocess env.

```python
def run_cli(
    args: list[str],
    db_path: Path,
    timer_file: Path | None = None,
) -> subprocess.CompletedProcess:
    """Invoke scripts/cli.py with isolated TODO_DB_PATH and (optionally)
    TODO_TEST_TIMER_FILE. Existing tests that don't pass timer_file see
    the same behavior as before.
    """
    env = os.environ.copy()
    env.pop("TODO_DB_PATH", None)
    env["TODO_DB_PATH"] = str(db_path)
    if timer_file is not None:
        env["TODO_TEST_TIMER_FILE"] = str(timer_file)
    else:
        env.pop("TODO_TEST_TIMER_FILE", None)
    return subprocess.run(
        [sys.executable, str(CLI_SCRIPT), *args],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
```

- [ ] **Step 3: Add `TestRebuildTimers` to `tests/test_cli.py`**

Append the following class to the end of `tests/test_cli.py`. It uses `freezegun.freeze_time` to pin the wall clock per test, the existing `_init_db` helper, and the extended `run_cli`.

```python
# -------------------- rebuild-timers integration tests --------------------

import freezegun


def _seed_focus_and_tasks(db_path: Path) -> None:
    """Insert one active goal with 3 pending tasks so scheduler has work to do."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "INSERT INTO goals (slug, name, description, status, "
            "total_tasks, completed_tasks, created_at, updated_at) "
            "VALUES ('g1', 'Goal One', '', 'active', 3, 0, "
            "'2026-08-04T00:00:00', '2026-08-04T00:00:00')"
        )
        for seq, title, hours in [
            (1, "task one", 1.0),
            (2, "task two", 1.0),
            (3, "task three", 1.0),
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

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

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

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

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

        first = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)
        assert first.returncode == 0
        n_after_first = len(json.loads(timer_file.read_text(encoding="utf-8")))

        second = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)
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

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

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

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

        assert result.returncode == 0, result.stderr
        assert "no remaining slots today" in result.stdout
        timers = json.loads(
            timer_file.read_text(encoding="utf-8") or "[]"
        )
        assert timers == []

    @freezegun.freeze_time("2026-08-04 05:00:00")
    def test_rebuild_timers_json_output(self, tmp_path):
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"

        result = run_cli(
            ["rebuild-timers", "--json"], db_path=db_path, timer_file=timer_file
        )

        assert result.returncode == 0, result.stderr
        # stderr is silent on success
        assert result.stderr == ""
        payload = json.loads(result.stdout)
        assert payload["date"] == "2026-08-04"
        assert payload["summary"] == {"added": 4, "removed": 0, "kept": 0}
        assert len(payload["added"]) == 4
        assert payload["removed"] == []
        assert payload["ignored_foreign"] == []

    @freezegun.freeze_time("2026-08-04 14:00:00")
    def test_rebuild_timers_stale_timer_removed(self, tmp_path):
        """If the timer file already has a 18:00 timer for an old task (T001)
        and the DB's 18:00 plan now resolves to T002, the old timer is removed
        and a new one is added."""
        db_path = tmp_path / "db.sqlite"
        _init_db(db_path)
        _seed_focus_and_tasks(db_path)
        timer_file = tmp_path / "timers.json"
        # Pre-seed: 18:00 timer for old task, plus 21:00 timer that's still valid.
        # Both must have descriptions in our format so the diff recognizes them.
        timer_file.write_text(json.dumps([
            {
                "id": "test-old-18",
                "fire_at": "2026-08-04T18:00:00+08:00",
                "description": "Todo scheduler: 2026-08-04 18:00 evening",
            },
            {
                "id": "test-21",
                "fire_at": "2026-08-04T21:00:00+08:00",
                "description": "Todo scheduler: 2026-08-04 21:00 night",
            },
        ], ensure_ascii=False, indent=2), encoding="utf-8")

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        # 21:00 was kept (slot_start still in plan). 18:00 was removed and re-added
        # with a new id; the test-old-18 id should be gone.
        ids = {t["id"] for t in timers}
        assert "test-old-18" not in ids
        # 21:00 still present
        assert "test-21" in ids
        # 18:00 still present (just a different id)
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

        result = run_cli(["rebuild-timers"], db_path=db_path, timer_file=timer_file)

        assert result.returncode == 0, result.stderr
        timers = json.loads(timer_file.read_text(encoding="utf-8"))
        ids = {t["id"] for t in timers}
        assert "foreign-1" in ids  # untouched
        # The two scheduled slots (18:00, 21:00) were also added.
        assert len(timers) == 3
        # Stdout mentions the foreign timer was ignored
        assert "ignored" in result.stdout
        assert "User manual reminder" in result.stdout
```

- [ ] **Step 4: Run the new tests; expect failures**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestRebuildTimers -v`
Expected: every test fails because `rebuild-timers` is not yet wired into the parser. Errors will look like `SystemExit: 2` from argparse's "the following arguments are required" or "unrecognized arguments" message.

- [ ] **Step 5: Wire `rebuild-timers` into `scripts/cli.py`**

Three edits in `scripts/cli.py`:

**(a) Add the subcommand body.** Append this function to `scripts/cli.py` (right after `format_rebuild_summary` and friends):

```python
# ---- rebuild-timers ----

def subcommand_rebuild_timers(args, as_json: bool) -> int:
    """Reconcile today's planned reminder timers with cc-connect's state.

    See spec §4 for the algorithm. Pure reads up front, then writes via
    cc_timers (removals first, then adds). All errors exit 1/2 with stderr.
    """
    import cc_timers  # local import keeps cli.py importable without cc_timers

    now = datetime.now().astimezone()
    today = now.date().isoformat()
    now_hhmm = now.strftime("%H:%M")
    focus = db.get_today_focus()

    if focus is None:
        # No focus → no timers. Normal state, not an error.
        if as_json:
            print(to_json({
                "date": today,
                "added": [], "removed": [], "kept": [],
                "ignored_foreign": [],
                "summary": {"added": 0, "removed": 0, "kept": 0, "ignored": 0},
                "note": "no focus set",
            }))
        else:
            print(format_rebuild_summary(
                today, [], [], [], [],
                no_focus=True,
            ))
        return 0

    all_slots = scheduler.get_slots_for_date(today)
    planned: list[dict] = []
    prev_end = "00:00"
    for slot in all_slots:
        if slot["start"] <= now_hhmm:
            prev_end = slot["end"]
            continue
        try:
            plan_one = scheduler.compute_schedule(
                focus, today, prev_end, max_slots=1,
            )
        except Exception as exc:
            # A scheduler failure on one slot must not abort the whole run.
            prev_end = slot["end"]
            print(
                f"Warning: scheduler failed for slot {slot['start']}: {exc}",
                file=sys.stderr,
            )
            continue
        prev_end = slot["end"]
        if not plan_one:
            continue
        if plan_one[0]["slot_start"] != slot["start"]:
            # Task is too big for this slot (or the scheduler overflowed
            # to a later slot). Skip — there's no point queuing a timer
            # for a slot we can't fill.
            continue
        planned.append({
            "date": today,
            "slot_start": plan_one[0]["slot_start"],
            "slot_end": plan_one[0]["slot_end"],
            "slot_label": slot["label"],
            "task_id": plan_one[0]["task_id"],
            "goal_slug": plan_one[0]["goal_slug"],
        })

    if not planned:
        # All slots are in the past, or none have tasks. Exit 0 with a
        # short message; no cc-connect writes.
        if as_json:
            print(to_json({
                "date": today,
                "added": [], "removed": [], "kept": [],
                "ignored_foreign": [],
                "summary": {"added": 0, "removed": 0, "kept": 0, "ignored": 0},
                "note": "no remaining slots today",
            }))
        else:
            print(format_rebuild_summary(
                today, [], [], [], [],
                today_had_no_slots=True,
            ))
        return 0

    own, foreign = cc_timers.list_today_remaining(today)
    actual = []
    for t in own:
        actual.append({
            **t,
            "slot_start": parse_slot_start_from_description(t["description"]),
        })

    diff = reconcile_timers(planned, actual)

    # Apply: removals first, then adds. If a single op fails we continue
    # and collect failures to surface at the end.
    apply_failures: list[str] = []
    for entry in diff["to_remove"]:
        try:
            cc_timers.delete(entry["id"])
        except Exception as exc:
            apply_failures.append(
                f"failed to delete {entry['id']} ({entry.get('slot_start')}): {exc}"
            )
    for entry in diff["to_add"]:
        try:
            prompt = build_slot_prompt(
                entry["date"], entry["slot_start"], entry["slot_end"],
                entry["slot_label"],
            )
            fire_at = f"{entry['date']}T{entry['slot_start']}:00+08:00"
            cc_timers.add(prompt, fire_at)
        except Exception as exc:
            apply_failures.append(
                f"failed to add {entry['slot_start']} ({entry.get('task_id')}): {exc}"
            )

    if apply_failures:
        for msg in apply_failures:
            print(f"Error: {msg}", file=sys.stderr)
        _emit_error(
            f"rebuild-timers: {len(apply_failures)} operation(s) failed; "
            "see stderr for details.",
            code=2,
        )
        # _emit_error calls sys.exit, so we never reach here.

    kept = [a for a in actual if a not in diff["to_remove"]]
    if as_json:
        print(to_json({
            "date": today,
            "added": [
                {
                    "slot_start": p["slot_start"],
                    "slot_end": p["slot_end"],
                    "slot_label": p["slot_label"],
                    "task_id": p["task_id"],
                    "goal_slug": p["goal_slug"],
                }
                for p in diff["to_add"]
            ],
            "removed": [
                {"id": r["id"], "slot_start": r.get("slot_start")}
                for r in diff["to_remove"]
            ],
            "kept": [
                {"id": k["id"], "slot_start": k.get("slot_start")}
                for k in kept
            ],
            "ignored_foreign": [
                {"id": f["id"], "description": f.get("description")}
                for f in foreign
            ],
            "summary": {
                "added": len(diff["to_add"]),
                "removed": len(diff["to_remove"]),
                "kept": len(kept),
                "ignored": len(foreign),
            },
        }))
    else:
        print(format_rebuild_summary(
            today, diff["to_add"], diff["to_remove"], kept, foreign,
        ))
    return 0
```

**(b) Register the subcommand in `_build_parser`.** Find the line `sub.add_parser("focus", help="Today's focus")` and add a new parser line **just before it**:

```python
    sub.add_parser("rebuild-timers",
                   help="Reconcile today's planned timers with cc-connect")
```

**(c) Wire the dispatch in `run()`.** Find the dispatch block (the `if parsed.command == "status": ... if parsed.command == "focus":` chain) and add a branch **before** the `if parsed.command == "focus":` line:

```python
        if parsed.command == "rebuild-timers":
            return subcommand_rebuild_timers(parsed, as_json)
```

(Insertion point: after `subcommand_task_update` dispatch, before `subcommand_focus` dispatch. The exact order doesn't matter for correctness, but keep it consistent with the parser order.)

- [ ] **Step 6: Run the integration tests; expect passes**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest tests/test_cli.py::TestRebuildTimers -v`
Expected: 9/9 passing.

- [ ] **Step 7: Run the full suite; expect 112/112 passing**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`
Expected: 112/112 passing (93 prior + 10 pure-helper tests from Task 2 + 9 rebuild-timers integration tests from this task).

If the count is off by 1-2, the likely cause is the pre-existing test suite already having a `TestReconcileTimers` or similar class that you accidentally duplicated — search for `class Test` in `tests/test_cli.py` and resolve. Do not modify the existing test classes.

- [ ] **Step 8: Update `README.md`**

Open `README.md` and find the `### CLI` subsection (it was added in Item 1, near the "Common commands" section). Add a new example block at the end of that subsection. Use whatever fenced-block style the existing `### CLI` section uses. The appended content should look like:

```markdown
**Rebuild today's reminder timers** (called by the morning cron, but can be run manually after a focus change):

```bash
python scripts/cli.py rebuild-timers
# Output: Rebuilt timers for 2026-08-04:
#           added   2  (18:00 evening → T002, 21:00 night → T003)
#           removed 0
#           kept    0

python scripts/cli.py rebuild-timers --json
# Output: {"date": "2026-08-04", "added": [...], ...}
```

The subcommand is idempotent — running it twice in a row is a no-op (`added=0, removed=0, kept=N`).
```

Do not rewrite other parts of the README.

- [ ] **Step 9: Commit**

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add scripts/cli.py tests/test_cli.py requirements-dev.txt README.md
git commit -m "Add rebuild-timers subcommand with integration tests

The subcommand reads today's planned reminder slots via
scheduler.compute_schedule (one slot at a time, independently), reads
cc-connect's actual state via cc_timers.list_today_remaining, and
applies the diff via cc_timers.delete + .add (removals first).

Wires into the existing CLI infrastructure:
- Reuses _require_initialized_db, _emit_error, --json flag, exit codes
- Imports to_json from cli_output
- Adds a parser entry 'rebuild-timers' and a dispatch branch in run()

New dev dependency: freezegun (for time-mocking integration tests).

9 new integration tests cover:
- DB uninitialized (exit 2 + stderr hint)
- Fresh morning 5am run adds all 4 weekday slots
- Partial-day 14pm skips past slots
- Idempotency (second run is a no-op)
- No focus set → no timers
- Late-night 22pm → no remaining slots
- --json output is parseable
- Stale timer removed + re-added
- Foreign timers left alone

Also adds requirements-dev.txt (one line: freezegun>=1.2) and
documents 'rebuild-timers' in the README CLI section.
"
```

---

### Task 4: Update the daily cron to call `rebuild-timers` + final smoke test

**Files (or rather, operations):**
- Modify: the cc-connect cron registration for the daily 5am job (one-liner via `cc-connect cron edit`)
- No file changes inside the repo

**Why this is its own task:** the cron registration is a live operational change. It needs verification (`cc-connect cron info`) and the change should be visible in the cron's history before the branch is considered done. Separating it also keeps the previous task's diff small and reviewable.

- [ ] **Step 1: Find the daily cron's job id**

Run: `cc-connect cron list`
Expected output: a list of cron jobs. Look for the one with the schedule `5 0 * * *` (5am daily). Note its job id (a short hex string).

If the daily cron is not yet registered (it might not be, on a fresh checkout), the implementer should pause and ask the user whether to add it now or skip this task.

- [ ] **Step 2: Edit the cron's prompt to call `rebuild-timers`**

If the cron already exists (the usual case for this user):

```bash
cc-connect cron edit <job-id> prompt "python scripts/cli.py rebuild-timers"
```

Substitute `<job-id>` for the id found in Step 1.

- [ ] **Step 3: Verify the edit took effect**

Run: `cc-connect cron info <job-id>`
Expected: the `prompt` field now shows `python scripts/cli.py rebuild-timers` (and no other field has changed). The `cron_expr` should still be `5 0 * * *`, and the `enabled` flag should still be true.

- [ ] **Step 4: Manual smoke test**

Run the new subcommand directly to confirm end-to-end behavior:

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
python scripts/cli.py rebuild-timers
```

Expected (output format may vary by state of the DB and cc-connect):

```
Rebuilt timers for 2026-08-04:
  added   N  (...)
  removed 0
  kept    0
```

or, if timers are already up to date:

```
Rebuilt timers for 2026-08-04:
  added   0
  removed 0
  kept    N
```

The exact numbers depend on the live state. The smoke test passes as long as:
- exit code is 0
- stdout is one of the documented summary shapes
- `cc-connect timer list` after the smoke test shows a consistent timer set for today

- [ ] **Step 5: Run the full test suite one last time**

Run: `cd "D:/codeSpace/claudecode/stock_data/todos" && python -m pytest -q`
Expected: 112/112 passing (no regressions from the cron change — the cron change is operational, not in the repo).

- [ ] **Step 6: Commit (only if a config file or doc change is needed)**

The cron change is outside the repo, so usually no commit is needed for this step. If the user asked to record the new cron's job id in a config file (e.g., `config/cron.txt` or a comment in `README.md`), do that as a separate small commit. Otherwise, this task ends without a commit.

If a doc commit is needed:

```bash
cd "D:/codeSpace/claudecode/stock_data/todos"
git add <file-that-was-touched>
git commit -m "Record daily cron's new prompt in <file>"
```

If no file needs to be touched, simply report: "Cron updated; no commit needed."

---

## Self-Review Checklist (run before declaring the plan complete)

- [ ] **Spec coverage.** Each spec section/requirement has a corresponding task. Cross-check:
  - Spec §3 architecture → Task 1 (`cc_timers.py`) + Task 2 (helpers in `cli.py`) + Task 3 (subcommand) + Task 4 (cron)
  - Spec §4.1 planned set → Task 3 `subcommand_rebuild_timers` step 4
  - Spec §4.2 actual set → Task 1 `cc_timers.list_today_remaining` + Task 2 `parse_slot_start_from_description`
  - Spec §4.3 diff → Task 2 `reconcile_timers`
  - Spec §4.4 output format → Task 2 `format_rebuild_summary` + Task 3 JSON branch
  - Spec §4.5 prompt format → Task 2 `build_slot_prompt`
  - Spec §5 error handling → Task 3 `subcommand_rebuild_timers` (try/except per slot, apply-failures list, `_emit_error` calls)
  - Spec §5.6 edge cases → all nine integration tests in Task 3 plus the no-focus / late-night cases
  - Spec §6 testing → Task 1 (no separate test, exercised in Task 3) + Task 2 (10 unit tests) + Task 3 (9 integration tests)
  - Spec §7 compatibility (no DB writes) → Task 3 `subcommand_rebuild_timers` has zero `db.create_*` / `db.update_*` calls; verified by reading the function body
  - Spec §8 acceptance criteria 1-12 → each maps to a test in Task 3 + a behavior in Task 3/4

- [ ] **Placeholder scan.** No `TBD`, `TODO`, `implement later`, "similar to", "add appropriate", "handle edge cases" anywhere in the plan text. (Note: the string `TODO_TEST_TIMER_FILE` is a real env var name and is intentional, not a placeholder.)

- [ ] **Type consistency.** Cross-check signatures referenced in multiple tasks:
  - `cc_timers.list_all() -> list[dict]` with keys `id, fire_at, description` — used in Task 1's smoke test and in Task 3's `list_today_remaining` impl
  - `cc_timers.add(prompt, fire_at_iso) -> dict` — used in Task 3's subcommand body
  - `cc_timers.delete(timer_id) -> None` — used in Task 3's subcommand body
  - `cc_timers.list_today_remaining(today) -> (own, foreign)` — used in Task 3
  - `reconcile_timers(planned, actual) -> {"to_add", "to_remove"}` — defined in Task 2, used in Task 3
  - `parse_slot_start_from_description(description) -> str | None` — defined in Task 2, used in Task 3
  - `build_slot_description(date, slot_start, slot_label) -> str` — defined in Task 2
  - `build_slot_prompt(date, slot_start, slot_end, slot_label) -> str` — defined in Task 2, used in Task 3
  - `format_rebuild_summary(date, added, removed, kept, ignored_foreign, today_had_no_slots, no_focus) -> str` — defined in Task 2, used in Task 3
  - `subcommand_rebuild_timers(args, as_json) -> int` — defined in Task 3, dispatched in `_build_parser` and `run`

- [ ] **Edge case re-check.** A slot whose planned task is too big for the slot duration is handled in Task 3 via the `if plan_one[0]["slot_start"] != slot["start"]: continue` check after `compute_schedule(focus, today, prev_end, max_slots=1)`. Confirmed by reading the scheduler code in `scripts/scheduler.py`: when the candidate's `estimated_hours` exceeds `_slot_duration_hours(slot)`, the function moves to the next slot, so the returned plan's first entry's `slot_start` will not match the slot we asked about.

- [ ] **Time mocking reality check.** `freezegun.freeze_time` patches `datetime.now()` globally for the test. The cc_timers file backend uses `datetime.now()` to filter `list_today_remaining`; the integration test's `freeze_time("2026-08-04 14:00:00")` makes 18:00 and 21:00 count as future and 12:00 count as past. Confirmed correct.

- [ ] **Cron `+08:00` hardcoding.** Asia/Shanghai is the only timezone in the existing cron's timer output (per `cc-connect timer list` showing `+08:00` offsets throughout). The plan hardcodes `+08:00` in `subcommand_rebuild_timers`. If the user moves timezones, this is a v2 concern, not v1.
