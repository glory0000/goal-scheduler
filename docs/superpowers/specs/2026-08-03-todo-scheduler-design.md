# Todo Scheduler — Design Spec

**Date:** 2026-08-03
**Status:** Draft (awaiting user approval)
**Author:** Claude (via brainstorming session)

---

## 1. Purpose & Background

Build a personal todo scheduler that:

- Tracks user's available time slots
- Manages multiple concurrent goals with task decomposition
- Sends reminders via Feishu (飞书) at each free time slot
- Reschedules dynamically based on completion, focus changes, or plan updates
- Survives session crashes via a daily-rebuild cron fallback

The system runs entirely under Claude's control, storing state in Markdown + SQLite + JSON, with no long-running service.

---

## 2. User Constraints

### Available Time Slots (from user)

- **Weekday commute:** 07:30–09:00, 21:00–23:00
- **Weekday breaks:** 12:00–13:00, 18:00–19:00
- **Weekend blocks:** 09:30–13:30, 14:00–18:00, 19:00–23:00

Slots are configurable via `config/schedule.json`.

### Task Lifecycle Rules

- A task is **done only when user explicitly says so**.
- No reply after a reminder = task **not done**.
- Unfinished tasks roll forward to the next free slot.
- New goals/tasks can be added at any time; scheduling re-balances after any change.

### Multi-Goal Coordination

- User dynamically sets `today_focus` each day (which goal gets priority today).
- Other active goals fill remaining slots.
- Per-goal completion percentage shown in `goal.md`.

---

## 3. Architecture Overview

### Directory Layout

```
todos/
├── README.md                       # Project description & user manual
├── .git/                           # Auto-commit every change
├── docs/
│   └── superpowers/
│       └── specs/
│           └── 2026-08-03-todo-scheduler-design.md
├── goals/
│   ├── index.md                    # Goal directory (one line per goal)
│   └── <goal-slug>/
│       ├── goal.md                 # Goal description + completion stats
│       └── history/                # Optional: archived snapshots
├── config/
│   └── schedule.json               # Free-slot configuration
├── data/
│   └── todos.db                    # SQLite database
├── scripts/
│   ├── db.py                       # SQLite helper (read/write)
│   ├── scheduler.py                # Compute next-task-per-slot
│   ├── reminder.py                 # Build reminder message text
│   ├── dump_state.sh               # Print current DB+file state
│   ├── simulate_reminder.sh        # Simulate a reminder at a given time
│   └── break_session.sh            # Kill active session for fallback test
├── logs/
│   └── cc-connect.log              # cc-connect command history
└── backups/
    └── <file>.bak                  # Rolling backups (keep last 5)
```

### Components

| Component | Responsibility | Implemented By |
|-----------|----------------|----------------|
| **Plan Manager** | Create / read / update `goal.md` | Claude direct edit |
| **DB Manager** | Read / write SQLite | `scripts/db.py` |
| **Scheduler** | Compute which task runs in which slot | `scripts/scheduler.py` |
| **Reminder Builder** | Format reminder message text | `scripts/reminder.py` |
| **Reminder Manager** | Create / list / delete cc-connect timer & cron | Claude (via Bash) |
| **Notifier** | Send Feishu messages | Claude direct reply |

### Data Flow

```
[User Feishu message]      → Claude → parse intent
                                       ├─ Add goal      → brainstorm → write goal.md + DB → re-schedule
                                       ├─ Complete task → update DB → cancel next timer → create new timer
                                       ├─ Change focus  → update DB → cancel all pending → rebuild
                                       └─ Query status  → read DB + goal.md → reply

[cc-connect timer fires]   → Claude → read DB → compute current task → send reminder → create next timer
[cc-connect cron (0:05)]   → Claude → rebuild today's remaining timers (fallback)
```

### Key Invariants

1. **`data/todos.db` is the single source of truth** for task state.
2. Every file write is followed by `git commit`.
3. cc-connect state (cron/timer IDs) is **not persisted** — only the 1 fallback cron is intentional.
4. Timer chain is naturally terminating: when no more pending tasks remain, no new timer is created.

---

## 4. Data Model

### 4.1 `goals/<slug>/goal.md`

```markdown
# 目标：<目标名称>

> 创建日期：YYYY-MM-DD
> 状态：进行中 | 已完成 | 已暂停

## 目标描述
（详细描述目标、为什么做、达成标准）

## 任务进度
- 总任务数：N
- 已完成：N
- 进行中：N
- 待办：N
- 完成率：N%

## 备注
（参考资料、风险、链接等）
```

### 4.2 `config/schedule.json`

```json
{
  "weekday": [
    {"start": "07:30", "end": "09:00", "label": "morning"},
    {"start": "12:00", "end": "13:00", "label": "lunch"},
    {"start": "18:00", "end": "19:00", "label": "evening"},
    {"start": "21:00", "end": "23:00", "label": "night"}
  ],
  "weekend": [
    {"start": "09:30", "end": "13:30", "label": "morning-block"},
    {"start": "14:00", "end": "18:00", "label": "afternoon-block"},
    {"start": "19:00", "end": "23:00", "label": "evening-block"}
  ]
}
```

### 4.3 SQLite Schema (`data/todos.db`)

```sql
CREATE TABLE goals (
  slug TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  description TEXT,
  status TEXT NOT NULL DEFAULT 'active',   -- active / paused / completed
  total_tasks INTEGER DEFAULT 0,
  completed_tasks INTEGER DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE tasks (
  id TEXT PRIMARY KEY,                     -- e.g. T001 (per goal: T001..TNNN)
  goal_slug TEXT NOT NULL,
  sequence INTEGER NOT NULL,                -- ordering within goal
  title TEXT NOT NULL,
  description TEXT,
  estimated_hours REAL,
  depends_on TEXT,                          -- JSON array of task ids
  status TEXT NOT NULL DEFAULT 'pending',   -- pending / in_progress / done / skipped
  last_reminded_at TEXT,                    -- last time reminder was sent for this task
  completed_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY (goal_slug) REFERENCES goals(slug)
);

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
-- settings rows:
--   today_focus = "<goal-slug>"
--   current_task_id = "<task-id>"  (which task is "now")
```

### 4.4 `goals/index.md`

```markdown
# 目标索引

- [A股量化本地数据项目](a-stock-quant/goal.md) — 状态：进行中 — 完成率 17%
- [视频剪辑学习](video-editing/goal.md) — 状态：未开始 — 完成率 0%
```

---

## 5. Core Flows

### 5.1 Add a New Goal

1. User announces goal in Feishu.
2. Claude enters `brainstorming` skill: asks clarifying questions, proposes task breakdown, gets user approval.
3. Claude creates `goals/<slug>/goal.md` with goal description.
4. Claude inserts `goals` row + `tasks` rows in SQLite.
5. Claude updates `goals/index.md`.
6. `git commit`.
7. Claude asks: "Is this your focus today?" — if yes, sets `today_focus` and triggers re-schedule (Flow 5.5).

### 5.2 Complete a Task

1. User: "T001 完成了" (or "T001 完成 70%" → marked `in_progress`).
2. Claude updates SQLite (`tasks.status`, `tasks.completed_at`, `goals.completed_tasks`).
3. Claude updates `goal.md` 任务进度 section.
4. Claude updates `goals/index.md` completion %.
5. `git commit`.
6. Claude calls `cc-connect timer list` to find the next pending timer.
7. Claude deletes that timer (`cc-connect timer del`).
8. Claude computes the next task (using `scheduler.py`) and creates a new timer for the next free slot.
9. Claude replies confirming completion + showing next slot's task.

### 5.3 Change Focus / Skip Slot / Pause Goal

1. User: "today focus = video-editing" / "跳过今晚时段" / "暂停 a-stock".
2. Claude updates `settings.today_focus` or `goals.status`.
3. `git commit`.
4. **Re-schedule flow (5.5).**

### 5.4 Update Goal / Add or Modify Tasks

1. User: "在 A 股目标加一个 akshare 学习任务" / "T003 改为先做 T005".
2. Claude inserts/updates/deletes SQLite rows.
3. Claude updates `goal.md` and `index.md`.
4. `git commit`.
5. **Re-schedule flow (5.5).**

### 5.5 Re-schedule (Cancel Pending + Rebuild)

1. `cc-connect timer list` — find all pending timers.
2. `cc-connect timer del <id>` for each.
3. Read `today_focus`, active goals, pending tasks, `schedule.json`.
4. Compute new mapping: for each upcoming free slot, pick a task using rules in §6.
5. `cc-connect timer add --at "<slot-start>" --prompt "<reminder message>"` for each.
6. Reply to user with new schedule summary (or silent if triggered by cron).

### 5.6 Timer Fires (Auto Reminder)

1. cc-connect timer fires; Claude receives the prompt.
2. Claude reads `data/todos.db`:
   - `settings.today_focus`
   - active goal's pending tasks (with dependencies satisfied)
   - current slot's assigned task (if any)
3. Claude reads `config/schedule.json` to confirm slot validity.
4. Claude generates reminder text via `reminder.py`.
5. Claude sends the message back through Feishu.
6. Claude updates `tasks.last_reminded_at`.
7. Claude computes next slot & task, creates next timer (chain continues).
8. If no more tasks today → do NOT create next timer (chain ends naturally).

### 5.7 Daily Fallback Cron (00:05)

Single cc-connect cron, only fallback mechanism:

1. Cron fires.
2. Claude enumerates today's remaining free slots from `schedule.json`.
3. Claude queries `cc-connect timer list` for current pending timers.
4. For each slot that **should** have a reminder but doesn't → create one.
5. For each pending timer pointing at a stale task (e.g., task already done) → delete.
6. `git commit` only if any DB or file changed.

---

## 6. Scheduling Algorithm

Inputs:
- `today_focus` (slug)
- All active goals (`status='active'`)
- All pending tasks with `depends_on` satisfied
- `schedule.json` for today's day-of-week
- Current time & remaining slots today

Rules:

1. **Focus first:** If `today_focus` is set and active, fill slots with its pending tasks in `sequence` order.
2. **Overflow:** When focus goal's pending tasks run out, fill remaining slots with other active goals' pending tasks, rotating by oldest `updated_at`.
3. **One task per slot:** Task's `estimated_hours` should not exceed slot duration; if it does, warn user and ask whether to split or override.
4. **Dependency respect:** Skip tasks whose `depends_on` includes any non-done task.
5. **No re-use:** A task already done or skipped is excluded.
6. **Daily cap:** No more tasks scheduled than free slots remaining today; surplus rolls to next day.

Output: List of `{slot_start, goal_slug, task_id}` to create timers for.

---

## 7. Reminder Message Format

Standard template:

```
⏰ 21:00 时段开始（21:00-23:00）

📌 目标：A 股量化本地数据项目
🎯 任务：T002 - 实现数据采集器基础架构
⏱️ 预计耗时：2 小时
📎 依赖：T001 已完成 ✓

完成后请回复 "T002 完成了"。
如需跳过请回复 "跳过"。
如需调整今日重点请回复 "今日重点 = xxx"。
```

Generated by `scripts/reminder.py` using DB data.

---

## 8. Error Handling

| Scenario | Detection | Recovery |
|----------|-----------|----------|
| A. Session dies, timer chain breaks | Daily 00:05 cron detects missing timers | Rebuild today's remaining timers |
| B. User doesn't reply after reminder | Next reminder arrives; check `tasks.last_reminded_at` | Ask user if reschedule needed; don't auto-mark failed |
| C. Git conflict / commit fails | `git commit` non-zero exit | Retry; if still fails, alert user |
| D. SQLite corruption | `sqlite3 open` fails | Alert user; attempt `git checkout` restore; backup current file to `backups/` |
| E. cc-connect command fails | Non-zero exit | Retry 3x; log to `logs/cc-connect.log`; notify user |
| F. Task dependencies not satisfied | Scheduler finds `depends_on` unmet | Skip task; pick next eligible; mention in reply |
| G. All today's tasks done but slots remain | Scheduler finds no eligible task | Don't create timer; chain ends naturally |
| H. Manual edit of `goal.md` desyncs from DB | Each Claude op re-reads DB | Trust DB as source of truth; warn user if inconsistency |
| I. `today_focus` points to non-existent / paused goal | Validation at read time | Alert user; fallback to most-recently-active goal |
| J. `schedule.json` invalid (end < start) | Validation on load | Refuse to save; demand correction |

### General Principles

1. **Never lose data.** Back up before overwriting (keep last 5 in `backups/`).
2. **Never silently fail.** All errors logged + surfaced.
3. **Atomic writes.** Use SQLite transactions; rollback on error.
4. **Idempotent ops.** Running the same operation twice produces the same final state.

---

## 9. User Message Disambiguation

- "T001 完成了" → default = entire T001 done. If user meant a sub-step, they'll correct.
- "我做了一些" / "T001 进度 50%" → ambiguous; Claude asks for clarification.
- "今天重点做 X" → update `today_focus`, trigger re-schedule.
- "跳过今晚" → cancel tonight's timers, no replacement.
- "暂停 A 股" → set `goals.status='paused'`, skip in scheduling.

---

## 10. Testing Strategy

### 10.1 Unit-level Verification

| Operation | Validation |
|-----------|-----------|
| Add goal | Brainstorm approval; DB rows; goal.md correctness |
| Complete task | Status change; count update; timer re-creation |
| Change focus | DB update; timer chain rebuild |
| Timer fires | Correct message content; next timer created |
| Fallback cron | Manually break chain; verify rebuild |
| Error recovery | Inject corrupted SQLite / JSON; verify recovery |

### 10.2 End-to-End Scenarios

**Scenario A — Full day:**
1. 07:30 reminder fires → user completes → next timer created for 12:00
2. 12:00 → complete → 18:00
3. 18:00 → complete → 21:00
4. 21:00 → complete → chain ends (no more slots today)

**Scenario B — Cross-day:**
1. Friday evening completes last task
2. Saturday 09:30 should fire fresh reminder
3. Verify fallback cron created Sat 09:30 timer

**Scenario C — Multi-goal:**
1. Switch `today_focus` mid-day
2. Verify pending timers cancelled and rebuilt correctly

**Scenario D — Session crash:**
1. Kill session mid-day
2. 00:05 cron rebuilds missing timers
3. Verify timer contents match original plan

### 10.3 Shadow Period (1-2 weeks pre-launch)

Run Claude-managed scheduling in parallel with user's manual scheduling; compare results; adjust algorithm before going fully automatic.

### 10.4 Test Scripts

- `scripts/dump_state.sh` — print current DB + file state
- `scripts/simulate_reminder.sh <time>` — simulate reminder firing at given time
- `scripts/break_session.sh` — kill active session for fallback testing

### 10.5 User Acceptance Checklist

- [ ] Add 3 test goals (with dependencies)
- [ ] Run 7 simulated days; verify reminder timing & content
- [ ] Test completion → next reminder update
- [ ] Test `today_focus` switching
- [ ] Test slot skipping
- [ ] Kill session; verify fallback cron rebuilds
- [ ] Corrupt SQLite; verify recovery
- [ ] Edit `goal.md` manually; verify Claude notices desync

---

## 11. Initial Setup (one-time)

Run these once before first use:

```bash
# 1. Enter project directory
cd D:/codeSpace/claudecode/stock_data/todos

# 2. Initialize git
git init
git config user.email "todo-scheduler@local"
git config user.name "todo-scheduler"

# 3. Create directory skeleton
mkdir -p goals config data scripts logs backups docs/superpowers/specs

# 4. Create empty SQLite database
sqlite3 data/todos.db < schema.sql

# 5. Create config/schedule.json (paste weekday/weekend schedule)

# 6. Create scripts/ (db.py, scheduler.py, reminder.py, *.sh)

# 7. Write README.md (user manual)

# 8. Initial commit
git add -A
git commit -m "Initialize todo scheduler"

# 9. Create the single fallback cron
cc-connect cron add \
  --cron "5 0 * * *" \
  --prompt "Rebuild today's remaining timers for todos scheduler. Read data/todos.db and config/schedule.json. Cancel stale timers and create missing ones. Commit any changes." \
  --desc "Daily reminder chain rebuild"
```

**Expected steady state after setup:**
- `cc-connect cron list` → 1 cron (the fallback)
- `cc-connect timer list` → N pending timers (N = future free slots with pending tasks)
- `data/todos.db` → empty goals/tasks tables until first goal added

---

## 12. Open Questions / Future Enhancements

- Multi-day planning view (week / month ahead) — out of scope for v1.
- Notifications on task overdue (vs. silent skip) — out of scope for v1.
- Cross-device sync (if user wants mobile) — not planned.
- Pomodoro-style sub-reminders within a slot — explicitly not chosen (task-level granularity only).
- Automatic estimation refinement (Claude learns actual vs estimated hours) — possible v2.

---

## 13. Acceptance Criteria

This design is accepted when:

1. User has approved all 5 design sections.
2. The spec file is committed.
3. User has reviewed the committed spec.
4. Implementation plan is written (via `writing-plans` skill).

Ready to proceed to implementation planning on approval.