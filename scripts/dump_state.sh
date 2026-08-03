#!/usr/bin/env bash
# Print current DB + file state for debugging.

set -e
cd "$(dirname "$0")/.."

echo "=== Goals ==="
sqlite3 data/todos.db "SELECT slug, name, status, total_tasks, completed_tasks FROM goals;"

echo ""
echo "=== Tasks ==="
sqlite3 -header -column data/todos.db "SELECT id, goal_slug, sequence, status FROM tasks ORDER BY goal_slug, sequence;"

echo ""
echo "=== Settings ==="
sqlite3 data/todos.db "SELECT key, value FROM settings;"

echo ""
echo "=== Pending cc-connect timers ==="
cc-connect timer list 2>&1 || echo "(cc-connect unavailable)"

echo ""
echo "=== Active cc-connect crons ==="
cc-connect cron list 2>&1 || echo "(cc-connect unavailable)"
