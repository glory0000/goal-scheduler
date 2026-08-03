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
