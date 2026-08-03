import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import reminder


def test_format_reminder_basic():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器基础架构",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "pending",
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "21:00" in msg
    assert "21:00-23:00" in msg
    assert "A股量化" in msg
    assert "实现数据采集器基础架构" in msg
    assert "2 小时" in msg
    assert "T001 完成了" in msg


def test_format_reminder_with_deps():
    goal = {"slug": "video", "name": "视频剪辑"}
    task = {
        "id": "video-T003",
        "title": "学习 Premiere 转场",
        "estimated_hours": 1.0,
        "depends_on": ["video-T001", "video-T002"],
        "status": "pending",
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="19:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "依赖" in msg
    assert "T001" in msg
    assert "T002" in msg
    assert "✓" in msg


def test_format_reminder_appends_elapsed_for_in_progress():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "in_progress",
        "started_at": "2026-08-04T07:00:00",
        "completed_at": None,
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    # The suffix appears on the task title line specifically.
    task_lines = [ln for ln in msg.splitlines() if ln.startswith("🎯 任务：")]
    assert len(task_lines) == 1
    assert "（已用" in task_lines[0]
    assert task_lines[0].endswith("）")


def test_format_reminder_no_elapsed_suffix_for_pending():
    goal = {"slug": "a-stock", "name": "A股量化"}
    task = {
        "id": "a-stock-T001",
        "title": "实现数据采集器",
        "estimated_hours": 2.0,
        "depends_on": [],
        "status": "pending",
        "started_at": None,
        "completed_at": None,
    }
    msg = reminder.format_reminder(
        date_str="2026-08-04",
        slot_start="21:00",
        slot_end="23:00",
        goal=goal,
        task=task,
    )
    assert "（已用" not in msg
