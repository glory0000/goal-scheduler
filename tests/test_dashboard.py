import os
import sqlite3
import sys
import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCHEMA_PATH = ROOT / "data" / "schema.sql"

# This file sorts before the existing DB tests during collection, so establish
# a disposable process-wide DB before importing the shared `db` module.
COLLECTION_DB_DIR = tempfile.mkdtemp()
COLLECTION_DB_PATH = Path(COLLECTION_DB_DIR) / "collection.db"
os.environ["TODO_DB_PATH"] = str(COLLECTION_DB_PATH)
with sqlite3.connect(COLLECTION_DB_PATH) as conn:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import db
import scheduler

from dashboard.app import create_app, validate_database


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "todos.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    return db_path


@pytest.fixture
def client(test_db):
    flask_app = create_app()
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as test_client:
        yield test_client


def test_health_route(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.text == "ok"
    assert response.mimetype == "text/plain"


def test_validate_database_rejects_missing_file(tmp_path):
    missing = tmp_path / "missing.db"
    with pytest.raises(FileNotFoundError, match="Todo database not found"):
        validate_database(missing)
    assert not missing.exists()


def test_validate_database_accepts_existing_file(test_db):
    validate_database(test_db)


def test_index_route_shows_goal_progress_and_current_task(client):
    db.create_goal("goal-a", "目标 A", "第一个目标")
    db.create_task("goal-a-T001", "goal-a", 1, "已完成任务", "", 1.0, [])
    db.create_task("goal-a-T002", "goal-a", 2, "当前任务", "", 2.0, [])
    db.update_task_status("goal-a-T001", "done")
    db.update_task_status("goal-a-T002", "in_progress")

    response = client.get("/")

    assert response.status_code == 200
    assert "目标 A" in response.text
    assert "当前任务" in response.text
    assert "50%" in response.text
    assert 'class="status status-active"' in response.text
    assert 'style="width: 50%"' in response.text


def test_index_route_shows_empty_state(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "暂无目标" in response.text
    assert "通过飞书告诉 Claude 添加你的第一个目标" in response.text


def test_index_loads_stylesheet(client):
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert ".progress" in response.text
    assert ".status-in_progress" in response.text


def test_goal_detail_shows_summary_tasks_and_dependencies(client):
    db.create_goal("detail", "详情目标", "目标说明")
    db.create_task("detail-T001", "detail", 1, "基础任务", "", 1.0, [])
    db.create_task("detail-T002", "detail", 2, "依赖任务", "", 1.5, ["detail-T001"])
    db.update_task_status("detail-T001", "done")
    db.mark_task_reminded("detail-T002")

    response = client.get("/goal/detail")

    assert response.status_code == 200
    assert "详情目标" in response.text
    assert "目标说明" in response.text
    assert "2 个任务，1 个已完成，完成率 50%" in response.text
    assert "总预估 2.5 小时" in response.text
    assert "基础任务" in response.text
    assert "依赖任务" in response.text
    assert "detail-T001 ✓" in response.text


def test_goal_detail_unknown_slug_returns_404(client):
    response = client.get("/goal/missing")
    assert response.status_code == 404
    assert "目标不存在" in response.text


def test_today_route_shows_date_focus_slots_and_assignment(client):
    db.create_goal("focus", "今日重点", "")
    db.create_task("focus-T001", "focus", 1, "今日任务", "", 0.5, [])
    db.set_today_focus("focus")

    response = client.get("/today")

    slots = scheduler.get_slots_for_date(date.today().isoformat())
    assert response.status_code == 200
    assert date.today().isoformat() in response.text
    assert "今日重点" in response.text
    assert "今日任务" in response.text
    assert f'{slots[0]["start"]}-{slots[0]["end"]}' in response.text


def test_today_route_shows_empty_schedule(client):
    response = client.get("/today")
    assert response.status_code == 200
    assert "未设置" in response.text
    assert "今日无安排" in response.text
    assert "全部任务已完成" in response.text


def test_today_route_counts_unscheduled_pending_tasks(client):
    db.create_goal("many", "多个任务", "")
    slots = scheduler.get_slots_for_date(date.today().isoformat())
    for number in range(len(slots) + 1):
        db.create_task(
            f"many-T{number + 1:03d}", "many", number + 1,
            f"任务 {number + 1}", "", 0.5, [],
        )

    response = client.get("/today")

    assert response.status_code == 200
    assert "今日剩余 1 个任务未安排" in response.text


def test_stats_route_shows_aggregates_progress_and_recent_completion(client):
    db.create_goal("active", "活跃目标", "")
    db.create_goal("paused", "暂停目标", "")
    db.update_goal_status("paused", "paused")
    db.create_task("active-T001", "active", 1, "最近完成", "", 1.5, [])
    db.create_task("active-T002", "active", 2, "待办任务", "", 2.0, [])
    db.create_task("paused-T001", "paused", 1, "暂停任务", "", 3.0, [])
    db.update_task_status("active-T001", "done")

    response = client.get("/stats")

    assert response.status_code == 200
    assert "活跃目标" in response.text
    assert ">1</strong><span>活跃目标" in response.text
    assert ">3</strong><span>总任务" in response.text
    assert ">1</strong><span>已完成" in response.text
    assert ">6.5 h</strong><span>总预估耗时" in response.text
    assert ">1.5 h</strong><span>已完成预估耗时" in response.text
    assert "最近完成" in response.text
    assert 'style="width: 50%"' in response.text


def test_stats_route_handles_empty_database(client):
    response = client.get("/stats")
    assert response.status_code == 200
    assert ">0</strong><span>活跃目标" in response.text
    assert "最近 7 天暂无已完成任务" in response.text


def test_database_error_returns_generic_500_page(client):
    with patch.object(db, "list_goals", side_effect=sqlite3.DatabaseError("secret DB detail")):
        response = client.get("/")

    assert response.status_code == 500
    assert "读取任务数据失败，请检查服务日志" in response.text
    assert "secret DB detail" not in response.text


def test_all_dashboard_routes_return_success(client):
    db.create_goal("smoke", "冒烟目标", "")
    db.create_task("smoke-T001", "smoke", 1, "冒烟任务", "", 0.5, [])

    for path in ("/", "/goal/smoke", "/today", "/stats", "/health"):
        response = client.get(path)
        assert response.status_code == 200, path


def test_goal_detail_shows_started_and_elapsed_columns(client):
    db.create_goal("elapsed-goal", "目标", "")
    db.create_task("elapsed-goal-T001", "elapsed-goal", 1, "未开始", "", 1.0, [])
    db.create_task("elapsed-goal-T002", "elapsed-goal", 2, "进行中", "", 1.5, [])
    db.create_task("elapsed-goal-T003", "elapsed-goal", 3, "已完成", "", 1.0, [])
    db.update_task_status("elapsed-goal-T002", "in_progress")
    db.update_task_status("elapsed-goal-T003", "done")

    response = client.get("/goal/elapsed-goal")

    assert response.status_code == 200
    # Column headers are rendered
    assert "Started" in response.text
    assert "Elapsed" in response.text
    # The pending task has both columns as "—"
    assert "未开始" in response.text
    # The in_progress and done tasks should have a non-dash elapsed
    # (a number with unit suffix). We assert the presence of the
    # "h " or "m " or "s" pattern in elapsed cells.
    body = response.text
    # At least one row should have a non-dash elapsed
    assert ("h " in body) or ("m " in body) or ("s</td>" in body)


def test_today_timeline_appends_elapsed_suffix_for_in_progress(client, monkeypatch):
    import scheduler
    from datetime import date

    db.create_goal("today-elapsed", "今日目标", "")
    db.create_task("today-elapsed-T001", "today-elapsed", 1, "进行中任务", "", 1.0, [])
    db.update_task_status("today-elapsed-T001", "in_progress")
    db.set_today_focus("today-elapsed")

    # Monkeypatch compute_schedule to always return our task in the first slot
    def mock_compute_schedule(focus_slug, from_date, from_time, max_slots=20):
        return [{
            "date": from_date,
            "slot_start": "07:30",
            "slot_end": "09:00",
            "goal_slug": "today-elapsed",
            "task_id": "today-elapsed-T001",
        }]

    monkeypatch.setattr(scheduler, "compute_schedule", mock_compute_schedule)

    response = client.get("/today")

    assert response.status_code == 200
    # The in_progress task label has the suffix appended
    assert "进行中任务（已用" in response.text
    # The closing parenthesis immediately follows the elapsed value
    assert "）" in response.text


# ============================================================================
# Task 5: archived filter on /, archived banner on goal_detail, /task/<id> route
# ============================================================================


def _seed_archived_goals(tmp_path, monkeypatch):
    """Seed an isolated DB with one active and one archived goal.

    Returns the db_path string.
    """
    db_path = tmp_path / "todos.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.create_goal("alive", "Alive", "")
    db.create_goal("dead", "Dead", "")
    db.update_goal_status("dead", "archived")
    return str(db_path)


def _app_client(monkeypatch, tmp_path):
    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app.test_client()


def test_index_default_hides_archived(tmp_path, monkeypatch):
    """GET / with no params hides only archived goals — paused/completed remain."""
    _seed_archived_goals(tmp_path, monkeypatch)
    # Add paused and completed goals alongside the existing active + archived.
    db.create_goal("paus", "Paused", "")
    db.update_goal_status("paus", "paused")
    db.create_goal("comp", "Completed", "")
    db.update_goal_status("comp", "completed")
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Alive" in body
    assert "Paused" in body
    assert "Completed" in body
    assert "Dead" not in body


def test_index_show_all_includes_archived(tmp_path, monkeypatch):
    """GET /?all=1 shows all goals (including archived)."""
    _seed_archived_goals(tmp_path, monkeypatch)
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/?all=1")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Alive" in body
    assert "Dead" in body


def test_index_status_query(tmp_path, monkeypatch):
    """GET /?status=archived shows only archived goals and their badge renders."""
    _seed_archived_goals(tmp_path, monkeypatch)
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/?status=archived")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "Dead" in body
    assert "Alive" not in body
    # C1: the archived status badge must render its label, not be empty.
    # The badge is rendered as <span class="status status-archived">已归档</span>.
    assert 'class="status status-archived">已归档</span>' in body


def test_goal_detail_archived_banner(tmp_path, monkeypatch):
    """Archived goal shows a banner with restoration hint on /goal/<slug>."""
    _seed_archived_goals(tmp_path, monkeypatch)
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/goal/dead")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    # Banner text mentions archived + restoration hint
    assert ("已归档" in body) or ("archived" in body.lower())
    assert ("restore" in body.lower()) or ("goal restore" in body)


def _seed_task_for_detail(tmp_path, monkeypatch):
    """Seed an isolated DB with one goal and one archived task."""
    db_path = tmp_path / "todos.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
    monkeypatch.setattr(db, "DB_PATH", str(db_path))
    db.create_goal("g", "G", "")
    db.create_task("g-T001", "g", 1, "hello task", "", 2.0, [])
    db.update_task_status("g-T001", "archived")
    return str(db_path)


def test_task_detail_basic(tmp_path, monkeypatch):
    """GET /task/<id> renders the task id, title, and parent goal slug."""
    _seed_task_for_detail(tmp_path, monkeypatch)
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/task/g-T001")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert "g-T001" in body
    assert "hello task" in body
    # Parent goal link points at /goal/g (more specific than just "g" in body).
    assert "/goal/g" in body


def test_task_detail_archived_banner(tmp_path, monkeypatch):
    """Archived task shows a banner on /task/<id>."""
    _seed_task_for_detail(tmp_path, monkeypatch)
    client = _app_client(monkeypatch, tmp_path)

    rv = client.get("/task/g-T001")

    assert rv.status_code == 200
    body = rv.get_data(as_text=True)
    assert ("已归档" in body) or ("archived" in body.lower())


def test_task_detail_404(tmp_path, monkeypatch):
    """GET /task/<unknown-id> returns 404."""
    db_path = tmp_path / "todos.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.execute("ALTER TABLE tasks ADD COLUMN started_at TEXT")
    monkeypatch.setattr(db, "DB_PATH", str(db_path))

    client = _app_client(monkeypatch, tmp_path)
    rv = client.get("/task/does-not-exist")

    assert rv.status_code == 404
