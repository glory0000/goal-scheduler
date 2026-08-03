# Todo Scheduler

Claude-driven personal todo scheduler.

## What it does

- Tracks your free time slots (weekday + weekend).
- Manages multiple goals with task decomposition.
- Sends Feishu reminders at each free time slot.
- Re-schedules dynamically after task completion, focus changes, or goal updates.
- Survives session crashes via a daily rebuild cron.

## Quick start

1. Run setup (see `docs/superpowers/specs/...` Initialization section).
2. Add your first goal via Feishu: tell Claude what you want to do.
3. Claude will brainstorm the task breakdown, write it to `goals/<slug>/goal.md` and SQLite.
4. Set your focus: "今日重点 = a-stock-quant".
5. Claude creates the first timer. Each reminder fires the next one.

## Key files

- `goals/<slug>/goal.md` — goal description + progress stats.
- `data/todos.db` — SQLite: goals, tasks, settings.
- `config/schedule.json` — your free time slots.
- `scripts/db.py`, `scheduler.py`, `reminder.py` — Python helpers.
- `logs/cc-connect.log` — cc-connect command history.

## Common commands

```bash
# Dump current state
bash scripts/dump_state.sh

# Simulate a reminder at a given time
bash scripts/simulate_reminder.sh "2026-08-04 21:00"

# Simulate session crash to test fallback cron
bash scripts/break_session.sh
```

## Fallback cron

The system relies on a single cc-connect cron to rebuild broken timer chains. Verify with:

```bash
cc-connect cron list
```

Expected: 1 job at `5 0 * * *` (00:05 daily). If missing, recreate:

```bash
cc-connect cron add --cron "5 0 * * *" --prompt "<see commit history>" --desc "Todo scheduler: daily reminder chain rebuild"
```

## Shadow period (1-2 weeks)

Before fully trusting the scheduler, run it in parallel with your manual planning:

1. Each morning, dump state via `bash scripts/dump_state.sh`.
2. Compare Claude's planned schedule with your manual plan.
3. Note discrepancies (Claude missed X, over-allocated Y, etc.).
4. Tweak `config/schedule.json` or scheduling rules in `scheduler.py` as needed.
5. Once 7+ days match consistently, remove the example goal and go live.

## When to engage Claude

Tell Claude any of these via Feishu:

- "新目标：<描述>" — start a new goal (Claude will brainstorm).
- "Txxx 完成了" / "Txxx 进度 50%" — update task status.
- "今日重点 = <slug>" — change focus.
- "跳过 <时段>" / "暂停 <slug>" — skip or pause.
- "<目标> 增加任务：<描述>" — add a task.
- "删除 Txxx" / "改 Txxx 为先做 Tyyy" — modify tasks.
