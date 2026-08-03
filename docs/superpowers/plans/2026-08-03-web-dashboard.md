# Web Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a read-only Flask dashboard that displays goal progress, goal tasks, today's configured schedule, and aggregate task statistics from the existing todo scheduler.

**Architecture:** `dashboard/app.py` is the Flask application factory, route layer, and view-model builder. It reuses `scripts/db.py` and `scripts/scheduler.py`; Jinja templates render server-side HTML, and one CSS file provides layout, status pills, and progress bars. Every request reads current SQLite/config data with no caching or writes.

**Tech Stack:** Python 3.10+, Flask 3.x, Jinja2, SQLite, HTML5, CSS3, pytest, Flask `test_client()`.

## Global Constraints

- The dashboard is read-only; it must not modify `data/todos.db`, goal Markdown, timers, or schedule configuration.
- All dashboard writes remain out of scope and continue through Claude via Feishu.
- Provide exactly four user views: `/`, `/goal/<slug>`, `/today`, and `/stats`, plus `/health` for health checks.
- Use Flask server-side rendering with HTML and minimal JavaScript; v1 requires no client-side JavaScript.
- Refresh is manual with F5; do not add polling, WebSockets, or server-side caching.
- Use CSS progress bars and status colors; do not add Chart.js or another visualization dependency.
- Bind the personal-use development server to `0.0.0.0:5000` for trusted-LAN access, with no authentication or HTTPS.
- Use `data/todos.db` for goals, tasks, focus, and statistics, and `config/schedule.json` for slot definitions.
- The DB path remains overridable through the existing `TODO_DB_PATH` environment variable.
- Missing DB at process startup must print an error and exit non-zero before SQLite can create an empty file.
- Runtime SQLite failures return a generic Chinese 500 page and are logged server-side.
- Unknown `/goal/<slug>` requests return status 404 with `目标不存在`.
- Tests must use a temporary SQLite DB and never touch `data/todos.db`.
- Keep Flask as the dashboard's only direct runtime dependency.

## File Structure

- Create `dashboard/app.py` — application factory, startup validation, shared formatting helpers, route handlers, and view-model assembly.
- Create `dashboard/requirements.txt` — Flask runtime dependency.
- Create `dashboard/templates/base.html` — shared page shell and navigation.
- Create `dashboard/templates/index.html` — goal list and empty state.
- Create `dashboard/templates/goal_detail.html` — one goal's summary and task table.
- Create `dashboard/templates/today.html` — configured-slot timeline and focus/remaining-task summary.
- Create `dashboard/templates/stats.html` — aggregate cards, active-goal progress, and recent completions.
- Create `dashboard/templates/error.html` — generic runtime DB error page.
- Create `dashboard/static/style.css` — desktop-first layout, cards, tables, progress bars, status pills, and timeline styling.
- Create `dashboard/README.md` — installation, startup, access, refresh, and trusted-LAN limitations.
- Create `tests/test_dashboard.py` — isolated Flask route, rendering, startup, and error tests.
- Do not modify `scripts/db.py`, `scripts/scheduler.py`, `data/schema.sql`, or `config/schedule.json`.

---

### Task 1: Flask application foundation and health check

**Files:**
- Create: `dashboard/app.py`
- Create: `dashboard/requirements.txt`
- Create: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `scripts.db.DB_PATH` and the existing `TODO_DB_PATH` override.
- Produces: `create_app() -> flask.Flask`, `validate_database(path: str | os.PathLike | None = None) -> None`, `main() -> int`, module-level `app`, and GET `/health` returning plain text `ok`.

- [ ] **Step 1: Write failing foundation tests**

Create `tests/test_dashboard.py`:

```python
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
```

- [ ] **Step 2: Run the foundation tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -v
```

Expected: collection fails with `ModuleNotFoundError: No module named 'dashboard.app'`.

- [ ] **Step 3: Add the Flask dependency declaration**

Create `dashboard/requirements.txt`:

```text
Flask>=3.1,<4
```

Install through a domestic mirror if Flask is not already available:

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r dashboard/requirements.txt
```

Expected: Flask 3.x and its transitive dependencies are installed successfully.

- [ ] **Step 4: Implement the application foundation**

Create `dashboard/app.py`:

```python
#!/usr/bin/env python3
"""Read-only web dashboard for the todo scheduler."""

import os
import sys
from pathlib import Path

from flask import Flask, Response

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import db


def create_app() -> Flask:
    flask_app = Flask(__name__)

    @flask_app.get("/health")
    def health() -> Response:
        return Response("ok", mimetype="text/plain")

    return flask_app


def validate_database(path: str | os.PathLike | None = None) -> None:
    db_path = Path(path if path is not None else db.DB_PATH)
    if not db_path.is_file():
        raise FileNotFoundError(f"Todo database not found: {db_path}")


def main() -> int:
    try:
        validate_database()
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    try:
        app.run(host="0.0.0.0", port=5000, debug=False)
    except OSError as exc:
        print(f"Error: unable to start dashboard on 0.0.0.0:5000: {exc}", file=sys.stderr)
        return 1
    return 0


app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 5: Run the foundation tests and full existing suite**

Run:

```bash
python -m pytest tests/test_dashboard.py -v
python -m pytest -q
```

Expected: dashboard foundation tests pass; the full suite reports 31 passing tests (28 existing + 3 new).

- [ ] **Step 6: Commit the foundation**

```bash
git add dashboard/app.py dashboard/requirements.txt tests/test_dashboard.py
git commit -m "Add Flask dashboard foundation"
```

---

### Task 2: Shared layout and goal list view

**Files:**
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`
- Create: `dashboard/templates/base.html`
- Create: `dashboard/templates/index.html`
- Create: `dashboard/static/style.css`

**Interfaces:**
- Consumes: `db.list_goals() -> list[dict]` and `db.list_tasks(goal_slug=slug) -> list[dict]`.
- Produces: `_progress(total: int, completed: int) -> int`, `_goal_row(goal: dict) -> dict`, shared Jinja global `status_label`, GET `/`, `base.html`, and initial `style.css`.

- [ ] **Step 1: Add failing goal-list route tests**

Append to `tests/test_dashboard.py`:

```python
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
```

- [ ] **Step 2: Run the new tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "index" -v
```

Expected: tests fail because `/` returns 404 and `style.css` does not exist.

- [ ] **Step 3: Add goal-list helpers and route**

In `dashboard/app.py`, change the Flask import to:

```python
from flask import Flask, Response, render_template
```

After `import db`, add:

```python
STATUS_LABELS = {
    "active": "进行中",
    "paused": "已暂停",
    "completed": "已完成",
    "pending": "待办",
    "in_progress": "进行中",
    "done": "已完成",
    "skipped": "已跳过",
}


def _progress(total: int, completed: int) -> int:
    return int(round(completed * 100 / total)) if total else 0


def _goal_row(goal: dict) -> dict:
    tasks = db.list_tasks(goal_slug=goal["slug"])
    completed = sum(task["status"] == "done" for task in tasks)
    current = next(
        (task for task in tasks if task["status"] == "in_progress"),
        None,
    )
    return {
        "goal": goal,
        "total": len(tasks),
        "completed": completed,
        "progress": _progress(len(tasks), completed),
        "current": current,
    }
```

Inside `create_app()`, immediately after `flask_app = Flask(__name__)`, add:

```python
    flask_app.jinja_env.globals["status_label"] = STATUS_LABELS

    @flask_app.get("/")
    def index():
        rows = [_goal_row(goal) for goal in db.list_goals()]
        return render_template("index.html", rows=rows)
```

- [ ] **Step 4: Create the shared page shell**

Create `dashboard/templates/base.html`:

```html
<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}任务看板{% endblock %}</title>
  <link rel="stylesheet" href="{{ url_for('static', filename='style.css') }}">
</head>
<body>
  <nav class="top-nav">
    <div class="nav-links">
      <a href="{{ url_for('index') }}">目标</a>
      <a href="/today">今日</a>
      <a href="/stats">统计</a>
    </div>
    <span class="hint">手动按 F5 刷新</span>
  </nav>
  <main class="container">
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 5: Create the goal list template**

Create `dashboard/templates/index.html`:

```html
{% extends "base.html" %}
{% block title %}目标 - 任务看板{% endblock %}
{% block content %}
  <h1>目标</h1>
  {% if rows %}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>名称</th><th>状态</th><th>总数</th><th>完成</th><th>进度</th><th>当前任务</th>
          </tr>
        </thead>
        <tbody>
          {% for row in rows %}
            <tr>
              <td><a href="/goal/{{ row.goal.slug }}">{{ row.goal.name }}</a></td>
              <td><span class="status status-{{ row.goal.status }}">{{ status_label[row.goal.status] }}</span></td>
              <td>{{ row.total }}</td>
              <td>{{ row.completed }}</td>
              <td class="progress-cell">
                <div class="progress"><div class="fill" style="width: {{ row.progress }}%"></div></div>
                <span>{{ row.progress }}%</span>
              </td>
              <td>{{ row.current.title if row.current else "—" }}</td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <section class="empty-state">
      <h2>暂无目标</h2>
      <p>通过飞书告诉 Claude 添加你的第一个目标</p>
    </section>
  {% endif %}
{% endblock %}
```

- [ ] **Step 6: Create the initial stylesheet**

Create `dashboard/static/style.css`:

```css
:root {
  color-scheme: light;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  color: #263238;
  background: #f4f6f8;
}
* { box-sizing: border-box; }
body { margin: 0; }
a { color: #1565c0; text-decoration: none; }
a:hover { text-decoration: underline; }
.top-nav {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 14px 24px;
  background: #263238;
  color: #fff;
}
.nav-links { display: flex; gap: 20px; }
.top-nav a { color: #fff; font-weight: 600; }
.hint { color: #cfd8dc; font-size: 0.9rem; }
.container { max-width: 1180px; margin: 0 auto; padding: 24px; }
.table-wrap { overflow-x: auto; background: #fff; border-radius: 8px; }
table { width: 100%; border-collapse: collapse; }
th, td { padding: 12px 14px; border-bottom: 1px solid #eceff1; text-align: left; }
th { background: #eceff1; white-space: nowrap; }
.status { display: inline-block; padding: 3px 8px; border-radius: 999px; font-size: 0.85rem; white-space: nowrap; }
.status-done, .status-active { background: #4caf50; color: #fff; }
.status-in_progress { background: #ff9800; color: #fff; }
.status-pending, .status-paused { background: #9e9e9e; color: #fff; }
.status-skipped { background: #f44336; color: #fff; }
.status-completed { background: #2196f3; color: #fff; }
.progress-cell { min-width: 190px; }
.progress { display: inline-block; width: 120px; height: 18px; margin-right: 8px; overflow: hidden; vertical-align: middle; background: #e0e0e0; border-radius: 3px; }
.progress > .fill { height: 100%; background: #4caf50; border-radius: 3px; }
.empty-state { padding: 48px 24px; text-align: center; background: #fff; border-radius: 8px; }
```

- [ ] **Step 7: Run goal-list and regression tests**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "index or health or validate" -v
python -m pytest -q
```

Expected: all selected tests pass; full suite reports 34 passing tests.

- [ ] **Step 8: Commit the goal list view**

```bash
git add dashboard/app.py dashboard/templates/base.html dashboard/templates/index.html dashboard/static/style.css tests/test_dashboard.py
git commit -m "Add dashboard goal list view"
```

---

### Task 3: Goal detail view

**Files:**
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`
- Create: `dashboard/templates/goal_detail.html`

**Interfaces:**
- Consumes: `_progress`, `db.get_goal(slug)`, `db.list_tasks(goal_slug=slug)`, and `db.get_task(id)`.
- Produces: `_format_timestamp(value: str | None) -> str`, `_task_row(task: dict) -> dict`, and the complete GET `/goal/<slug>` implementation.

- [ ] **Step 1: Add failing goal-detail tests**

Append to `tests/test_dashboard.py`:

```python
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
```

- [ ] **Step 2: Run goal-detail tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "goal_detail" -v
```

Expected: tests fail because `/goal/detail` and `/goal/missing` return 404 before the route is added.

- [ ] **Step 3: Add task display helpers**

In `dashboard/app.py`, add after `_goal_row`:

```python
def _format_timestamp(value: str | None) -> str:
    return value.replace("T", " ") if value else "—"


def _task_row(task: dict) -> dict:
    dependencies = []
    for dependency_id in task["depends_on"]:
        dependency = db.get_task(dependency_id)
        dependencies.append({
            "id": dependency_id,
            "done": dependency is not None and dependency["status"] == "done",
        })
    return {
        "task": task,
        "dependencies": dependencies,
        "last_reminded": _format_timestamp(task["last_reminded_at"]),
        "completed": _format_timestamp(task["completed_at"]),
    }
```

Add this route inside `create_app()` after the index route:

```python
    @flask_app.get("/goal/<slug>")
    def goal_detail(slug: str):
        goal = db.get_goal(slug)
        if goal is None:
            return render_template("goal_detail.html", goal=None), 404

        tasks = db.list_tasks(goal_slug=slug)
        completed = sum(task["status"] == "done" for task in tasks)
        summary = {
            "total": len(tasks),
            "completed": completed,
            "progress": _progress(len(tasks), completed),
            "estimated_hours": sum(task["estimated_hours"] or 0 for task in tasks),
        }
        return render_template(
            "goal_detail.html",
            goal=goal,
            summary=summary,
            task_rows=[_task_row(task) for task in tasks],
        )
```

- [ ] **Step 4: Create the goal detail template**

Create `dashboard/templates/goal_detail.html`:

```html
{% extends "base.html" %}
{% block title %}{{ goal.name if goal else "目标不存在" }} - 任务看板{% endblock %}
{% block content %}
  {% if not goal %}
    <section class="empty-state"><h1>目标不存在</h1><p><a href="{{ url_for('index') }}">返回目标列表</a></p></section>
  {% else %}
    <header class="page-header">
      <div>
        <h1>{{ goal.name }}</h1>
        <p>{{ goal.description or "暂无描述" }}</p>
      </div>
      <span class="status status-{{ goal.status }}">{{ status_label[goal.status] }}</span>
    </header>
    <p class="meta">创建于 {{ goal.created_at|replace("T", " ") }}</p>
    <section class="summary-line">
      {{ summary.total }} 个任务，{{ summary.completed }} 个已完成，完成率 {{ summary.progress }}%，总预估 {{ "%.1f"|format(summary.estimated_hours) }} 小时
    </section>
    {% if task_rows %}
      <div class="table-wrap">
        <table>
          <thead><tr><th>ID</th><th>标题</th><th>小时</th><th>依赖</th><th>状态</th><th>上次提醒</th><th>完成时间</th></tr></thead>
          <tbody>
            {% for row in task_rows %}
              <tr>
                <td>{{ row.task.id }}</td>
                <td>{{ row.task.title }}</td>
                <td>{{ "%.1f"|format(row.task.estimated_hours or 0) }}</td>
                <td>
                  {% if row.dependencies %}
                    {% for dep in row.dependencies %}→ {{ dep.id }} {{ "✓" if dep.done else "未完成" }}{% if not loop.last %}<br>{% endif %}{% endfor %}
                  {% else %}—{% endif %}
                </td>
                <td><span class="status status-{{ row.task.status }}">{{ status_label[row.task.status] }}</span></td>
                <td>{{ row.last_reminded }}</td>
                <td>{{ row.completed }}</td>
              </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
    {% else %}
      <section class="empty-state"><p>该目标暂无任务</p></section>
    {% endif %}
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Extend styles for detail headers**

Append to `dashboard/static/style.css`:

```css
.page-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; }
.page-header h1 { margin-bottom: 8px; }
.page-header p { margin-top: 0; color: #546e7a; }
.meta { color: #78909c; font-size: 0.9rem; }
.summary-line { margin: 20px 0; padding: 16px; background: #fff; border-left: 4px solid #4caf50; border-radius: 4px; }
```

- [ ] **Step 6: Run goal-detail and regression tests**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "goal_detail" -v
python -m pytest -q
```

Expected: both goal-detail tests pass; full suite reports 36 passing tests.

- [ ] **Step 7: Commit the goal detail view**

```bash
git add dashboard/app.py dashboard/templates/goal_detail.html dashboard/static/style.css tests/test_dashboard.py
git commit -m "Add dashboard goal detail view"
```

---

### Task 4: Today schedule timeline

**Files:**
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`
- Create: `dashboard/templates/today.html`

**Interfaces:**
- Consumes: `scheduler.get_slots_for_date(date_str)`, `scheduler.compute_schedule(today_focus, from_date, from_time, max_slots)`, `db.get_today_focus()`, `db.list_goals(status="active")`, `db.list_tasks`, `db.get_task`, and `db.get_goal`.
- Produces: `_weekday_label(day: int) -> str`, `_today_view(today_date: str) -> dict`, and the complete GET `/today` implementation.

- [ ] **Step 1: Add failing today-route tests**

Add these imports near the top of `tests/test_dashboard.py`:

```python
from datetime import date

import scheduler
```

Append these tests:

```python
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
```

- [ ] **Step 2: Run today-route tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "today_route" -v
```

Expected: tests fail because `/today` returns 404 before the route is added.

- [ ] **Step 3: Add today schedule view-model logic**

In `dashboard/app.py`, add imports:

```python
from datetime import date

import scheduler
```

Add after `_task_row`:

```python
WEEKDAY_LABELS = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")


def _today_view(today_date: str) -> dict:
    slots = scheduler.get_slots_for_date(today_date)
    focus_slug = db.get_today_focus()
    focus_goal = db.get_goal(focus_slug) if focus_slug else None
    plan = scheduler.compute_schedule(
        focus_slug,
        today_date,
        "00:00",
        max_slots=len(slots),
    )
    today_plan = [item for item in plan if item["date"] == today_date]
    assignments = {item["slot_start"]: item for item in today_plan}

    slot_rows = []
    scheduled_ids = set()
    for slot in slots:
        assignment = assignments.get(slot["start"])
        task = db.get_task(assignment["task_id"]) if assignment else None
        goal = db.get_goal(assignment["goal_slug"]) if assignment else None
        dependencies = []
        if task:
            scheduled_ids.add(task["id"])
            for dependency_id in task["depends_on"]:
                dependency = db.get_task(dependency_id)
                dependencies.append({
                    "id": dependency_id,
                    "done": dependency is not None and dependency["status"] == "done",
                })
        slot_rows.append({
            "slot": slot,
            "task": task,
            "goal": goal,
            "dependencies": dependencies,
        })

    active_goals = db.list_goals(status="active")
    pending_tasks = [
        task
        for goal in active_goals
        for task in db.list_tasks(goal_slug=goal["slug"], status="pending")
    ]
    remaining = sum(task["id"] not in scheduled_ids for task in pending_tasks)
    return {
        "date": today_date,
        "weekday": WEEKDAY_LABELS[date.fromisoformat(today_date).weekday()],
        "focus_goal": focus_goal,
        "slot_rows": slot_rows,
        "has_assignments": bool(scheduled_ids),
        "remaining": remaining,
    }
```

Add this route inside `create_app()`:

```python
    @flask_app.get("/today")
    def today():
        return render_template("today.html", view=_today_view(date.today().isoformat()))
```

- [ ] **Step 4: Create the today timeline template**

Create `dashboard/templates/today.html`:

```html
{% extends "base.html" %}
{% block title %}今日 - 任务看板{% endblock %}
{% block content %}
  <header class="page-header">
    <div><h1>{{ view.date }} {{ view.weekday }}</h1></div>
    <div>今日重点：{% if view.focus_goal %}[{{ view.focus_goal.slug }}] {{ view.focus_goal.name }}{% else %}未设置{% endif %}</div>
  </header>

  {% if not view.has_assignments %}<p class="notice">今日无安排</p>{% endif %}
  <section class="timeline">
    {% for row in view.slot_rows %}
      <article class="timeline-row">
        <time>{{ row.slot.start }}-{{ row.slot.end }}</time>
        <div class="timeline-content">
          {% if row.task %}
            <strong>[{{ row.goal.name }}] {{ row.task.id }} - {{ row.task.title }}</strong>
            <a href="{{ url_for('goal_detail', slug=row.goal.slug) }}">详情</a>
            {% if row.dependencies %}
              <div class="dependencies">依赖：{% for dep in row.dependencies %}{{ dep.id }} {{ "✓" if dep.done else "未完成" }}{% if not loop.last %}，{% endif %}{% endfor %}</div>
            {% endif %}
          {% else %}
            <span class="empty-slot">──────────（无任务）</span>
          {% endif %}
        </div>
      </article>
    {% endfor %}
  </section>

  <footer class="schedule-footer">
    {% if view.remaining %}今日剩余 {{ view.remaining }} 个任务未安排{% else %}全部任务已完成{% endif %}
  </footer>
{% endblock %}
```

- [ ] **Step 5: Add timeline styles**

Append to `dashboard/static/style.css`:

```css
.notice { padding: 12px 16px; background: #fff3e0; border-radius: 6px; }
.timeline { margin-top: 20px; background: #fff; border-radius: 8px; }
.timeline-row { display: grid; grid-template-columns: 120px 1fr; gap: 20px; padding: 18px; border-bottom: 1px solid #eceff1; }
.timeline-row:last-child { border-bottom: 0; }
.timeline-row time { font-weight: 700; color: #455a64; }
.timeline-content a { margin-left: 12px; }
.empty-slot { color: #90a4ae; }
.dependencies { margin-top: 8px; color: #607d8b; font-size: 0.9rem; }
.schedule-footer { margin-top: 20px; padding: 16px; text-align: center; font-weight: 600; background: #e8f5e9; border-radius: 6px; }
```

- [ ] **Step 6: Run today-route and regression tests**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "today_route" -v
python -m pytest -q
```

Expected: all three today-route tests pass; full suite reports 39 passing tests.

- [ ] **Step 7: Commit the today schedule view**

```bash
git add dashboard/app.py dashboard/templates/today.html dashboard/static/style.css tests/test_dashboard.py
git commit -m "Add dashboard today schedule view"
```

---

### Task 5: Global statistics view

**Files:**
- Modify: `dashboard/app.py`
- Modify: `tests/test_dashboard.py`
- Create: `dashboard/templates/stats.html`

**Interfaces:**
- Consumes: `_goal_row`, `_format_timestamp`, `db.list_goals()`, and `db.list_tasks()`.
- Produces: `_stats_view() -> dict` and the complete GET `/stats` implementation.

- [ ] **Step 1: Add failing stats-route tests**

Append to `tests/test_dashboard.py`:

```python
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
```

- [ ] **Step 2: Run stats tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "stats_route" -v
```

Expected: tests fail because `/stats` returns 404 before the route is added.

- [ ] **Step 3: Add statistics view-model logic**

In `dashboard/app.py`, replace the datetime import with:

```python
from datetime import date, datetime, timedelta, timezone
```

Add after `_today_view`:

```python
def _stats_view() -> dict:
    goals = db.list_goals()
    tasks = db.list_tasks()
    completed_tasks = [task for task in tasks if task["status"] == "done"]
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)

    recent = []
    for task in completed_tasks:
        if not task["completed_at"]:
            continue
        completed_at = datetime.strptime(task["completed_at"], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        if completed_at >= cutoff:
            recent.append({
                "task": task,
                "goal": db.get_goal(task["goal_slug"]),
                "completed": _format_timestamp(task["completed_at"]),
            })
    recent.sort(key=lambda row: row["task"]["completed_at"], reverse=True)

    return {
        "active_goals": sum(goal["status"] == "active" for goal in goals),
        "total_tasks": len(tasks),
        "completed_tasks": len(completed_tasks),
        "estimated_hours": sum(task["estimated_hours"] or 0 for task in tasks),
        "completed_estimated_hours": sum(task["estimated_hours"] or 0 for task in completed_tasks),
        "goal_rows": [_goal_row(goal) for goal in goals if goal["status"] == "active"],
        "recent": recent,
    }
```

Add this route inside `create_app()`:

```python
    @flask_app.get("/stats")
    def stats():
        return render_template("stats.html", view=_stats_view())
```

- [ ] **Step 4: Create the statistics template**

Create `dashboard/templates/stats.html`:

```html
{% extends "base.html" %}
{% block title %}统计 - 任务看板{% endblock %}
{% block content %}
  <h1>全局统计</h1>
  <section class="stat-grid">
    <article class="stat-card"><strong>{{ view.active_goals }}</strong><span>活跃目标</span></article>
    <article class="stat-card"><strong>{{ view.total_tasks }}</strong><span>总任务</span></article>
    <article class="stat-card"><strong>{{ view.completed_tasks }}</strong><span>已完成</span></article>
    <article class="stat-card"><strong>{{ "%.1f"|format(view.estimated_hours) }} h</strong><span>总预估耗时</span></article>
    <article class="stat-card"><strong>{{ "%.1f"|format(view.completed_estimated_hours) }} h</strong><span>已完成预估耗时</span></article>
  </section>

  <h2>活跃目标进度</h2>
  {% if view.goal_rows %}
    <div class="table-wrap">
      <table>
        <thead><tr><th>目标</th><th>完成</th><th>进度</th></tr></thead>
        <tbody>
          {% for row in view.goal_rows %}
            <tr>
              <td><a href="{{ url_for('goal_detail', slug=row.goal.slug) }}">{{ row.goal.name }}</a></td>
              <td>{{ row.completed }}/{{ row.total }}</td>
              <td class="progress-cell"><div class="progress"><div class="fill" style="width: {{ row.progress }}%"></div></div><span>{{ row.progress }}%</span></td>
            </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="notice">暂无活跃目标</p>
  {% endif %}

  <h2>最近完成</h2>
  {% if view.recent %}
    <div class="table-wrap">
      <table>
        <thead><tr><th>任务</th><th>目标</th><th>完成时间</th></tr></thead>
        <tbody>
          {% for row in view.recent %}
            <tr><td>{{ row.task.title }}</td><td>{{ row.goal.name }}</td><td>{{ row.completed }}</td></tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
  {% else %}
    <p class="notice">最近 7 天暂无已完成任务</p>
  {% endif %}
{% endblock %}
```

- [ ] **Step 5: Add statistics card styles**

Append to `dashboard/static/style.css`:

```css
.stat-grid { display: grid; grid-template-columns: repeat(5, minmax(140px, 1fr)); gap: 16px; margin-bottom: 32px; }
.stat-card { display: flex; flex-direction: column; gap: 8px; padding: 20px; background: #fff; border-radius: 8px; }
.stat-card strong { font-size: 1.8rem; color: #2e7d32; }
.stat-card span { color: #607d8b; }
```

- [ ] **Step 6: Run stats and regression tests**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "stats_route" -v
python -m pytest -q
```

Expected: both stats tests pass; full suite reports 41 passing tests.

- [ ] **Step 7: Commit the statistics view**

```bash
git add dashboard/app.py dashboard/templates/stats.html dashboard/static/style.css tests/test_dashboard.py
git commit -m "Add dashboard statistics view"
```

---

### Task 6: Runtime error page, responsive finishing styles, and operator documentation

**Files:**
- Modify: `dashboard/app.py`
- Modify: `dashboard/static/style.css`
- Modify: `tests/test_dashboard.py`
- Create: `dashboard/templates/error.html`
- Create: `dashboard/README.md`

**Interfaces:**
- Consumes: Flask's `@app.errorhandler(sqlite3.Error)` and the completed routes from Tasks 1–5.
- Produces: generic Chinese runtime DB error responses, complete desktop-first CSS, and documented installation/start/access procedures.

- [ ] **Step 1: Add failing runtime error and route smoke tests**

Add this import near the top of `tests/test_dashboard.py`:

```python
from unittest.mock import patch
```

Append these tests:

```python
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
```

- [ ] **Step 2: Run final behavior tests and verify failure**

Run:

```bash
python -m pytest tests/test_dashboard.py -k "database_error or all_dashboard_routes" -v
```

Expected: the DB error test fails because no `sqlite3.Error` handler exists; the smoke test passes.

- [ ] **Step 3: Add the generic runtime DB error handler**

In `dashboard/app.py`, add:

```python
import sqlite3
```

Inside `create_app()`, immediately after the Jinja global assignment, add:

```python
    @flask_app.errorhandler(sqlite3.Error)
    def handle_database_error(exc: sqlite3.Error):
        flask_app.logger.exception(
            "Dashboard database read failed",
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        return render_template("error.html"), 500
```

Create `dashboard/templates/error.html`:

```html
{% extends "base.html" %}
{% block title %}读取失败 - 任务看板{% endblock %}
{% block content %}
  <section class="empty-state">
    <h1>读取失败</h1>
    <p>读取任务数据失败，请检查服务日志</p>
  </section>
{% endblock %}
```

- [ ] **Step 4: Finish desktop-first and narrow-screen styles**

Append to `dashboard/static/style.css`:

```css
h2 { margin-top: 32px; }
@media (max-width: 800px) {
  .top-nav { align-items: flex-start; gap: 12px; }
  .container { padding: 16px; }
  .stat-grid { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
  .timeline-row { grid-template-columns: 1fr; gap: 8px; }
}
```

This preserves the spec's desktop-first layout while preventing unusable overflow on a narrow LAN client; it is not a separate mobile-optimized design.

- [ ] **Step 5: Create dashboard operator documentation**

Create `dashboard/README.md`:

```markdown
# Todo Scheduler Web Dashboard

只读个人任务看板。数据来自 `data/todos.db`，时间段来自 `config/schedule.json`；所有任务修改仍通过飞书中的 Claude 完成。

## 安装

在项目根目录执行：

```bash
python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r dashboard/requirements.txt
```

## 启动

```bash
python dashboard/app.py
```

服务监听 `0.0.0.0:5000`。本机访问 `http://127.0.0.1:5000`；同一局域网设备访问 `http://<本机局域网 IP>:5000`。

页面不会自动刷新。任务数据更新后按 F5 获取最新内容。

## 页面

- `/`：目标列表与进度
- `/goal/<slug>`：目标详情与任务
- `/today`：今日空闲时段与安排
- `/stats`：全局统计与最近完成
- `/health`：返回 `ok`

## 限制

该服务使用 Flask 开发服务器，仅适用于可信局域网中的个人使用。它不提供认证、HTTPS、编辑、自动刷新或公网部署能力。

若启动时提示数据库不存在，请先在项目根目录初始化或恢复 `data/todos.db`。若端口 5000 已占用，请先停止占用该端口的进程，再重新启动看板。
```

- [ ] **Step 6: Run dashboard tests, full regression suite, and formatting checks**

Run:

```bash
python -m pytest tests/test_dashboard.py -v
python -m pytest -q
python -m compileall -q dashboard scripts
git diff --check
```

Expected:
- All 15 dashboard tests pass.
- Full suite reports 43 passing tests.
- `compileall` exits 0 with no output.
- `git diff --check` exits 0 with no output.

- [ ] **Step 7: Perform manual route acceptance from localhost**

Start the server from the project root:

```bash
python dashboard/app.py
```

In another terminal, run:

```bash
python -c "from urllib.request import urlopen; paths=['/','/goal/example-goal','/today','/stats','/health']; print([(p, urlopen('http://127.0.0.1:5000'+p).status) for p in paths])"
```

Expected with the seeded `example-goal` database:

```text
[('/', 200), ('/goal/example-goal', 200), ('/today', 200), ('/stats', 200), ('/health', 200)]
```

Open `http://127.0.0.1:5000` in a browser and verify progress bars, status colors, navigation, and F5 refresh. From a phone on the same trusted LAN, open `http://<本机局域网 IP>:5000` and verify the goal list loads. Stop the server with Ctrl+C.

- [ ] **Step 8: Commit the finished dashboard**

```bash
git add dashboard/app.py dashboard/templates/error.html dashboard/static/style.css dashboard/README.md tests/test_dashboard.py
git commit -m "Finish dashboard error handling and documentation"
```

---

## Final Verification

- [ ] Run the complete automated suite:

```bash
python -m pytest -q
```

Expected: 43 tests pass.

- [ ] Confirm the dashboard remains read-only by reviewing the dashboard package for write calls:

```bash
python -c "from pathlib import Path; text='\n'.join(p.read_text(encoding='utf-8') for p in Path('dashboard').rglob('*') if p.is_file() and p.suffix in {'.py','.html'}); forbidden=['create_goal(', 'create_task(', 'update_goal_status(', 'update_task_status(', 'set_today_focus(', 'mark_task_reminded(']; found=[name for name in forbidden if name in text]; print(found); raise SystemExit(bool(found))"
```

Expected: prints `[]` and exits 0.

- [ ] Confirm the working tree contains only intentional changes before the final review:

```bash
git status --short
```

Expected: no output after all task commits.
