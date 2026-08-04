#!/usr/bin/env python3
"""Read-only web dashboard for the todo scheduler."""

import os
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, render_template, request

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import db
import format_utils
import scheduler

STATUS_LABELS = {
    "active": "进行中",
    "paused": "已暂停",
    "completed": "已完成",
    "archived": "已归档",
    "pending": "待办",
    "in_progress": "进行中",
    "done": "已完成",
    "skipped": "已跳过",
}


def _progress(total: int, completed: int) -> int:
    return int(round(completed * 100 / total)) if total else 0


def _goal_row(goal: dict) -> dict:
    tasks = db.list_tasks(goal_slug=goal["slug"])
    completed = sum(task["status"] == "done" for task in tasks)
    current = next(
        (task for task in tasks if task["status"] == "in_progress"),
        None,
    )
    return {
        "goal": goal,
        "total": len(tasks),
        "completed": completed,
        "progress": _progress(len(tasks), completed),
        "current": current,
    }


def _format_timestamp(value: str | None) -> str:
    return value.replace("T", " ") if value else "—"


def _task_row(task: dict) -> dict:
    dependencies = []
    for dependency_id in task["depends_on"]:
        dependency = db.get_task(dependency_id)
        dependencies.append({
            "id": dependency_id,
            "done": dependency is not None and dependency["status"] == "done",
        })
    started_at = task.get("started_at")
    completed_at = task.get("completed_at")
    try:
        elapsed = format_utils.format_elapsed(started_at, completed_at)
    except ValueError:
        elapsed = "—"
    return {
        "task": task,
        "dependencies": dependencies,
        "last_reminded": _format_timestamp(task["last_reminded_at"]),
        "started": _format_timestamp(started_at),
        "elapsed": elapsed,
        "completed": _format_timestamp(completed_at),
    }


WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _today_view(today_date: str) -> dict:
    slots = scheduler.get_slots_for_date(today_date)
    focus_slug = db.get_today_focus()
    focus_goal = db.get_goal(focus_slug) if focus_slug else None
    plan = scheduler.compute_schedule(
        focus_slug,
        today_date,
        "00:00",
        max_slots=len(slots),
    )
    today_plan = [item for item in plan if item["date"] == today_date]
    assignments = {item["slot_start"]: item for item in today_plan}

    slot_rows = []
    scheduled_ids = set()
    for slot in slots:
        assignment = assignments.get(slot["start"])
        task = db.get_task(assignment["task_id"]) if assignment else None
        goal = db.get_goal(assignment["goal_slug"]) if assignment else None
        dependencies = []
        task_label = None
        if task:
            scheduled_ids.add(task["id"])
            for dependency_id in task["depends_on"]:
                dependency = db.get_task(dependency_id)
                dependencies.append({
                    "id": dependency_id,
                    "done": dependency is not None and dependency["status"] == "done",
                })
            task_label = f"[{goal['name']}] {task['id']} - {task['title']}"
            if task.get("status") == "in_progress":
                try:
                    task_label += f"（已用 {format_utils.format_elapsed(task.get('started_at'))}）"
                except ValueError:
                    pass  # leave suffix off
        slot_rows.append({
            "slot": slot,
            "task": task,
            "goal": goal,
            "task_label": task_label,
            "dependencies": dependencies,
        })

    active_goals = db.list_goals(status="active")
    pending_tasks = [
        task
        for goal in active_goals
        for task in db.list_tasks(goal_slug=goal["slug"], status="pending")
    ]
    remaining = sum(task["id"] not in scheduled_ids for task in pending_tasks)
    return {
        "date": today_date,
        "weekday": WEEKDAY_LABELS[date.fromisoformat(today_date).weekday()],
        "focus_goal": focus_goal,
        "slot_rows": slot_rows,
        "has_assignments": bool(scheduled_ids),
        "remaining": remaining,
    }


def _stats_view() -> dict:
    goals = db.list_goals()
    tasks = db.list_tasks()
    completed_tasks = [task for task in tasks if task["status"] == "done"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent = []
    for task in completed_tasks:
        if not task["completed_at"]:
            continue
        completed_at = datetime.strptime(task["completed_at"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        if completed_at >= cutoff:
            recent.append({
                "task": task,
                "goal": db.get_goal(task["goal_slug"]),
                "completed": _format_timestamp(task["completed_at"]),
            })
    recent.sort(key=lambda row: row["task"]["completed_at"], reverse=True)

    return {
        "active_goals": sum(goal["status"] == "active" for goal in goals),
        "total_tasks": len(tasks),
        "completed_tasks": len(completed_tasks),
        "estimated_hours": sum(task["estimated_hours"] or 0 for task in tasks),
        "completed_estimated_hours": sum(task["estimated_hours"] or 0 for task in completed_tasks),
        "goal_rows": [_goal_row(goal) for goal in goals if goal["status"] == "active"],
        "recent": recent,
    }


def create_app() -> Flask:
    flask_app = Flask(__name__)
    flask_app.jinja_env.globals["status_label"] = STATUS_LABELS

    @flask_app.errorhandler(sqlite3.Error)
    def handle_database_error(exc: sqlite3.Error):
        flask_app.logger.exception(
            "Dashboard database read failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return render_template("error.html"), 500

    @flask_app.get("/")
    def index():
        show_all = request.args.get("all") == "1"
        status_filter = request.args.get("status")
        if status_filter:
            goals = db.list_goals(status=status_filter)
        elif show_all:
            goals = db.list_goals()
        else:
            # Default: show all non-archived goals (active + paused + completed).
            # Filter negatively so paused/completed remain visible.
            goals = [
                g for g in db.list_goals()
                if g["status"] != "archived"
            ]
        rows = [_goal_row(goal) for goal in goals]
        return render_template(
            "index.html",
            rows=rows,
            show_all=show_all,
            status_filter=status_filter,
        )

    @flask_app.get("/goal/<slug>")
    def goal_detail(slug: str):
        goal = db.get_goal(slug)
        if goal is None:
            return render_template("goal_detail.html", goal=None), 404

        tasks = db.list_tasks(goal_slug=slug)
        completed = sum(task["status"] == "done" for task in tasks)
        summary = {
            "total": len(tasks),
            "completed": completed,
            "progress": _progress(len(tasks), completed),
            "estimated_hours": sum(task["estimated_hours"] or 0 for task in tasks),
        }
        return render_template(
            "goal_detail.html",
            goal=goal,
            summary=summary,
            task_rows=[_task_row(task) for task in tasks],
            is_archived=(goal["status"] == "archived"),
        )

    @flask_app.get("/task/<task_id>")
    def task_detail(task_id: str):
        task = db.get_task(task_id)
        if task is None:
            return render_template("task_detail.html", task=None), 404
        parent = db.get_goal(task["goal_slug"])
        return render_template(
            "task_detail.html",
            task=task,
            parent=parent,
            is_archived=(task["status"] == "archived"),
        )

    @flask_app.get("/health")
    def health() -> Response:
        return Response("ok", mimetype="text/plain")

    @flask_app.get("/today")
    def today():
        return render_template("today.html", view=_today_view(date.today().isoformat()))

    @flask_app.get("/stats")
    def stats():
        return render_template("stats.html", view=_stats_view())

    return flask_app


def validate_database(path: str | os.PathLike | None = None) -> None:
    db_path = Path(path if path is not None else db.DB_PATH)
    if not db_path.is_file():
        raise FileNotFoundError(f"Todo database not found: {db_path}")


def main() -> int:
    try:
        validate_database()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    except OSError as exc:
        print(f"Error: unable to start dashboard on 0.0.0.0:5000: {exc}", file=sys.stderr)
        return 1
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
