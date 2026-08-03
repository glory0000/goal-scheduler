#!/usr/bin/env bash
# Kill all pending cc-connect timers to simulate session crash.
# Used to test the daily fallback cron rebuild.

set -e
echo "Listing pending timers..."
cc-connect timer list

echo ""
echo "Deleting all pending timers..."
# cc-connect returns short IDs (e.g., 4603c91a) prefixed with emoji + spaces.
# Match hex IDs after the clock emoji.
IDS=$(cc-connect timer list 2>/dev/null | grep -oE '[a-f0-9]{6,}' || true)
for id in $IDS; do
  echo "Deleting $id..."
  cc-connect timer del "$id" || true
done

echo ""
echo "Done. Verify with: cc-connect timer list"
