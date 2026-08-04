#!/usr/bin/env python3
"""Auto-sync goals/index.md from SQLite state.

Two layers:
- Pure functions (this file's top section): `render_index_md`,
  `compute_completion_pct`, `_split_header`, `_group_and_sort`,
  `group_tasks_by_goal`. No I/O; no DB access. Independently testable.
- File-I/O function (`sync_index_md`, Task 2): reads DB, calls pure
  functions, writes the file. Atomic write.

DB access happens only inside `sync_index_md` (Task 2). Pure functions
take goals / tasks as arguments so tests can drive them with fixtures.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

# ---- public constants ----

STATUS_LABELS: dict[str, str] = {
    "active": "进行中",
    "paused": "已暂停",
    "completed": "已完成",
}

_GROUP_ORDER: list[str] = ["active", "paused", "completed"]

# Matches a link line in goals/index.md: "- [<name>](<slug>/goal.md)".
# Whitespace at line start tolerated for robustness.
_LINK_LINE_RE = re.compile(r"^\s*-\s+\[.*\]\(.*/goal\.md\)")


# ---- pure helpers ----

def compute_completion_pct(tasks: list[dict]) -> int:
    """Integer percentage 0..100.

    Counts only tasks whose `status == "done"`. Returns 0 for empty input
    (avoids ZeroDivisionError; spec §4.3 mandates this).
    """
    total = len(tasks)
    if total == 0:
        return 0
    done = sum(1 for t in tasks if t.get("status") == "done")
    return round(done * 100 / total)


def _group_and_sort(goals: list[dict]) -> list[list[dict]]:
    """Bucket goals by status into the three fixed buckets, sorted by slug.

    Returns a 3-element list: [active_group, paused_group, completed_group].
    Each group is independently sorted by slug (Unicode codepoint order).
    Empty input yields three empty lists.
    """
    groups: dict[str, list[dict]] = {s: [] for s in _GROUP_ORDER}
    for g in goals:
        status = g.get("status", "")
        if status in groups:
            groups[status].append(g)
    return [sorted(groups[s], key=lambda g: g["slug"]) for s in _GROUP_ORDER]


def _split_header(content: str) -> tuple[str, int]:
    """Return (header_text, first_list_line_index).

    `header_text` is everything before the first line matching `_LINK_LINE_RE`,
    with trailing newlines stripped. `first_list_line_index` is the 0-based
    line index of the first link line, or `len(lines)` if none exists.

    Lines are split with `splitlines(keepends=True)` so the header content
    is preserved byte-for-byte (modulo the final newline strip).
    """
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if _LINK_LINE_RE.match(line):
            return ("".join(lines[:i]).rstrip("\n"), i)
    return (content.rstrip("\n"), len(lines))


def group_tasks_by_goal(tasks: Iterable[dict]) -> dict[str, list[dict]]:
    """Group a flat task list by their `goal_slug` field.

    Tasks missing `goal_slug` are skipped silently (defensive).
    """
    grouped: dict[str, list[dict]] = {}
    for t in tasks:
        slug = t.get("goal_slug")
        if not slug:
            continue
        grouped.setdefault(slug, []).append(t)
    return grouped


# ---- pure main entry point ----

def render_index_md(
    goals: list[dict],
    tasks_by_goal: dict[str, list[dict]],
    header_text: str,
) -> str:
    """Render the body of goals/index.md.

    Output structure (spec §4.2):
        <header_text with trailing newline stripped>
        <blank line if header_text is non-empty>
        ## 进行中
        - [<name>](<slug>/goal.md) — 状态：进行中 — 完成率 <P>%
        ... (sorted by slug)
        ## 已暂停     (only if non-empty)
        ...
        ## 已完成     (only if non-empty)
        ...
        <final \n>

    Empty groups emit no heading. Goal slugs without entries in
    `tasks_by_goal` are treated as having 0 tasks (completion 0%).
    """
    parts: list[str] = []
    if header_text:
        parts.append(header_text)
        parts.append("")  # blank line after header

    grouped = _group_and_sort(goals)
    for status_key, group_goals in zip(_GROUP_ORDER, grouped):
        if not group_goals:
            continue
        parts.append(f"## {STATUS_LABELS[status_key]}")
        parts.append("")  # blank line after heading
        for g in group_goals:
            slug = g["slug"]
            name = g["name"]
            label = STATUS_LABELS[status_key]
            pct = compute_completion_pct(tasks_by_goal.get(slug, []))
            parts.append(
                f"- [{name}]({slug}/goal.md) — 状态：{label} — 完成率 {pct}%"
            )
        parts.append("")  # blank line after group

    body = "\n".join(parts)
    # Strip trailing whitespace, then add a single trailing newline.
    return body.rstrip() + "\n"
