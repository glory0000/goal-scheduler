"""Render human-readable text and JSON for scripts/cli.py.

Pure formatters only — no I/O, no DB access. Each function takes plain
dicts/lists and returns a string.
"""

import json


def format_goal_row(goal: dict) -> str:
    """One line per goal: 'slug  done/total 完成  pct%   name'."""
    total = goal.get("total_tasks") or 0
    completed = goal.get("completed_tasks") or 0
    pct = int(round(completed * 100 / total)) if total > 0 else 0
    return f"{goal['slug']:<20}  {completed}/{total} 完成  {pct}%"


def format_task_row(assignment: dict) -> str:
    """One line per slot assignment: 'HH:MM-HH:MM  TID - title  [goal_slug]'."""
    return (
        f"{assignment['slot_start']}-{assignment['slot_end']}  "
        f"{assignment['task_id']} - {assignment['task_title']}  "
        f"[{assignment['goal_slug']}]"
    )


def format_status_overview(
    goals: list[dict],
    focus: str | None,
    next_task: dict | None,
) -> str:
    """Full status block: goals + focus + next task."""
    lines: list[str] = []
    if goals:
        lines.append(f"活跃目标 ({len(goals)}):")
        for g in goals:
            lines.append("  " + format_goal_row(g))
    else:
        lines.append("活跃目标: (无活跃目标)")
    lines.append(f"今日重点: {focus if focus else '未设置'}")
    if next_task:
        lines.append(
            f"下一任务:  {next_task['slot_start']}  "
            f"{next_task['task_id']} - {next_task['title']}"
        )
    else:
        lines.append("下一任务:  (无)")
    return "\n".join(lines)


def format_today_view(
    date_str: str,
    weekday: str,
    focus: str | None,
    slot_rows: list[dict],
    remaining: int,
) -> str:
    """Full today view: header + per-slot rows + remaining count."""
    lines = [f"{date_str} {weekday}"]
    lines.append(f"今日重点: {focus if focus else '未设置'}")
    for row in slot_rows:
        lines.append("  " + format_task_row(row))
    if remaining > 0:
        lines.append(f"今日剩余 {remaining} 个任务未安排")
    else:
        lines.append("今日剩余 0 个任务未安排")
    return "\n".join(lines)


def to_json(obj: dict | list) -> str:
    """Render a JSON string. ensure_ascii=False keeps Chinese readable."""
    return json.dumps(obj, ensure_ascii=False, indent=2)
