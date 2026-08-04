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
from dataclasses import dataclass, field
from pathlib import Path

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


# ---- file-I/O result ----

@dataclass
class SyncResult:
    """Outcome of one `sync_index_md` call."""

    path: Path
    synced_count: int
    by_status: dict[str, int] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    header_preserved: bool = False
    tasks_by_goal: dict[str, list[dict]] = field(default_factory=dict)


def _status_key_from_label(label: str) -> str | None:
    """Reverse STATUS_LABELS map (best-effort, used for change detection)."""
    for key, value in STATUS_LABELS.items():
        if value == label:
            return key
    return None


# Matches a fully-rendered index line, capturing name / slug / status / pct.
# Half-width colon is tolerated so hand-edited files still diff cleanly.
_RENDERED_LINE_RE = re.compile(
    r"^\s*-\s+\[(?P<name>[^\]]*)\]\((?P<slug>[^)]+)/goal\.md\)"
    r"\s+—\s+状态[：:](?P<status>[^—]+)—\s+完成率\s+(?P<pct>\d+)%"
)

# Matches just the link part of a line (slug extraction, no status needed).
_SLUG_LINE_RE = re.compile(r"^-\s+\[[^\]]*\]\((?P<slug>[^)]+)/goal\.md\)")

# The `## <label>` group headings we generate. `_split_header` stops at the
# first link line, so these headings land inside its "header" slice; they must
# be trimmed back off or every sync would duplicate them (spec: idempotency).
_GENERATED_HEADINGS = frozenset(f"## {STATUS_LABELS[s]}" for s in _GROUP_ORDER)


def _strip_generated_headings(header_text: str) -> str:
    """Truncate `header_text` at the first generated group heading.

    Everything from that heading onward is regenerated content, not the user's
    header. Returns the remainder with trailing newlines stripped.
    """
    lines = header_text.splitlines()
    for i, line in enumerate(lines):
        if line.strip() in _GENERATED_HEADINGS:
            return "\n".join(lines[:i]).rstrip("\n")
    return header_text


# ---- file-I/O entry point ----

def sync_index_md(goals_root: Path) -> SyncResult:
    """Read DB state, render goals/index.md, write atomically.

    Steps:
    1. Ensure `goals_root` exists.
    2. Read the existing index.md header (if any) via `_split_header`.
    3. Read all goals / tasks from the DB (no status filters).
    4. Warn about orphan goal dirs (no DB row) and DB goals whose goal.md
       is missing on disk. Orphans are skipped; missing goal.md still gets
       a rendered link.
    5. Render via `render_index_md`, diff per-slug lines against the old
       file to fill `changed` / `unchanged`.
    6. Write `index.md.tmp` then `Path.replace()` onto `index.md`.

    Raises OSError if the atomic write fails (caller decides the exit code).
    """
    # Lazy import so the pure-function layer stays importable without db.
    import db  # noqa: PLC0415

    goals_root = Path(goals_root)
    goals_root.mkdir(parents=True, exist_ok=True)
    index_path = goals_root / "index.md"

    # 1. Read the existing file once: header + old per-slug lines.
    header_text = ""
    header_preserved = False
    old_lines: dict[str, str] = {}
    if index_path.exists():
        old_text = index_path.read_text(encoding="utf-8")
        header_text, _ = _split_header(old_text)
        header_text = _strip_generated_headings(header_text)
        # Only genuine user content counts as a preserved header; a file that
        # held nothing but our own generated sections has no header to keep.
        header_preserved = bool(header_text)
        for line in old_text.splitlines():
            match = _RENDERED_LINE_RE.match(line)
            if not match:
                continue
            # Normalize via the current label map so a half-width colon or
            # stale label spelling isn't reported as a change.
            status_key = _status_key_from_label(match.group("status").strip())
            if status_key:
                slug = match.group("slug")
                old_lines[slug] = (
                    f"- [{match.group('name')}]({slug}/goal.md) — 状态："
                    f"{STATUS_LABELS[status_key]} — 完成率 {match.group('pct')}%"
                )

    # 2. Read DB.
    goals = db.list_goals()  # no status filter
    tasks = db.list_tasks()  # all goals
    tasks_by_goal = group_tasks_by_goal(tasks)

    # 3. Detect orphan dirs (no DB row) and DB goals missing goal.md.
    warnings: list[str] = []
    db_slugs = {g["slug"] for g in goals}
    for entry in sorted(goals_root.iterdir()):
        if not entry.is_dir():
            continue
        if entry.name not in db_slugs:
            warnings.append(
                f"goal dir 'goals/{entry.name}/' has no DB row — skipped"
            )
        elif not (entry / "goal.md").exists():
            warnings.append(f"goal '{entry.name}' has no goals/{entry.name}/goal.md")

    # 4. Render and diff against the old lines.
    rendered = render_index_md(goals, tasks_by_goal, header_text)
    new_lines: dict[str, str] = {}
    for line in rendered.splitlines():
        match = _SLUG_LINE_RE.match(line)
        if match:
            new_lines[match.group("slug")] = line

    all_slugs = set(old_lines) | set(new_lines)
    changed = sorted(s for s in all_slugs if old_lines.get(s) != new_lines.get(s))
    unchanged = sorted(all_slugs - set(changed))

    # 5. Atomic write.
    tmp_path = index_path.with_suffix(index_path.suffix + ".tmp")
    tmp_path.write_text(rendered, encoding="utf-8")
    tmp_path.replace(index_path)

    by_status: dict[str, int] = {s: 0 for s in _GROUP_ORDER}
    for g in goals:
        by_status[g["status"]] = by_status.get(g["status"], 0) + 1

    return SyncResult(
        path=index_path,
        synced_count=len(goals),
        by_status=by_status,
        changed=changed,
        unchanged=unchanged,
        warnings=warnings,
        header_preserved=header_preserved,
        tasks_by_goal=tasks_by_goal,
    )
