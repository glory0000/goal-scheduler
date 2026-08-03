#!/usr/bin/env bash
# Kill all pending cc-connect timers to simulate session crash.
# Used to test the daily fallback cron rebuild.

set -e
echo "Listing pending timers..."
cc-connect timer list

echo ""
echo "Deleting all pending timers..."
IDS=$(cc-connect timer list 2>/dev/null | grep -oE 'timer_[a-zA-Z0-9]+' || true)
for id in $IDS; do
  echo "Deleting $id..."
  cc-connect timer del "$id" || true
done

echo ""
echo "Done. Verify with: cc-connect timer list"
