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

import db

from db import (
    create_goal, get_goal, list_goals,
    update_goal_status, update_goal_counts, now_iso,
    create_task, get_task, list_tasks, list_eligible_tasks,
    update_task_status, mark_task_reminded, count_tasks_by_status,
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
