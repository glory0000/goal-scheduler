import os
import sqlite3
import sys
import tempfile
from pathlib import Path

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

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import db

from dashboard.app import create_app, validate_database


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "todos.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
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
