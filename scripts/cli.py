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
from sync_md import sync_index_md, compute_completion_pct, STATUS_LABELS  # noqa: E402

# ---- shared constants ----

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
VALID_STATUSES = ("pending", "in_progress", "done", "skipped")

# Display labels for task statuses. Distinct from sync_md.STATUS_LABELS,
# which covers *goal* statuses (active/paused/completed/archived).
# 'archived' is listed here so `task list`/`task show` can render soft-deleted
# rows, but it stays out of VALID_STATUSES: `task delete` is the only way in,
# just as `goal delete` is for goals.
TASK_STATUS_LABELS: dict[str, str] = {
    "pending": "待办",
    "in_progress": "进行中",
    "done": "已完成",
    "skipped": "已跳过",
    "archived": "已归档",
}

DB_UNINIT_HINT = (
    "Error: database not initialized. "
    "Run `python scripts/db.py init` first."
)

GOALS_DIR = Path(os.environ.get("TODO_GOALS_DIR", "goals"))  # default = repo-root-relative; tests pass cwd=tmp_path


# ---- helpers ----

def _db_is_initialized(conn: sqlite3.Connection | None = None) -> bool:
    """Return True iff the `schema_version` table exists.

    When `conn` is None, opens its own connection via `db.get_conn()`
    and closes it. Used by both the user-facing
    `_require_initialized_db()` guard and the silent
    `_autosync_index_md()` no-op path. The helper exists because some
    test/embedded paths reach `_autosync_index_md()` without an
    initialized DB; without this, the helper would raise on every
    CLI call in those paths.
    """
    if conn is None:
        with db.get_conn() as probe_conn:
            return _db_is_initialized(probe_conn)
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name='schema_version'"
    ).fetchone()
    return row is not None


def _require_initialized_db() -> None:
    """Exit 2 with the standard init hint unless the DB is initialized."""
    try:
        if not _db_is_initialized():
            _emit_error(DB_UNINIT_HINT, code=2)
    except sqlite3.DatabaseError as exc:
        _emit_error(f"Error: database error: {exc}", code=2)


def _autosync_index_md() -> None:
    """Re-render goals/index.md after a successful CRUD op.

    Captures all exceptions and writes them to stderr. Never raises,
    never exits. The calling subcommand has already succeeded; sync
    failure must not roll back the user's operation. Silently no-ops
    when the DB is not initialized (some test paths reach here without
    a schema_version table).
    """
    try:
        if not _db_is_initialized():
            return
        sync_index_md(GOALS_DIR)
    except Exception as exc:
        print(f"warning: sync-md failed: {exc}", file=sys.stderr)


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
    _autosync_index_md()
    return 0


def subcommand_goal_list(args, as_json: bool) -> int:
    """List goals. Default excludes archived; --all includes them;
    --status filters to exactly one status (and wins over --all)."""
    if args.status:
        goals = db.list_goals(status=args.status)
    elif args.all:
        goals = db.list_goals()
    else:
        # Default: all non-archived goals (active + paused + completed).
        # Filter negatively so paused/completed remain visible.
        goals = [
            g for g in db.list_goals()
            if g["status"] != "archived"
        ]
    if as_json:
        print(to_json(goals))
        return 0
    if not goals:
        print("(no goals)")
        return 0
    for g in goals:
        label = STATUS_LABELS.get(g["status"], g["status"])
        print(f"- {g['slug']:<20} {label}")
    return 0


def subcommand_goal_show(args, as_json: bool) -> int:
    """Show one goal by slug. Exits 2 if not found."""
    goal = db.get_goal(args.slug)
    if goal is None:
        _emit_error(f"Goal '{args.slug}' not found.", code=2)
    if as_json:
        print(to_json(goal))
        return 0
    label = STATUS_LABELS.get(goal["status"], goal["status"])
    print(f"slug：       {goal['slug']}")
    print(f"name：       {goal['name']}")
    print(f"status：     {goal['status']} ({label})")
    print(f"description：{goal['description'] or '(none)'}")
    return 0


def subcommand_goal_update(args, as_json: bool) -> int:
    """Update a goal's status. Rejects 'archived' (that is `goal delete`'s
    job). Re-renders index.md only when the status actually changed."""
    if args.status == "archived":
        _emit_error(
            f"Cannot set status to 'archived'. "
            f"Use `goal delete {args.slug}` to archive a goal.",
            code=2,
        )
    current = db.get_goal(args.slug)
    if current is None:
        _emit_error(f"Goal '{args.slug}' not found.", code=2)
    changed = current["status"] != args.status
    if changed:
        db.update_goal_status(args.slug, args.status)
        _autosync_index_md()
    if as_json:
        print(to_json({
            "slug": args.slug, "status": args.status, "changed": changed,
        }))
    else:
        verb = "updated to" if changed else "already"
        print(f"Goal '{args.slug}' {verb} {args.status}.")
    return 0


def subcommand_goal_delete(args, as_json: bool) -> int:
    """Soft-delete: set status='archived'. Idempotent — re-deleting an
    already-archived goal exits 0 without re-rendering index.md."""
    try:
        changed = db.archive_goal(args.slug)
    except ValueError as exc:
        _emit_error(f"Error: {exc}", code=2)
    if changed:
        _autosync_index_md()
    if as_json:
        print(to_json({"slug": args.slug, "archived": changed}))
    else:
        verb = "archived" if changed else "already archived"
        print(f"Goal '{args.slug}' {verb}.")
    return 0


def subcommand_goal_restore(args, as_json: bool) -> int:
    """Restore an archived goal to 'active'. Strict: exits 2 if the goal is
    missing or is not currently archived."""
    try:
        db.restore_goal(args.slug)
    except ValueError as exc:
        _emit_error(f"Error: {exc}", code=2)
    _autosync_index_md()
    if as_json:
        print(to_json({"slug": args.slug, "status": "active"}))
    else:
        print(f"Goal '{args.slug}' restored to active.")
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
        args.task_id, args.goal_slug, args.sequence, args.title, args.description,
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
    _autosync_index_md()
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
    _autosync_index_md()
    return 0


def subcommand_task_list(args, as_json: bool) -> int:
    """List tasks. Default excludes archived; --all includes them;
    --status filters to exactly one status (and wins over --all).
    --goal narrows to one goal."""
    if args.status:
        tasks = db.list_tasks(goal_slug=args.goal, status=args.status)
    elif args.all:
        tasks = db.list_tasks(goal_slug=args.goal)
    else:
        tasks = [
            t for t in db.list_tasks(goal_slug=args.goal)
            if t["status"] != "archived"
        ]
    if as_json:
        print(to_json(tasks))
        return 0
    if not tasks:
        print("(no tasks)")
        return 0
    for t in tasks:
        label = TASK_STATUS_LABELS.get(t["status"], t["status"])
        print(f"- {t['id']:<20} [{t['goal_slug']}] {label}  {t['title']}")
    return 0


def subcommand_task_show(args, as_json: bool) -> int:
    """Show one task by id. Exits 2 if not found."""
    task = db.get_task(args.task_id)
    if task is None:
        _emit_error(f"Task '{args.task_id}' not found.", code=2)
    if as_json:
        print(to_json(task))
        return 0
    label = TASK_STATUS_LABELS.get(task["status"], task["status"])
    print(f"id：         {task['id']}")
    print(f"goal：       {task['goal_slug']}")
    print(f"sequence：   {task['sequence']}")
    print(f"title：      {task['title']}")
    print(f"hours：      {task.get('estimated_hours')}")
    print(f"status：     {task['status']} ({label})")
    return 0


def subcommand_task_delete(args, as_json: bool) -> int:
    """Soft-delete: set status='archived'. Idempotent — re-deleting an
    already-archived task exits 0 without re-rendering index.md."""
    try:
        changed = db.archive_task(args.task_id)
    except ValueError as exc:
        _emit_error(f"Error: {exc}", code=2)
    if changed:
        _autosync_index_md()
    if as_json:
        print(to_json({"task_id": args.task_id, "archived": changed}))
    else:
        verb = "archived" if changed else "already archived"
        print(f"Task '{args.task_id}' {verb}.")
    return 0


def subcommand_task_restore(args, as_json: bool) -> int:
    """Restore an archived task to 'pending'. Strict: exits 2 if the task is
    missing or is not currently archived."""
    try:
        db.restore_task(args.task_id)
    except ValueError as exc:
        _emit_error(f"Error: {exc}", code=2)
    _autosync_index_md()
    if as_json:
        print(to_json({"task_id": args.task_id, "status": "pending"}))
    else:
        print(f"Task '{args.task_id}' restored to pending.")
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

    # Shared parent so --json is accepted whether it appears before or after
    # the subcommand (argparse inherits the flag onto the subparser).
    # SUPPRESS keeps the subparser from clobbering a top-level --json with
    # False when the flag was given before the subcommand.
    json_parent = argparse.ArgumentParser(add_help=False)
    json_parent.add_argument(
        "--json", action="store_true", default=argparse.SUPPRESS,
        help="Emit a single JSON object on stdout",
    )

    sub.add_parser("status", help="Snapshot: goals, focus, next task")

    sub.add_parser("today",
                   help="Today's slots + assignments + remaining count")

    goal_p = sub.add_parser("goal", help="Goal operations")
    goal_sub = goal_p.add_subparsers(dest="goal_command", required=True)
    ga = goal_sub.add_parser("add", help="Add a new goal")
    ga.add_argument("slug")
    ga.add_argument("name")
    ga.add_argument("--description", default="")

    gl = goal_sub.add_parser("list", help="List goals (default: hide archived)",
                             parents=[json_parent])
    gl.add_argument("--status",
                    choices=["active", "paused", "completed", "archived"])
    gl.add_argument("--all", action="store_true", help="Include archived goals")

    gsh = goal_sub.add_parser("show", help="Show a single goal",
                              parents=[json_parent])
    gsh.add_argument("slug")

    gu = goal_sub.add_parser("update", help="Update a goal's status",
                             parents=[json_parent])
    gu.add_argument("slug")
    # 'archived' is accepted by the parser but rejected in the body so the
    # user gets an actionable hint ("use goal delete") instead of argparse's
    # bare "invalid choice".
    gu.add_argument(
        "--status",
        required=True,
        choices=["active", "paused", "completed", "archived"],
        help="New status (use 'goal delete' to archive)",
    )

    gd = goal_sub.add_parser("delete",
                             help="Soft-delete a goal (sets status=archived)",
                             parents=[json_parent])
    gd.add_argument("slug")

    gr = goal_sub.add_parser("restore", help="Restore an archived goal to active",
                             parents=[json_parent])
    gr.add_argument("slug")

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
    ta.add_argument("--description", default="",
                    help="Static howto (5-7 numbered steps); rendered in reminders")
    tu = task_sub.add_parser("update", help="Update a task's status")
    tu.add_argument("task_id")
    tu.add_argument("status")

    tl = task_sub.add_parser("list", help="List tasks (default: hide archived)",
                             parents=[json_parent])
    tl.add_argument("--goal", help="Only tasks belonging to this goal slug")
    tl.add_argument("--status", choices=list(TASK_STATUS_LABELS))
    tl.add_argument("--all", action="store_true", help="Include archived tasks")

    tsh = task_sub.add_parser("show", help="Show a single task",
                              parents=[json_parent])
    tsh.add_argument("task_id")

    td = task_sub.add_parser("delete",
                             help="Soft-delete a task (sets status=archived)",
                             parents=[json_parent])
    td.add_argument("task_id")

    tr = task_sub.add_parser("restore",
                             help="Restore an archived task to pending",
                             parents=[json_parent])
    tr.add_argument("task_id")

    sub.add_parser("rebuild-timers",
                   help="Reconcile today's planned timers with cc-connect")

    # Use a parents group so --json is accepted whether it appears before
    # or after the subcommand (argparse inherits the flag onto the subparser).
    sub.add_parser("sync-md",
                   help="Regenerate goals/index.md from current DB state",
                   parents=[json_parent])

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
    # `parsed.json` may not exist if the subparser consumed --json instead
    # of the top-level parser (e.g., for sync-md where --json can appear
    # after the subcommand). Prefer the top-level flag; fall back to the
    # subparser's parsed value.
    as_json = getattr(parsed, "json", False)

    _require_initialized_db()

    try:
        if parsed.command == "status":
            return subcommand_status(parsed, as_json)
        if parsed.command == "today":
            return subcommand_today(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "add":
            return subcommand_goal_add(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "list":
            return subcommand_goal_list(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "show":
            return subcommand_goal_show(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "update":
            return subcommand_goal_update(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "delete":
            return subcommand_goal_delete(parsed, as_json)
        if parsed.command == "goal" and parsed.goal_command == "restore":
            return subcommand_goal_restore(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "add":
            return subcommand_task_add(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "list":
            return subcommand_task_list(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "show":
            return subcommand_task_show(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "update":
            return subcommand_task_update(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "delete":
            return subcommand_task_delete(parsed, as_json)
        if parsed.command == "task" and parsed.task_command == "restore":
            return subcommand_task_restore(parsed, as_json)
        if parsed.command == "rebuild-timers":
            return subcommand_rebuild_timers(parsed, as_json)
        if parsed.command == "sync-md":
            return subcommand_sync_md(parsed, as_json)
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


# ---- rebuild-timers ----

def subcommand_rebuild_timers(args, as_json: bool) -> int:
    """Reconcile today's planned reminder timers with cc-connect's state.

    See spec §4 for the algorithm. Pure reads up front, then writes via
    cc_timers (removals first, then adds). All errors exit 1/2 with stderr."""
    import cc_timers  # local import keeps cli.py importable without cc_timers
    import os

    # For testing: allow overriding now via an environment variable
    test_now_str = os.environ.get("TEST_NOW_DATETIME")
    if test_now_str:
        from datetime import timezone
        now = datetime.fromisoformat(test_now_str).astimezone()
    else:
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
    try:
        # One call lets the scheduler hand out distinct tasks across all of
        # today's remaining slots (it keeps a local used_task_ids set per call).
        plan = scheduler.compute_schedule(
            focus, today, now_hhmm, max_slots=len(all_slots),
        )
    except Exception as exc:
        _emit_error(f"Error: scheduler.compute_schedule failed: {exc}", code=2)

    slots_by_start = {s["start"]: s for s in all_slots}
    planned: list[dict] = []
    for entry in plan:
        if entry["slot_start"] <= now_hhmm:
            continue  # past slot
        slot = slots_by_start.get(entry["slot_start"])
        if slot is None:
            continue  # safety; should not happen
        planned.append({
            "date": today,
            "slot_start": entry["slot_start"],
            "slot_end": entry["slot_end"],
            "slot_label": slot["label"],
            "task_id": entry["task_id"],
            "goal_slug": entry["goal_slug"],
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
    actual: list[dict] = []
    for t in own:
        actual.append({
            **t,
            "slot_start": parse_slot_start_from_description(t["description"]),
            "task_id": parse_task_id_from_description(t["description"]),
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
                entry["slot_label"], entry["task_id"],
            )
            description = build_slot_description(
                entry["date"], entry["slot_start"], entry["slot_label"],
                entry["task_id"],
            )
            fire_at = f"{entry['date']}T{entry['slot_start']}:00+08:00"
            cc_timers.add(prompt, fire_at, description=description)
        except Exception as exc:
            apply_failures.append(
                f"failed to add {entry['slot_start']} ({entry.get('task_id')}): {exc}"
            )

    if apply_failures:
        for msg in apply_failures:
            print(f"Error: {msg}", file=sys.stderr)
        _emit_error(
            f"rebuild-timers: {len(apply_failures)} operation(s) failed; "
            "see stderr for details",
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
                {"id": r["id"], "slot_start": r.get("slot_start"),
                 "task_id": r.get("task_id")}
                for r in diff["to_remove"]
            ],
            "kept": [
                {"id": k["id"], "slot_start": k.get("slot_start"),
                 "task_id": k.get("task_id")}
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


# ---- sync-md ----

def subcommand_sync_md(args, as_json: bool) -> int:
    """Regenerate goals/index.md from current SQLite state.

    Always full-sync (no --goal filter in v1). Atomic write; warnings
    (orphan dirs, missing goal.md) go to stderr but do not block exit 0.
    """
    result = sync_index_md(GOALS_DIR)
    for w in result.warnings:
        print(f"warning: {w}", file=sys.stderr)
    if as_json:
        print(to_json({
            "path": result.path.as_posix(),  # M10: forward slashes
            "synced_count": result.synced_count,
            "by_status": result.by_status,
            "changed": result.changed,
            "unchanged": result.unchanged,
            "warnings": result.warnings,
            "header_preserved": result.header_preserved,
        }))
    else:
        active_n = result.by_status.get("active", 0)
        paused_n = result.by_status.get("paused", 0)
        completed_n = result.by_status.get("completed", 0)
        # M10: forward slashes in human output.
        print(
            f"Synced {result.synced_count} goals to {result.path.as_posix()} "
            f"(active={active_n}, paused={paused_n}, completed={completed_n})"
        )
        # Per-goal lines: "- <marker><slug>  (<label> <pct>%)"
        # I4 marker logic:
        #   "+" → newly added (slug in result.added)
        #   "~" → pre-existing but rendered line differs (in result.changed)
        #   " " → unchanged
        # I5: mirror the renderer's grouping+sorted-by-slug so the human
        # summary order matches the file (active→paused→completed, then
        # slug-sorted within each group).
        added_set = set(result.added)
        changed_set = set(result.changed)
        group_order = ["active", "paused", "completed"]
        grouped = {s: [] for s in group_order}
        for g in result.goals:
            if g.get("status") in grouped:
                grouped[g["status"]].append(g)
        for status_key in group_order:
            for g in sorted(grouped[status_key], key=lambda x: x["slug"]):
                slug = g["slug"]
                label = STATUS_LABELS.get(g["status"], g["status"])  # M6
                pct = compute_completion_pct(
                    result.tasks_by_goal.get(slug, [])
                )
                if slug in added_set:
                    marker = "+"
                elif slug in changed_set:
                    marker = "~"
                else:
                    marker = " "
                print(f"- {marker}{slug:<16} ({label} {pct}%)")
    return 0


def main() -> None:
    sys.exit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
