import os
import sys
import tempfile
import pytest

TEST_DB_DIR = tempfile.mkdtemp()
os.environ["TODO_DB_PATH"] = os.path.join(TEST_DB_DIR, "test.db")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import sqlite3
with sqlite3.connect(os.environ["TODO_DB_PATH"]) as conn:
    with open(os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")) as f:
        conn.executescript(f.read())

import db
import scheduler


@pytest.fixture(autouse=True)
def reset_db():
    # Clean tables before each test
    with db.get_conn() as conn:
        conn.execute("DELETE FROM tasks")
        conn.execute("DELETE FROM goals")
        conn.execute("DELETE FROM settings")


def test_load_schedule():
    sched = scheduler.load_schedule()
    assert "weekday" in sched
    assert "weekend" in sched
    assert len(sched["weekday"]) == 4
    assert len(sched["weekend"]) == 3


def test_is_weekend():
    # 2026-08-01 is Saturday, 2026-08-03 is Monday
    assert scheduler.is_weekend("2026-08-01") is True
    assert scheduler.is_weekend("2026-08-02") is True
    assert scheduler.is_weekend("2026-08-03") is False


def test_get_slots_for_weekday():
    slots = scheduler.get_slots_for_date("2026-08-03")  # Monday
    assert len(slots) == 4
    assert slots[0]["start"] == "07:30"


def test_get_slots_for_weekend():
    slots = scheduler.get_slots_for_date("2026-08-01")  # Saturday
    assert len(slots) == 3
    assert slots[0]["start"] == "09:30"


def test_get_next_slot_after_morning():
    # Monday 08:00 → next slot is 12:00 same day
    nxt = scheduler.get_next_slot_after("2026-08-03", "08:00")
    assert nxt == ("2026-08-03", "12:00")


def test_get_next_slot_after_last_weekday_slot():
    # Monday 22:30 → no slots today, wrap to next day
    nxt = scheduler.get_next_slot_after("2026-08-03", "22:30")
    # Should be 2026-08-04 (Tuesday) 07:30
    assert nxt == ("2026-08-04", "07:30")


def test_compute_schedule_focus_first():
    db.create_goal("g-a", "GA", "")
    db.create_goal("g-b", "GB", "")
    db.create_task("g-a-T001", "g-a", 1, "A1", "", 1.0, [])
    db.create_task("g-a-T002", "g-a", 2, "A2", "", 1.0, [])
    db.create_task("g-b-T001", "g-b", 1, "B1", "", 1.0, [])

    # Monday morning, focus on g-a
    plan = scheduler.compute_schedule(
        today_focus="g-a", from_date="2026-08-03", from_time="07:30"
    )
    # 4 weekday slots remaining; first two should be g-a tasks
    assert plan[0]["task_id"] == "g-a-T001"
    assert plan[1]["task_id"] == "g-a-T002"
    # Then overflow to g-b
    assert plan[2]["goal_slug"] == "g-b"


def test_compute_schedule_respects_deps():
    db.create_goal("g-d", "GD", "")
    db.create_task("g-d-T001", "g-d", 1, "A", "", 1.0, [])
    db.create_task("g-d-T002", "g-d", 2, "B", "", 1.0, ["g-d-T001"])
    # T001 not done → only T001 (no deps) is eligible
    plan = scheduler.compute_schedule(
        today_focus="g-d", from_date="2026-08-03", from_time="07:30"
    )
    eligible_ids = [p["task_id"] for p in plan]
    assert "g-d-T001" in eligible_ids
    assert "g-d-T002" not in eligible_ids  # blocked by T001


def test_compute_schedule_skips_paused_goals():
    db.create_goal("g-p", "GP", "")
    db.create_goal("g-q", "GQ", "")
    db.create_task("g-p-T001", "g-p", 1, "P1", "", 1.0, [])
    db.create_task("g-q-T001", "g-q", 1, "Q1", "", 1.0, [])
    db.update_goal_status("g-p", "paused")
    plan = scheduler.compute_schedule(
        today_focus="g-p", from_date="2026-08-03", from_time="07:30"
    )
    # g-p is paused/focus but ineligible; should fallback
    assert all(p["goal_slug"] == "g-q" for p in plan)


def test_compute_schedule_no_tasks_returns_empty():
    db.create_goal("g-e", "GE", "")
    plan = scheduler.compute_schedule(
        today_focus="g-e", from_date="2026-08-03", from_time="07:30"
    )
    assert plan == []


def test_compute_schedule_warns_on_slot_overflow():
    import warnings as _warnings
    db.create_goal("g-w", "GW", "")
    # 2.0h task vs 1.0h lunch slot — guaranteed to overflow on all weekday slots
    db.create_task("g-w-T001", "g-w", 1, "BigTask", "", 2.0, [])
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        plan = scheduler.compute_schedule(
            today_focus="g-w", from_date="2026-08-03", from_time="07:30"
        )
    # No eligible task fits → plan empty
    assert plan == []
    # At least one warning mentioning the task id, title, and estimated hours
    msgs = [str(w.message) for w in caught]
    assert any("g-w-T001" in m for m in msgs)
    assert any("BigTask" in m for m in msgs)
    assert any("2" in m for m in msgs)


# -------------------- TestArchivedExclusion --------------------

class TestArchivedExclusion:
    """Task 2: archived tasks must be excluded from `list_eligible_tasks`.

    NOTE: The brief states `list_eligible_tasks` lives in scheduler.py, but
    it actually lives in db.py. The line numbers in the brief (142-156) match
    db.py's function, so this test calls db.list_eligible_tasks() and the
    production change happens in db.py.
    """

    def _setup_db(self, tmp_path, monkeypatch):
        """Seed an isolated DB. db._init_schema() does not exist; use the
        schema.sql + ALTER TABLE pattern (see tests/test_db.py:15-18).
        """
        db_path = tmp_path / "todos.db"
        with sqlite3.connect(str(db_path)) as conn:
            with open(
                os.path.join(os.path.dirname(__file__), "..", "data", "schema.sql")
            ) as f:
                conn.executescript(f.read())
            conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
        monkeypatch.setattr(db, "DB_PATH", str(db_path))

    def test_list_eligible_tasks_excludes_archived(self, tmp_path, monkeypatch):
        self._setup_db(tmp_path, monkeypatch)
        db.create_goal("g", "G", "")
        # Create with default 'pending' status, then transition to in_progress
        # / done / archived via update_task_status. create_task does NOT
        # accept a status arg (see Task 1's known setup).
        db.create_task("g-T001", "g", 1, "pending task", "", 1.0, [])
        db.create_task("g-T002", "g", 2, "in_progress task", "", 1.0, [])
        db.create_task("g-T003", "g", 3, "done task", "", 1.0, [])
        db.create_task("g-T004", "g", 4, "archived task", "", 1.0, [])
        db.update_task_status("g-T002", "in_progress")
        db.update_task_status("g-T003", "done")
        db.update_task_status("g-T004", "archived")

        eligible = db.list_eligible_tasks()
        eligible_ids = {t["id"] for t in eligible}
        assert "g-T001" in eligible_ids      # pending: eligible
        assert "g-T003" not in eligible_ids  # done: not eligible
        assert "g-T004" not in eligible_ids  # archived: NOT eligible (NEW)
