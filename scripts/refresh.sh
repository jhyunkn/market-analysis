#!/bin/sh
# Rebuild and validate the self-contained market-analysis datasets.
# Usage: scripts/refresh.sh [--publish]
set -eu

PUBLISH=0
if [ "${1:-}" = "--publish" ]; then
  PUBLISH=1
  shift
fi
[ "$#" -eq 0 ] || { echo "usage: scripts/refresh.sh [--publish]" >&2; exit 2; }

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON="${PYTHON:-python3}"
cd "$ROOT"

"$PYTHON" scripts/refresh_analysts.py || echo "analyst refresh failed; keeping the previous targets" >&2
"$PYTHON" scripts/build_data.py
"$PYTHON" scripts/validate_data.py
"$PYTHON" scripts/macro_regime.py || echo "macro regime refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/market_history.py || echo "market history refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_flows.py || echo "flows refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_cross_asset.py || echo "cross-asset refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_breadth.py || echo "S&P 500 breadth refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_live.py || echo "futures/after-hours refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_forward.py || echo "forward view refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/build_strategies.py || echo "strategy lab refresh failed; keeping the previous file" >&2
"$PYTHON" scripts/validate_extra.py || echo "some optional datasets failed validation and were restored" >&2
PREVIOUS=$(mktemp)
cp data/drivers.json "$PREVIOUS"
if ! { "$PYTHON" scripts/build_drivers.py && "$PYTHON" scripts/validate_drivers.py; }; then
  cp "$PREVIOUS" data/drivers.json
  echo "drivers refresh failed; keeping the previous news and macro data" >&2
fi
rm -f "$PREVIOUS"
"$PYTHON" scripts/record_narrative.py || echo "narrative recording failed" >&2

if [ "$PUBLISH" = 1 ]; then
  git add config/analyst_targets.json data/outlook_summary.json data/outlook_cone.json data/outlook_history.json data/news.json data/market.json data/drivers.json data/macro_regime.json data/market_history.json data/flows.json data/cross_asset.json data/live.json data/breadth.json config/sp500.json data/narrative.json data/forward.json data/forecast_log.json data/strategies.json data/decisions.ics data/ratings.json data/ratings_log.json data/ratings_backtest.json
  if git diff --cached --quiet; then
    echo "no data change"
  else
    git commit -m "Refresh market analysis data"
    git pull --rebase origin main
    git push origin main
    echo "published $(git rev-parse --short HEAD)"
  fi
fi
