"""Thin wrapper around `cc-connect timer list / add / del`.

Two backends:
- Production: subprocess.run("cc-connect timer ...").
- Test: when TODO_TEST_TIMER_FILE env var is set, read/write a JSON
  array at that path. Each entry: {"id": str, "fire_at": str (ISO8601),
  "description": str}. This is the seam for integration tests; it lets
  tests exercise cc_timers end-to-end without touching the real cc-connect
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

def add(prompt: str, fire_at_iso: str, description: str | None = None) -> dict:
    """Create a new timer. Returns the created timer dict.

    If `description` is None (default), the test backend derives it from
    the prompt's first line via `_description_from_prompt` (existing
    behavior, used by ad-hoc callers). Production callers should always
    pass an explicit `description` (e.g., the one `build_slot_description`
    produces) so the value matches what `rebuild-timers` will later parse.

    Production: `cc-connect timer add --at <iso> --prompt <text> --desc <text>`.
    Test: append a new entry to the JSON file with a generated id.
    """
    if _use_file_backend():
        return _add_to_file(prompt, fire_at_iso, description)
    return _add_via_subprocess(prompt, fire_at_iso, description)


def _add_to_file(prompt: str, fire_at_iso: str, description: str | None) -> dict:
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
    if description is None:
        description = _description_from_prompt(prompt)
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


def _description_from_prompt(prompt: str) -> str:
    """Derive a short description for the timer entry.

    The production cron historically uses the first line of the prompt
    as the visible description. We mirror that here: take the first line
    and prefix it with "Todo scheduler: " if it isn't already prefixed.
    """
    first_line = prompt.splitlines()[0] if prompt else ""
    if first_line.startswith("Todo scheduler: "):
        return first_line
    return f"Todo scheduler: {first_line}".strip()


def _add_via_subprocess(
    prompt: str, fire_at_iso: str, description: str | None,
) -> dict:
    """Add via cc-connect, then re-list to find the new entry."""
    cmd = ["cc-connect", "timer", "add", "--at", fire_at_iso, "--prompt", prompt]
    if description is not None:
        cmd += ["--desc", description]
    subprocess.run(cmd, capture_output=True, text=True, check=True)
    # Re-list to discover the new id (cc-connect's `add` output is
    # implementation-defined; listing is the canonical source).
    for t in _list_all_via_subprocess():
        if t["fire_at"] == fire_at_iso:
            return t
    # If the new entry doesn't appear yet, return a synthetic placeholder.
    return {"id": "", "fire_at": fire_at_iso, "description": description or ""}


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
    import os
    test_now_str = os.environ.get("TEST_NOW_DATETIME")
    if test_now_str:
        now = datetime.fromisoformat(test_now_str).astimezone()
    else:
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
