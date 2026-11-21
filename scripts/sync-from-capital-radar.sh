#!/bin/sh
# Copy the latest market-only outlook data from a Capital Radar checkout into data/.
# Usage: scripts/sync-from-capital-radar.sh [path-to-openclaw-capital-radar]
set -e
CR="${1:-../openclaw-capital-radar}"
for f in outlook_summary outlook_cone outlook_history; do
  cp "$CR/outputs/risk-lab/$f.json" "data/$f.json"
done
echo "synced from $CR/outputs/risk-lab"
