# Web Dashboard — Design Spec

**Date:** 2026-08-03
**Status:** Design approved; written spec awaiting user review
**Author:** Claude (via brainstorming session)
**Parent project:** Todo Scheduler (`docs/superpowers/specs/2026-08-03-todo-scheduler-design.md`)

---

## 1. Purpose & Background

Add a personal web dashboard to the existing todo scheduler that displays goal progress, task statuses, today's schedule, and aggregate statistics. The dashboard is for **personal use only**, accessible over LAN, refresh-on-demand (manual F5).

This is a **read-only** view layer. All writes still go through Claude via Feishu messages. The dashboard reads the existing SQLite database and schedule configuration, then renders HTML without modifying either source.

---

## 2. User Constraints

| Requirement | Decision |
|-------------|----------|
| Use case | Personal overview dashboard |
| Audience | Self only (no auth, no sharing) |
| Views | (1) Goal list, (2) Goal detail, (3) Today schedule, (4) Global stats |
| Tech stack | Python Flask + HTML + minimal JS |
| Refresh | Manual (F5) |
| Visualization | CSS progress bars + status colors (no Chart.js) |
| Access | LAN: `0.0.0.0:5000` |

---

## 3. Architecture Overview

### File Layout

```
todos/
├── dashboard/                       # NEW
│   ├── app.py                       # Flask app + routes
│   ├── requirements.txt             # flask
│   ├── templates/
│   │   ├── base.html                # Common layout + nav
│   │   ├── index.html               # View 1: Goal list
│   │   ├── goal_detail.html         # View 2: Single goal
│   │   ├── today.html               # View 3: Today schedule
│   │   └── stats.html               # View 4: Global stats
│   ├── static/
│   │   └── style.css                # Progress bar + status colors
│   └── README.md                    # Start command + access URL
└── tests/
    └── test_dashboard.py            # NEW: Flask route tests
```

### Reuse from existing project

- `scripts/db.py` — all DB queries
- `scripts/scheduler.py` — `get_slots_for_date` and `compute_schedule` for today view
- `data/todos.db` — goals, tasks, focus, and statistics
- `config/schedule.json` — slot definitions

### Components

| Component | Responsibility | Lines of code (est.) |
|-----------|----------------|----------------------|
| `app.py` | Flask routes + data loading | ~150 |
| `base.html` | Common nav + layout | ~30 |
| `index.html` | Goal list table | ~50 |
| `goal_detail.html` | Goal info + task table | ~70 |
| `today.html` | Today timeline | ~50 |
| `stats.html` | Aggregate cards + lists | ~60 |
| `style.css` | Progress bar + status colors | ~80 |
| `test_dashboard.py` | Route tests | ~100 |

### Flask Routes

| Route | Method | View | Data calls |
|-------|--------|------|-----------|
| `/` | GET | Goal list | `db.list_goals` + per-goal counts |
| `/goal/<slug>` | GET | Goal detail | `db.get_goal` + `db.list_tasks(goal_slug=slug)` |
| `/today` | GET | Today schedule | `scheduler.get_slots_for_date` + `scheduler.compute_schedule` + `db.get_today_focus` + pending-task counts |
| `/stats` | GET | Global stats | Aggregations over `db.list_goals` + tasks |
| `/health` | GET | Health check | None — returns "ok" |

### Data Flow

```
Browser GET /goal/a-stock
   ↓
Flask route goal_detail(slug="a-stock")
   ↓
db.get_goal("a-stock") → goal dict
db.list_tasks(goal_slug="a-stock") → tasks list
   ↓
Jinja2 renders goal_detail.html with goal + tasks
   ↓
HTML returned to browser (CSS already loaded)
   ↓
Browser renders with progress bars + status colors
```

User presses F5 → repeat.

---

## 4. View Designs

### View 1: Goal List `/`

Layout: Table with one row per goal.

| Column | Content | Style |
|--------|---------|-------|
| Name | goal name (link to detail) | text |
| Status | active / paused / completed | status pill |
| Total | task count | number |
| Done | completed task count | number |
| Progress | progress bar + percentage | CSS bar |
| Current | in-progress task title (or "—") | text |

Example row:
```
[A 股量化](a-stock)  [进行中]  12/2  17%  [▓▓░░░░░░░░]  当前: T003 - 数据采集器
```

If no goals exist: show "暂无目标 — 通过飞书告诉 Claude 添加你的第一个目标".

### View 2: Goal Detail `/goal/<slug>`

**Header section:**
- Goal name (h1)
- Status badge
- Created date
- Description (from `goals.description` in the DB)
- Summary: "N tasks, M done, P% complete, total estimated X hours"

**Tasks table:**

| ID | Title | Hours | Dependencies | Status | Last Reminded | Completed |
|----|-------|-------|--------------|--------|---------------|-----------|
| T001 | 数据采集器 | 2.0 | — | done | 2026-08-03 12:01 | 2026-08-03 14:30 |
| T002 | 解析器 | 1.5 | T001 ✓ | pending | — | — |

Dependencies shown as: `→ T001 ✓` (chain of completed deps).

### View 3: Today Schedule `/today`

Layout: Vertical timeline of today's free slots.

**Header:**
- Date (e.g., "2026-08-03 周一")
- Today's focus: `[example-goal] 示例目标` (or "未设置")

**Slot list:**
```
07:30-09:00  ────────── (无任务)
12:00-13:00  [示例目标] T002 - 子任务2           [详情]
18:00-19:00  ────────── (无任务)
21:00-23:00  [A 股量化] T003 - 数据采集器
              └─ 依赖: T001 ✓, T002 ✓
```

**Footer:**
- "今日剩余 2 个任务未安排" when active pending tasks are not present in today's schedule
- "全部任务已完成" when no active pending tasks remain

Data sources:
- `scheduler.get_slots_for_date(today_date)` provides every configured slot, including empty slots.
- `scheduler.compute_schedule(today_focus, today_date, "00:00", max_slots=len(today_slots))` provides assignments; only rows whose `date` equals `today_date` are displayed.
- Pending tasks not represented in those assignments determine the "未安排" count.

### View 4: Global Stats `/stats`

**Stat cards (top row):**
- 活跃目标: 3
- 总任务: 25
- 已完成: 12
- 总预估耗时: 38.5 h
- 已完成预估耗时: 15 h

Both hour totals are sums of `tasks.estimated_hours`; the completed total includes only tasks with `status = "done"`. The dashboard does not claim to track actual elapsed time.

**Goal progress list (middle):** mini-table with progress bars for each active goal.

**Recently completed (bottom):** last 7 days' completed tasks with timestamps.

---

## 5. Common UI Elements

### Top Navigation (in base.html)

```html
<nav>
  <a href="/">目标</a>
  <a href="/today">今日</a>
  <a href="/stats">统计</a>
  <span class="hint">手动按 F5 刷新</span>
</nav>
```

### Status Colors (style.css)

```css
.status-done        { background: #4caf50; color: white; }
.status-in_progress { background: #ff9800; color: white; }
.status-pending     { background: #9e9e9e; color: white; }
.status-skipped     { background: #f44336; color: white; }
.status-completed   { background: #2196f3; color: white; }
.status-active      { background: #4caf50; color: white; }
.status-paused      { background: #9e9e9e; color: white; }
```

### Progress Bar (style.css)

```css
.progress { background: #e0e0e0; height: 18px; border-radius: 3px; }
.progress > .fill { background: #4caf50; height: 100%; border-radius: 3px; }
```

Template usage:
```html
<div class="progress"><div class="fill" style="width: 17%"></div></div>
```

---

## 6. Core Flows

### Flow 1: Start Dashboard

```bash
cd D:/codeSpace/claudecode/stock_data/todos
pip install -r dashboard/requirements.txt   # install flask
python dashboard/app.py
```

Output:
```
 * Running on http://0.0.0.0:5000
 * Running on http://127.0.0.1:5000
Press Ctrl+C to quit
```

User opens browser at `http://<machine-ip>:5000` (or `localhost:5000` from same machine).

### Flow 2: Each HTTP Request

1. Flask receives GET.
2. Route handler imports `db`, queries DB (synchronous SQLite, fast for personal use).
3. For `/today`, loads today's configured slots with `scheduler.get_slots_for_date`, calls `scheduler.compute_schedule`, and joins scheduled task/goal details for rendering.
4. Jinja2 renders the appropriate template.
5. HTML returned to browser (status 200).
6. Browser renders with CSS already loaded.

Total latency expected: < 100 ms on modern hardware.

### Flow 3: Manual Refresh

User presses F5 → browser re-sends GET → Flow 2 repeats. No server-side caching.

### Flow 4: Stop Dashboard

`Ctrl+C` in terminal. Flask development server shuts down cleanly.

### Flow 5: Adding/Updating Data

No dashboard interaction needed. User tells Claude via Feishu → Claude updates DB → user refreshes dashboard → new data shown.

---

## 7. Error Handling

| Scenario | Behavior |
|----------|----------|
| `data/todos.db` missing at startup | Check file existence before opening SQLite; print error and exit with non-zero status |
| DB read fails after startup | Return a generic Chinese error page with status 500; log the exception server-side |
| Port 5000 in use | Catch OSError, print message, exit |
| `/goal/<slug>` with unknown slug | Return 404 with message "目标不存在" |
| `/today` with no tasks | Show "今日无安排" message |
| `/` with no goals | Show "暂无目标" with onboarding hint |
| Jinja2 template error | Flask default error handler (500) |

---

## 8. Testing Strategy

### Unit Tests (`tests/test_dashboard.py`)

Use Flask's `test_client()` for in-process testing:

- `test_index_route` — GET `/` returns 200, contains goal name
- `test_index_no_goals` — empty DB → 200, shows "暂无目标"
- `test_goal_detail_route` — GET `/goal/<slug>` returns 200, contains tasks
- `test_goal_detail_404` — unknown slug → 404
- `test_today_route` — GET `/today` returns 200, shows date
- `test_stats_route` — GET `/stats` returns 200, contains stat numbers
- `test_health_route` — GET `/health` returns "ok"

Use a separate test DB (set `TODO_DB_PATH` env var) to avoid touching real data.

### Manual Acceptance

- [ ] `python dashboard/app.py` starts without error
- [ ] All 4 routes return 200
- [ ] Progress bars render correctly
- [ ] Status colors match (done=green, in_progress=orange, etc.)
- [ ] Updating task via Claude shows in dashboard after refresh
- [ ] LAN device (phone) can access `http://<ip>:5000`

---

## 9. Deployment

- Flask development server runs on `0.0.0.0:5000` (LAN accessible)
- Not production-grade — for personal use only
- For 24/7 availability, run in background (`python dashboard/app.py &`) or via systemd/Task Scheduler
- No HTTPS, no auth — assumes trusted LAN

---

## 10. Future Enhancements

- Auto-refresh polling (out of scope for v1)
- Mobile-optimized layout (current is desktop-first)
- WebSocket live updates (out of scope)
- Editing tasks via dashboard (out of scope — keep Claude-mediated)
- Per-goal timeline visualization (could add later)

---

## 11. Acceptance Criteria

This design is accepted when:

1. User has approved all 5 design sections.
2. Spec file is committed.
3. User reviews the committed spec.
4. Implementation plan is written (via `writing-plans` skill).

Ready to proceed to implementation planning on approval.