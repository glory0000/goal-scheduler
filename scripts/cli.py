#!/usr/bin/env python3
"""Unified CLI for the todo scheduler.

Subcommands: status, today, goal add, task add, task update, focus,
rebuild-timers.
All errors go to stderr. Success goes to stdout as human text by default
or as a single JSON object when --json is set.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

# Make sibling scripts/ modules importable when run as a script.
SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import db  # noqa: E402
import scheduler  # noqa: E402
from cli_output import format_status_overview, format_today_view, to_json  # noqa: E402

# ---- shared constants ----

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VALID_STATUSES = ("pending", "in_progress", "done", "skipped")

DB_UNINIT_HINT = (
    "Error: database not initialized. "
    "Run `python scripts/db.py init` first."
)


# ---- helpers ----

def _require_initialized_db() -> None:
    """Exit 2 with the init hint if schema_version is absent."""
    try:
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name='schema_version'"
            ).fetchone()
            if row is None:
                _emit_error(DB_UNINIT_HINT, code=2)
    except sqlite3.DatabaseError as exc:
        _emit_error(f"Error: database error: {exc}", code=2)


def _emit_error(message: str, code: int) -> None:
    """Write a human-readable error to stderr and exit with `code`.

    Sets sys.exit so the call site is `raise SystemExit` from the
    perspective of control flow, but we use `sys.exit` directly so
    argparse / try blocks see a clean exit.
    """
    print(message, file=sys.stderr, flush=True)
    sys.exit(code)


def _validate_slug(slug: str) -> None:
    if not SLUG_RE.match(slug):
        _emit_error(
            f"Invalid slug: must match [a-z0-9][a-z0-9-]{{0,62}}.",
            code=1,
        )


# ---- status ----

def subcommand_status(args, as_json: bool) -> int:
    goals = db.list_goals(status="active")
    focus = db.get_today_focus()
    next_task = None
    # Ask the scheduler for one slot's worth of planning.
    plan = scheduler.compute_schedule(
        focus,
        date.today().isoformat(),
        datetime.now().strftime("%H:%M"),
        max_slots=1,
    )
    if plan:
        first = plan[0]
        task = db.get_task(first["task_id"])
        if task:
            next_task = {
                "task_id": task["id"],
                "slot_start": first["slot_start"],
                "title": task["title"],
            }
    if as_json:
        goals_data = [
            {
                "slug": g["slug"],
                "total": g["total_tasks"],
                "completed": g["completed_tasks"],
                "progress": int(round(
                    (g["completed_tasks"] or 0) * 100 / g["total_tasks"]
                )) if (g["total_tasks"] or 0) > 0 else 0,
            }
            for g in goals
        ]
        print(to_json({
            "goals": goals_data,
            "focus": focus,
            "next_task": next_task,
        }))
    else:
        print(format_status_overview(goals=goals, focus=focus,
                                     next_task=next_task))
    return 0


# ---- stubs for later tasks ----

def subcommand_today(args, as_json: bool) -> int:
    today = date.today()
    slots = scheduler.get_slots_for_date(today.isoformat())
    focus = db.get_today_focus()
    plan = scheduler.compute_schedule(
        focus, today.isoformat(), datetime.now().strftime("%H:%M"),
        max_slots=len(slots),
    )
    today_plan = [p for p in plan if p["date"] == today.isoformat()]
    assignments = {p["slot_start"]: p for p in today_plan}
    slot_rows = []
    scheduled_ids: set[str] = set()
    for slot in slots:
        a = assignments.get(slot["start"])
        if a is None:
            slot_rows.append({
                "slot_start": slot["start"], "slot_end": slot["end"],
                "task_id": None, "task_title": None,
                "goal_slug": None, "goal_name": None,
            })
            continue
        task = db.get_task(a["task_id"])
        goal = db.get_goal(a["goal_slug"])
        scheduled_ids.add(task["id"])
        slot_rows.append({
            "slot_start": slot["start"], "slot_end": slot["end"],
            "task_id": task["id"], "task_title": task["title"],
            "goal_slug": goal["slug"], "goal_name": goal["name"],
        })
    active_goals = db.list_goals(status="active")
    pending = [
        t for g in active_goals
        for t in db.list_tasks(goal_slug=g["slug"], status="pending")
    ]
    remaining = sum(t["id"] not in scheduled_ids for t in pending)
    weekday_cn = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")[
        today.weekday()
    ]
    if as_json:
        out_rows = [
            {"slot": f"{r['slot_start']}-{r['slot_end']}",
             "task": r["task_id"], "goal": r["goal_slug"]}
            for r in slot_rows
        ]
        print(to_json({
            "date": today.isoformat(),
            "weekday": weekday_cn,
            "focus_slug": focus,
            "slot_rows": out_rows,
            "remaining": remaining,
        }))
    else:
        nonempty = [r for r in slot_rows if r["task_id"]]
        print(format_today_view(
            date_str=today.isoformat(), weekday=weekday_cn,
            focus=focus, slot_rows=nonempty, remaining=remaining,
        ))
    return 0


def subcommand_goal_add(args, as_json: bool) -> int:
    _validate_slug(args.slug)
    if db.get_goal(args.slug) is not None:
        _emit_error(f"Goal '{args.slug}' already exists.", code=1)
    db.create_goal(args.slug, args.name, args.description or "")
    created = db.get_goal(args.slug)
    if as_json:
        print(to_json({
            "slug": created["slug"],
            "name": created["name"],
            "description": created["description"] or "",
            "status": created["status"],
            "created_at": created["created_at"],
        }))
    else:
        print(f"Goal '{args.slug}' created.")
    return 0


def subcommand_task_add(args, as_json: bool) -> int:
    # Validate task id format: <slug>-T<digits>
    m = re.match(r"^(?P<slug>[a-z0-9][a-z0-9-]{0,62})-T\d{3,}$", args.task_id)
    if not m:
        _emit_error(
            f"Invalid task id: must match '<slug>-T<digits>'.",
            code=1,
        )
    # The embedded slug must match args.goal_slug.
    if m.group("slug") != args.goal_slug:
        _emit_error(
            f"Task id '{args.task_id}' slug does not match "
            f"'{args.goal_slug}'.",
            code=1,
        )
    if args.hours < 0:
        _emit_error("Hours must be ≥ 0.", code=1)
    if db.get_goal(args.goal_slug) is None:
        _emit_error(f"Goal '{args.goal_slug}' not found.", code=3)
    if db.get_task(args.task_id) is not None:
        _emit_error(f"Task '{args.task_id}' already exists.", code=1)
    # Validate depends-on ids.
    for dep in args.depends_on:
        dep_task = db.get_task(dep)
        if dep_task is None or dep_task["goal_slug"] != args.goal_slug:
            _emit_error(
                f"Depends-on task '{dep}' not found in goal "
                f"'{args.goal_slug}'.",
                code=1,
            )
    db.create_task(
        args.task_id, args.goal_slug, args.sequence, args.title, "",
        args.hours, args.depends_on,
    )
    created = db.get_task(args.task_id)
    if as_json:
        print(to_json({
            "id": created["id"],
            "goal_slug": created["goal_slug"],
            "sequence": created["sequence"],
            "title": created["title"],
            "estimated_hours": created["estimated_hours"],
            "depends_on": created["depends_on"],
            "status": created["status"],
        }))
    else:
        print(f"Task {created['id']} created.")
    return 0


def subcommand_task_update(args, as_json: bool) -> int:
    if args.status not in VALID_STATUSES:
        _emit_error(
            f"Invalid status '{args.status}'. "
            f"Valid: {', '.join(VALID_STATUSES)}.",
            code=1,
        )
    task = db.get_task(args.task_id)
    if task is None:
        _emit_error(f"Task '{args.task_id}' not found.", code=3)
    if task["status"] == args.status:
        # Idempotent: no DB change, exit 0.
        if as_json:
            print(to_json({
                "id": task["id"],
                "status": task["status"],
                "started_at": task.get("started_at"),
                "completed_at": task.get("completed_at"),
                "updated_at": task.get("updated_at"),
            }))
        else:
            print(f"Task {task['id']} already {args.status} (no change).")
        return 0
    db.update_task_status(args.task_id, args.status)
    updated = db.get_task(args.task_id)
    if as_json:
        print(to_json({
            "id": updated["id"],
            "status": updated["status"],
            "started_at": updated.get("started_at"),
            "completed_at": updated.get("completed_at"),
            "updated_at": updated.get("updated_at"),
        }))
    else:
        suffix = ""
        if args.status == "in_progress" and updated.get("started_at"):
            suffix = f" at {updated['started_at']}"
        elif args.status == "done" and updated.get("completed_at"):
            suffix = f" at {updated['completed_at']}"
        print(f"Task {updated['id']} marked {args.status}{suffix}.")
    return 0


def subcommand_focus(args, as_json: bool) -> int:
    if args.focus_command == "set":
        _validate_slug(args.slug)
        if db.get_goal(args.slug) is None:
            _emit_error(f"Goal '{args.slug}' not found.", code=3)
        current = db.get_today_focus()
        if current == args.slug:
            if as_json:
                print(to_json({"focus": current}))
            else:
                print(f"Focus already '{current}' (no change).")
            return 0
        db.set_today_focus(args.slug)
        if as_json:
            print(to_json({"focus": args.slug}))
        else:
            print(f"Focus set to '{args.slug}'.")
        return 0
    if args.focus_command == "clear":
        current = db.get_today_focus()
        if current is None:
            if as_json:
                print(to_json({"focus": None}))
            else:
                print("Focus already unset (no change).")
            return 0
        db.set_today_focus(None)
        if as_json:
            print(to_json({"focus": None}))
        else:
            print("Focus cleared.")
        return 0
    _emit_error("Error: focus subcommand required.", code=1)
    return 1  # unreachable


# ---- parser ----

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="cli",
        description="Unified todo scheduler CLI",
    )
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON object on stdout")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Snapshot: goals, focus, next task")

    sub.add_parser("today",
                   help="Today's slots + assignments + remaining count")

    goal_p = sub.add_parser("goal", help="Goal operations")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)
    ga = goal_sub.add_parser("add", help="Add a new goal")
    ga.add_argument("slug")
    ga.add_argument("name")
    ga.add_argument("--description", default="")

    task_p = sub.add_parser("task", help="Task operations")
    task_sub = task_p.add_subparsers(dest="task_command", required=True)
    ta = task_sub.add_parser("add", help="Add a new task")
    ta.add_argument("task_id")
    ta.add_argument("goal_slug")
    ta.add_argument("sequence", type=int)
    ta.add_argument("title")
    ta.add_argument("--hours", type=float, default=0.0)
    ta.add_argument("--depends-on", action="append", default=[],
                    dest="depends_on")
    tu = task_sub.add_parser("update", help="Update a task's status")
    tu.add_argument("task_id")
    tu.add_argument("status")

    focus_p = sub.add_parser("focus", help="Today's focus")
    focus_sub = focus_p.add_subparsers(dest="focus_command", required=True)
    fs = focus_sub.add_parser("set", help="Set today's focus")
    fs.add_argument("slug")
    focus_sub.add_parser("clear", help="Clear today's focus")

    return p


# ---- main ----

def run(args: list[str]) -> int:
    parser = _build_parser()
    parsed = parser.parse_args(args)
    as_json = parsed.json

    _require_initialized_db()

    try:
        if parsed.command == "status":
            return subcommand_status(parsed, as_json)
        if parsed.command == "today":
            return subcommand_today(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "add":
            return subcommand_goal_add(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "add":
            return subcommand_task_add(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "update":
            return subcommand_task_update(parsed, as_json)
        if parsed.command == "focus":
            return subcommand_focus(parsed, as_json)
    except NotImplementedError as exc:
        _emit_error(f"Error: {exc}", code=1)
    except SystemExit:
        raise
    except Exception as exc:
        _emit_error(f"Error: {exc}", code=1)

    _emit_error("Error: no handler matched", code=1)
    return 1  # unreachable


def main() -> None:
    sys.exit(run(sys.argv[1:]))


# ---- rebuild-timers helpers (pure, no I/O) ----

import re as _re_rebuild  # local alias to keep the global imports untouched

_OWN_DESC_SLOT_RE = _re_rebuild.compile(
    r"^Todo scheduler: \d{4}-\d{2}-\d{2} (\d{2}:\d{2})"
)
_OWN_DESC_TASK_RE = _re_rebuild.compile(
    r" - ([a-z0-9][a-z0-9-]{0,62}-T\d{3,})$"
)


def parse_slot_start_from_description(description: str) -> str | None:
    """Extract 'HH:MM' from a 'Todo scheduler: <date> <HH:MM> <label> [- <task_id>]' description.

    Returns None if the description is not in our format (e.g., foreign timers
    or empty input). This is the only place that knows the description format.
    """
    if not description:
        return None
    m = _OWN_DESC_SLOT_RE.match(description)
    return m.group(1) if m else None


def parse_task_id_from_description(description: str) -> str | None:
    """Extract the task_id from the '- <task_id>' suffix of our own descriptions.

    Returns None if the description is missing the suffix (legacy timers or
    foreign entries). The matching shape is `<slug>-T<digits>` to stay
    consistent with task_add's validation.
    """
    if not description:
        return None
    m = _OWN_DESC_TASK_RE.search(description)
    return m.group(1) if m else None


def build_slot_description(
    date: str, slot_start: str, slot_label: str, task_id: str,
) -> str:
    """Build the cc-connect timer description for a slot we own.

    Format: 'Todo scheduler: <date> <HH:MM> <label> - <task_id>'.
    parse_slot_start_from_description must be able to recover slot_start from
    this string; parse_task_id_from_description must be able to recover task_id.
    """
    return f"Todo scheduler: {date} {slot_start} {slot_label} - {task_id}"


def build_slot_prompt(
    date: str, slot_start: str, slot_end: str, slot_label: str, task_id: str,
) -> str:
    """Build the natural-language prompt that a per-slot timer fires with.

    This is the same template the morning cron's per-slot prompt uses today
    (see the spec's §4.5). The first line is the user-facing title and embeds
    the task_id so parse_task_id_from_description can recover it from the
    description. The remaining lines walk Claude through the 6-step reminder
    flow.
    """
    # Use forward slashes regardless of OS — the prompt is interpreted by
    # bash on the cron side, and the morning cron's existing prompt uses
    # forward slashes too.
    repo_root = SCRIPTS_DIR.parent.as_posix()
    return (
        f"Free slot 启动: {date} {slot_start} {slot_label} "
        f"({slot_start}-{slot_end}) - {task_id}.\n"
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
    """Diff planned vs actual timer sets by (slot_start, task_id) tuple.

    planned: [{'slot_start', 'slot_end', 'slot_label', 'task_id', 'goal_slug'}, ...]
    actual:  [{'id', 'fire_at', 'description', 'slot_start', 'task_id'}, ...]
        (slot_start and task_id are parsed from the description;
         legacy timers and foreign timers have task_id=None and are excluded)

    Returns {'to_add': [planned entries without a (slot_start, task_id) match in actual],
             'to_remove': [actual entries without a (slot_start, task_id) match in planned]}.

    Actual entries with task_id=None are ignored — neither kept nor removed.
    Past actual entries are not the concern of this function; callers should
    pre-filter via cc_timers.list_today_remaining.
    """
    planned_keys = {
        (p["slot_start"], p["task_id"])
        for p in planned
    }
    actual_keys = {
        (a["slot_start"], a["task_id"])
        for a in actual
        if a.get("slot_start") is not None and a.get("task_id") is not None
    }
    to_add = [
        p for p in planned
        if (p["slot_start"], p["task_id"]) not in actual_keys
    ]
    to_remove = [
        a for a in actual
        if a.get("slot_start") is not None and a.get("task_id") is not None
        and (a["slot_start"], a["task_id"]) not in planned_keys
    ]
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


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
