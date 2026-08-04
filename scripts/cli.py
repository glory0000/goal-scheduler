#!/usr/bin/env python3
"""Unified CLI for the todo scheduler.

Subcommands: status, today, goal add, task add, task update, focus.
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
from cli_output import format_status_overview, to_json  # noqa: E402

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
    raise NotImplementedError("not yet implemented")


def subcommand_goal_add(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_task_add(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_task_update(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


def subcommand_focus(args, as_json: bool) -> int:
    raise NotImplementedError("not yet implemented")


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


if __name__ == "__main__":
    main()
