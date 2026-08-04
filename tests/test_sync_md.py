"""Tests for scripts/sync_md.py — pure-function and file-I/O layers."""

from __future__ import annotations

import pytest

# Import the module under test. We do this via sys.path manipulation so the
# test file matches the cli test layout (which uses subprocess, but the
# pure-function tests here import directly for speed).
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = REPO_ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import sync_md  # noqa: E402


# -------------------- TestRenderIndexMd --------------------

class TestRenderIndexMd:
    def test_empty_goals_renders_only_header(self):
        result = sync_md.render_index_md(
            goals=[],
            tasks_by_goal={},
            header_text="# My Header\n\nSome intro text.",
        )
        # Header preserved (trailing newline stripped), no headings.
        assert result.startswith("# My Header\n\nSome intro text.")
        assert "## 进行中" not in result
        assert "## 已暂停" not in result
        assert "## 已完成" not in result

    def test_single_active_goal(self):
        goals = [
            {"slug": "example", "name": "示例目标", "status": "active"},
        ]
        tasks_by_goal = {
            "example": [
                {"id": "example-T001", "status": "done"},
                {"id": "example-T002", "status": "pending"},
            ],
        }
        result = sync_md.render_index_md(goals, tasks_by_goal, header_text="")
        assert "## 进行中" in result
        assert "- [示例目标](example/goal.md) — 状态：进行中 — 完成率 50%" in result
        # Status appears in the line; the heading itself does too.
        assert result.count("进行中") >= 1

    def test_groups_in_order_active_paused_completed(self):
        goals = [
            {"slug": "comp-goal", "name": "C", "status": "completed"},
            {"slug": "active-goal", "name": "A", "status": "active"},
            {"slug": "paused-goal", "name": "P", "status": "paused"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        # Heading order: active before paused before completed.
        active_pos = result.index("## 进行中")
        paused_pos = result.index("## 已暂停")
        completed_pos = result.index("## 已完成")
        assert active_pos < paused_pos < completed_pos

    def test_empty_group_section_omitted(self):
        goals = [
            {"slug": "active-only", "name": "A", "status": "active"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        assert "## 进行中" in result
        assert "## 已暂停" not in result
        assert "## 已完成" not in result

    def test_completion_pct_with_zero_tasks(self):
        assert sync_md.compute_completion_pct([]) == 0

    def test_sort_within_group_by_slug(self):
        goals = [
            {"slug": "zebra", "name": "Z", "status": "active"},
            {"slug": "alpha", "name": "A", "status": "active"},
            {"slug": "mike", "name": "M", "status": "active"},
        ]
        result = sync_md.render_index_md(goals, {}, header_text="")
        # The three lines, in order, should reference alpha, mike, zebra.
        alpha_pos = result.index("[A](alpha/goal.md)")
        mike_pos = result.index("[M](mike/goal.md)")
        zebra_pos = result.index("[Z](zebra/goal.md)")
        assert alpha_pos < mike_pos < zebra_pos
