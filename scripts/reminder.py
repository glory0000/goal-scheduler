#!/usr/bin/env python3
"""Format Feishu reminder messages."""

from format_utils import format_elapsed


def format_reminder(
    date_str: str,
    slot_start: str,
    slot_end: str,
    goal: dict,
    task: dict,
) -> str:
    """Build a reminder message following the spec §7 template."""
    hours = task.get("estimated_hours") or 0
    if hours == int(hours):
        hours_str = f"{int(hours)} 小时"
    else:
        hours_str = f"{hours:.1f} 小时"

    deps = task.get("depends_on") or []
    if deps:
        dep_lines = "\n".join(f"  - {d} ✓" for d in deps)
        deps_block = f"📎 依赖：\n{dep_lines}"
    else:
        deps_block = "📎 依赖：无"

    task_short_id = task["id"].split("-")[-1]  # strip slug prefix

    elapsed_suffix = ""
    if task.get("status") == "in_progress":
        try:
            elapsed_suffix = f"（已用 {format_elapsed(task.get('started_at'))}）"
        except ValueError:
            elapsed_suffix = ""

    # Build description block
    description_block = ""
    raw_desc = (task.get("description") or "").strip()
    if raw_desc:
        # Split on newlines, strip each, drop empties
        lines = [ln.strip() for ln in raw_desc.splitlines() if ln.strip()]
        if lines:
            # Render first 7 lines with numbering
            rendered = "\n".join(f"  {i+1}. {ln}" for i, ln in enumerate(lines[:7]))
            # Add extra lines notice if needed
            if len(lines) > 7:
                extra = len(lines) - 7
                rendered += f"\n  ... (+{extra} more — 回复 \"{task_short_id} 展开\" 查看完整版)"
            description_block = f"📝 步骤：\n{rendered}\n"

    return (
        f"⏰ {slot_start} 时段开始（{slot_start}-{slot_end}）\n"
        f"\n"
        f"📌 目标：{goal['name']}\n"
        f"🎯 任务：{task_short_id} - {task['title']}{elapsed_suffix}\n"
        f"{description_block}"
        f"⏱️ 预计耗时：{hours_str}\n"
        f"{deps_block}\n"
        f"\n"
        f"完成后请回复 \"{task_short_id} 完成了\"。\n"
        f"如需跳过请回复 \"跳过\"。\n"
        f"如需调整今日重点请回复 \"今日重点 = xxx\"。"
    )


if __name__ == "__main__":
    import sys
    import db

    if len(sys.argv) >= 2 and sys.argv[1] == "preview":
        task_id = sys.argv[2]
        task = db.get_task(task_id)
        if task is None:
            print(f"Task {task_id} not found")
            sys.exit(1)
        goal = db.get_goal(task["goal_slug"])
        print(format_reminder("2026-08-04", "21:00", "23:00", goal, task))
