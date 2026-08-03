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
    update_goal_status, update_goal_counts, now_iso
)


@pytest.fixture(autouse=True)
def reset_db():
    """Clear goals table before each test."""
    conn = db.get_conn()
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
