#!/usr/bin/env python3
"""Compute scheduling decisions: which task runs in which slot."""

import json
import os
from datetime import datetime, timedelta
import warnings

import db

SCHEDULE_PATH = os.environ.get("TODO_SCHEDULE_PATH", "config/schedule.json")


def load_schedule() -> dict:
    """Load the schedule config from disk."""
    with open(SCHEDULE_PATH, encoding="utf-8") as f:
        return json.load(f)


def is_weekend(date_str: str) -> bool:
    """True if the given YYYY-MM-DD is Sat or Sun."""
    dt = datetime.strptime(date_str, "%Y-%m-%d").date()
    return dt.weekday() >= 5  # Sat=5, Sun=6


def get_slots_for_date(date_str: str) -> list[dict]:
    """Return the slots applicable to this date."""
    sched = load_schedule()
    key = "weekend" if is_weekend(date_str) else "weekday"
    return sched[key]


def get_next_slot_after(date_str: str, time_str: str) -> tuple[str, str] | None:
    """Find the next (date, start_time) slot strictly after the given moment.

    Returns None if no slot in the next 7 days.
    """
    cur_date = datetime.strptime(date_str, "%Y-%m-%d").date()
    cur_time = datetime.strptime(time_str, "%H:%M").time()

    for offset in range(0, 7):
        check_date = (cur_date + timedelta(days=offset)).strftime("%Y-%m-%d")
        slots = get_slots_for_date(check_date)
        for slot in slots:
            slot_start = datetime.strptime(slot["start"], "%H:%M").time()
            if offset == 0 and slot_start <= cur_time:
                continue
            return (check_date, slot["start"])
    return None


def _slot_duration_hours(slot: dict) -> float:
    s = datetime.strptime(slot["start"], "%H:%M")
    e = datetime.strptime(slot["end"], "%H:%M")
    return (e - s).total_seconds() / 3600


def compute_schedule(
    today_focus: str | None,
    from_date: str,
    from_time: str,
    max_slots: int = 20,
) -> list[dict]:
    """Plan which task fills each upcoming free slot.

    Strategy:
      1. Focus goal's pending eligible tasks first (sequence order).
      2. Overflow to other active goals' eligible tasks, oldest-updated first.
      3. Skip tasks whose `estimated_hours` exceeds slot duration (warn).
      4. Stop when no eligible tasks remain or `max_slots` reached.

    Returns list of `{date, slot_start, slot_end, goal_slug, task_id}`.
    Empty list if nothing to schedule.
    """
    plan: list[dict] = []
    used_task_ids: set[str] = set()
    cur_date, cur_time = from_date, from_time

    active_goals = [g for g in db.list_goals(status="active")]

    for _ in range(max_slots):
        nxt = get_next_slot_after(cur_date, cur_time)
        if nxt is None:
            break
        slot_date, slot_start = nxt
        slot = next(
            s for s in get_slots_for_date(slot_date) if s["start"] == slot_start
        )
        slot_hours = _slot_duration_hours(slot)

        # Pick next task
        candidate = None
        overflowed: list[dict] = []

        # 1. Focus goal first
        if today_focus:
            focus = db.get_goal(today_focus)
            if focus and focus["status"] == "active":
                elig = [
                    t for t in db.list_eligible_tasks(goal_slug=today_focus)
                    if t["id"] not in used_task_ids
                ]
                elig.sort(key=lambda t: t["sequence"])
                for t in elig:
                    if (t["estimated_hours"] or 0) <= slot_hours:
                        candidate = t
                        break
                    else:
                        overflowed.append(t)

        # 2. Overflow to other goals (round-robin by oldest updated_at)
        if candidate is None:
            other_goals = sorted(
                [g for g in active_goals if g["slug"] != today_focus],
                key=lambda g: g["updated_at"],
            )
            for g in other_goals:
                elig = [
                    t for t in db.list_eligible_tasks(goal_slug=g["slug"])
                    if t["id"] not in used_task_ids
                ]
                elig.sort(key=lambda t: t["sequence"])
                for t in elig:
                    if (t["estimated_hours"] or 0) <= slot_hours:
                        candidate = t
                        break
                    else:
                        overflowed.append(t)
                if candidate:
                    break

        # Emit warnings per spec §6 rule 3
        for t in overflowed:
            msg = (
                f"task {t['id']} ('{t['title']}') estimated_hours="
                f"{t['estimated_hours']} exceeds slot duration {slot_hours}h "
                f"on {slot_date} {slot_start}-{slot['end']}; "
                f"consider splitting or overriding."
            )
            warnings.warn(msg, stacklevel=2)

        if candidate is None:
            # No more eligible tasks; stop planning.
            break

        plan.append({
            "date": slot_date,
            "slot_start": slot_start,
            "slot_end": slot["end"],
            "goal_slug": candidate["goal_slug"],
            "task_id": candidate["id"],
        })
        used_task_ids.add(candidate["id"])
        # Advance cursor to end of this slot (so next iteration finds the slot after)
        cur_date, cur_time = slot_date, slot["end"]

    return plan


if __name__ == "__main__":
    import sys
    if len(sys.argv) >= 2 and sys.argv[1] == "plan":
        focus = db.get_today_focus()
        now = datetime.now()
        plan = compute_schedule(focus, now.strftime("%Y-%m-%d"), now.strftime("%H:%M"))
        for p in plan:
            print(f"{p['date']} {p['slot_start']}-{p['slot_end']} "
                  f"{p['goal_slug']} {p['task_id']}")
