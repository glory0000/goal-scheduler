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

## Migrations

Schema changes land as numbered SQL files in `migrations/`:

```bash
python scripts/migrate.py init      # one-time: stamp schema_version=1
python scripts/migrate.py upgrade   # apply any pending migrations/
```

Add a new migration by creating `migrations/NNN_description.sql` where `NNN`
is the next three-digit version (e.g. `002_add_started_at.sql`). The runner
applies each file in order; a failed file is rolled back and leaves
`schema_version` at the prior value.

## Common commands

```bash
# Dump current state
bash scripts/dump_state.sh

# Simulate a reminder at a given time
bash scripts/simulate_reminder.sh "2026-08-04 21:00"

# Simulate session crash to test fallback cron
bash scripts/break_session.sh
```

### CLI

The unified Python CLI is a thin wrapper over `scripts/db.py` and
`scripts/scheduler.py`. It is what Claude and the user call to read or
change state. Output is human-readable by default; add `--json` for a
single parseable JSON object on stdout. All errors go to stderr.

```bash
# View state
python scripts/cli.py status
python scripts/cli.py today

# Add a goal / task
python scripts/cli.py goal add a-stock-quant "A股量化" --description "策略回测与实盘"
python scripts/cli.py task add a-stock-quant-T013 a-stock-quant 13 "跑通回测示例" --hours 1.0

# Update progress
python scripts/cli.py task update a-stock-quant-T013 in_progress
python scripts/cli.py task update a-stock-quant-T013 done

# Change today's focus
python scripts/cli.py focus set a-stock-quant
python scripts/cli.py focus clear

# Rebuild today's reminder timers
python scripts/cli.py rebuild-timers
# Output: Rebuilt timers for 2026-08-04:
#           added   2  (18:00 evening → T002, 21:00 night → T003)
#           removed 0
#           kept    0

python scripts/cli.py rebuild-timers --json
# Output: {"date": "2026-08-04", "added": [...], ...}

# Regenerate goals/index.md from current DB state (manual)
python scripts/cli.py sync-md
# Output: Synced 3 goals to goals/index.md (active=1, paused=1, completed=1)
#           - +example-goal      (进行中 50%)
#           - ~paused-goal       (已暂停 33%)
#           - ~old-completed     (已完成 100%)

python scripts/cli.py sync-md --json
# Output: {"path": "goals/index.md", "synced_count": 3, ...}

# Auto-fires after goal add / task add / task update — no need to run manually.
```

```bash
# List goals (default hides archived)
python scripts/cli.py goal list
python scripts/cli.py goal list --all
python scripts/cli.py goal list --status archived
python scripts/cli.py goal list --json

# Show one goal
python scripts/cli.py goal show example-goal
python scripts/cli.py goal show example-goal --json

# Change a goal's status (use 'paused' / 'completed'; not 'archived')
python scripts/cli.py goal update example-goal --status paused
python scripts/cli.py goal update example-goal --status completed --json

# Soft-delete (archive) and restore
python scripts/cli.py goal delete example-goal
python scripts/cli.py goal restore example-goal

# Same surface for tasks
python scripts/cli.py task list
python scripts/cli.py task list --goal example-goal
python scripts/cli.py task list --status done --all
python scripts/cli.py task show example-goal-T001
python scripts/cli.py task delete example-goal-T001
python scripts/cli.py task restore example-goal-T001
```

Exit codes: `0` success, `1` input error, `2` database not initialized,
`3` resource not found.

## Fallback cron

The system relies on a single cc-connect cron to rebuild broken timer chains. Verify with:

```bash
cc-connect cron list
```

Expected: 1 job at `5 0 * * *` (00:05 daily). If missing, recreate:

```bash
cc-connect cron add --cron "5 0 * * *" --prompt "Daily fallback for todos scheduler. Read data/todos.db and config/schedule.json. For each remaining free slot today, ensure a cc-connect timer exists pointing at a pending task. Cancel stale timers (pointing at done/skipped tasks or past slots). Commit any DB or file changes. If everything is in order, no commit needed." --desc "Todo scheduler: daily reminder chain rebuild"
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
