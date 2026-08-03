#!/usr/bin/env bash
# Simulate a reminder firing at the given "YYYY-MM-DD HH:MM".
# Prints what message WOULD be sent and what next timer WOULD be created.

set -e
cd "$(dirname "$0")/.."

if [ -z "$1" ]; then
  echo "Usage: $0 'YYYY-MM-DD HH:MM'"
  exit 1
fi

DATE_STR=$(echo "$1" | awk '{print $1}')
TIME_STR=$(echo "$1" | awk '{print $2}')

FOCUS=$(python -c "import db; print(db.get_today_focus() or '')")
echo "Today focus: $FOCUS"

echo ""
echo "Plan from $1:"
python -c "
import sys
sys.path.insert(0, 'scripts')
import scheduler
plan = scheduler.compute_schedule('$FOCUS', '$DATE_STR', '$TIME_STR', max_slots=5)
for p in plan:
    print(f\"  {p['date']} {p['slot_start']}-{p['slot_end']} {p['goal_slug']} {p['task_id']}\")
if not plan:
    print('  (nothing scheduled)')
"

echo ""
echo "First reminder message preview:"
python -c "
import sys
sys.path.insert(0, 'scripts')
import db, scheduler, reminder

FOCUS = '$FOCUS'
plan = scheduler.compute_schedule(FOCUS, '$DATE_STR', '$TIME_STR', max_slots=1)
if not plan:
    print('  (no task to remind)')
else:
    p = plan[0]
    task = db.get_task(p['task_id'])
    goal = db.get_goal(p['goal_slug'])
    print(reminder.format_reminder(p['date'], p['slot_start'], p['slot_end'], goal, task))
"
