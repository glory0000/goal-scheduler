import os
import sys
import tempfile
import pytest
import sqlite3

# Use a temp DB for tests
TEST_DB_DIR = tempfile.mkdtemp()
TEST_DB_PATH = os.path.join(TEST_DB_DIR, "test.db")
os.environ["TODO_DB_PATH"] = TEST_DB_PATH

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

# Apply schema to test DB
with sqlite3.connect(TEST_DB_PATH) as conn:
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")) as f:
        conn.executescript(f.read())
    conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")

import db

from db import (
    create_goal, get_goal, list_goals,
    update_goal_status, update_goal_counts, now_iso,
    create_task, get_task, list_tasks, list_eligible_tasks,
    update_task_status, mark_task_reminded, count_tasks_by_status,
    get_setting, set_setting,
    get_today_focus, set_today_focus,
    recompute_goal_counts, write_goal_md_progress,
)


@pytest.fixture(autouse=True)
def reset_db():
    """Clear goals and tasks tables before each test."""
    conn = db.get_conn()
    conn.execute("DELETE FROM tasks")
    conn.execute("DELETE FROM goals")
    conn.commit()
    conn.close()


def test_create_and_get_goal():
    create_goal(
        slug="test-goal",
        name="Test Goal",
        description="Just testing"
    )
    g = get_goal("test-goal")
    assert g is not None
    assert g["slug"] == "test-goal"
    assert g["name"] == "Test Goal"
    assert g["status"] == "active"
    assert g["total_tasks"] == 0
    assert g["completed_tasks"] == 0


def test_list_goals():
    create_goal("g1", "Goal 1", "")
    create_goal("g2", "Goal 2", "")
    update_goal_status("g2", "paused")
    active = list_goals(status="active")
    paused = list_goals(status="paused")
    assert len(active) == 1
    assert active[0]["slug"] == "g1"
    assert len(paused) == 1
    assert paused[0]["slug"] == "g2"


def test_update_goal_status():
    create_goal("g3", "G3", "")
    update_goal_status("g3", "completed")
    g = get_goal("g3")
    assert g["status"] == "completed"


def test_update_goal_counts():
    create_goal("g4", "G4", "")
    update_goal_counts("g4", total=5, completed=2)
    g = get_goal("g4")
    assert g["total_tasks"] == 5
    assert g["completed_tasks"] == 2


def test_now_iso_format():
    ts = now_iso()
    # Should match YYYY-MM-DDTHH:MM:SS
    assert len(ts) == 19
    assert ts[4] == "-"
    assert ts[10] == "T"


def test_create_and_get_task():
    create_goal("g-tasks", "G Tasks", "")
    create_task(
        id="g-tasks-T001",
        goal_slug="g-tasks",
        sequence=1,
        title="Task 1",
        description="",
        estimated_hours=1.5,
        depends_on=[],
    )
    t = get_task("g-tasks-T001")
    assert t is not None
    assert t["title"] == "Task 1"
    assert t["estimated_hours"] == 1.5
    assert t["status"] == "pending"
    assert t["depends_on"] == []  # parsed back from JSON


def test_list_tasks_by_goal():
    create_goal("g-list", "G List", "")
    create_task("g-list-T001", "g-list", 1, "A", "", 1.0, [])
    create_task("g-list-T002", "g-list", 2, "B", "", 1.0, [])
    tasks = list_tasks(goal_slug="g-list")
    assert len(tasks) == 2
    assert tasks[0]["sequence"] == 1


def test_list_eligible_tasks():
    create_goal("g-elig", "G Elig", "")
    create_task("g-elig-T001", "g-elig", 1, "A", "", 1.0, [])
    create_task("g-elig-T002", "g-elig", 2, "B", "", 1.0, ["g-elig-T001"])
    update_task_status("g-elig-T001", "done")
    eligible = list_eligible_tasks(goal_slug="g-elig")
    assert len(eligible) == 1
    assert eligible[0]["id"] == "g-elig-T002"


def test_update_task_status():
    create_goal("g-st", "G ST", "")
    create_task("g-st-T001", "g-st", 1, "X", "", 1.0, [])
    update_task_status("g-st-T001", "done")
    t = get_task("g-st-T001")
    assert t["status"] == "done"
    assert t["completed_at"] is not None


def test_mark_task_reminded():
    create_goal("g-rem", "G Rem", "")
    create_task("g-rem-T001", "g-rem", 1, "X", "", 1.0, [])
    mark_task_reminded("g-rem-T001")
    t = get_task("g-rem-T001")
    assert t["last_reminded_at"] is not None


def test_count_tasks_by_status():
    create_goal("g-cnt", "G Cnt", "")
    create_task("g-cnt-T001", "g-cnt", 1, "A", "", 1.0, [])
    create_task("g-cnt-T002", "g-cnt", 2, "B", "", 1.0, [])
    create_task("g-cnt-T003", "g-cnt", 3, "C", "", 1.0, [])
    update_task_status("g-cnt-T002", "done")
    counts = count_tasks_by_status("g-cnt")
    assert counts["pending"] == 2
    assert counts["done"] == 1


def test_settings_roundtrip():
    set_setting("foo", "bar")
    assert get_setting("foo") == "bar"


def test_today_focus():
    create_goal("focus-1", "F1", "")
    set_today_focus("focus-1")
    assert get_today_focus() == "focus-1"
    set_today_focus(None)
    assert get_today_focus() is None


def test_recompute_goal_counts():
    create_goal("rc", "RC", "")
    create_task("rc-T001", "rc", 1, "A", "", 1.0, [])
    create_task("rc-T002", "rc", 2, "B", "", 1.0, [])
    update_task_status("rc-T001", "done")
    recompute_goal_counts("rc")
    g = get_goal("rc")
    assert g["total_tasks"] == 2
    assert g["completed_tasks"] == 1


def test_write_goal_md_progress(tmp_path, monkeypatch):
    # Redirect cwd
    monkeypatch.chdir(tmp_path)
    (tmp_path / "goals" / "rc2").mkdir(parents=True)
    md = tmp_path / "goals" / "rc2" / "goal.md"
    md.write_text(
        "# 目标：RC2\n\n## 任务进度\n- 总任务数：0\n- 已完成：0\n\n## 备注\n", encoding="utf-8"
    )
    create_goal("rc2", "RC2", "")
    create_task("rc2-T001", "rc2", 1, "A", "", 1.0, [])
    create_task("rc2-T002", "rc2", 2, "B", "", 1.0, [])
    update_task_status("rc2-T002", "done")
    write_goal_md_progress("rc2")
    text = md.read_text(encoding="utf-8")
    assert "总任务数：2" in text
    assert "已完成：1" in text
    assert "完成率：50%" in text


def test_update_task_status_stamps_started_at_on_first_in_progress():
    create_goal("g-s1", "GS1", "")
    create_task("g-s1-T001", "g-s1", 1, "X", "", 1.0, [])
    update_task_status("g-s1-T001", "in_progress")
    t = get_task("g-s1-T001")
    assert t["started_at"] is not None
    # Stamped on the same call as updated_at
    assert t["started_at"] == t["updated_at"]


def test_update_task_status_done_preserves_started_at():
    create_goal("g-s2", "GS2", "")
    create_task("g-s2-T001", "g-s2", 1, "X", "", 1.0, [])
    update_task_status("g-s2-T001", "in_progress")
    started = get_task("g-s2-T001")["started_at"]
    update_task_status("g-s2-T001", "done")
    t = get_task("g-s2-T001")
    assert t["started_at"] == started  # preserved through done
    assert t["completed_at"] is not None


def test_update_task_status_in_progress_idempotent_via_coalesce():
    create_goal("g-s3", "GS3", "")
    create_task("g-s3-T001", "g-s3", 1, "X", "", 1.0, [])
    update_task_status("g-s3-T001", "in_progress")
    started = get_task("g-s3-T001")["started_at"]
    update_task_status("g-s3-T001", "pending")
    update_task_status("g-s3-T001", "in_progress")
    t = get_task("g-s3-T001")
    assert t["started_at"] == started  # unchanged (COALESCE)


def test_update_task_status_done_to_in_progress_preserves_started_at():
    create_goal("g-s4", "GS4", "")
    create_task("g-s4-T001", "g-s4", 1, "X", "", 1.0, [])
    update_task_status("g-s4-T001", "in_progress")
    update_task_status("g-s4-T001", "done")
    started = get_task("g-s4-T001")["started_at"]
    update_task_status("g-s4-T001", "in_progress")
    t = get_task("g-s4-T001")
    assert t["started_at"] == started
    assert t["status"] == "in_progress"
