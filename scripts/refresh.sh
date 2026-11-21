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

"$PYTHON" scripts/build_data.py
"$PYTHON" scripts/validate_data.py

if [ "$PUBLISH" = 1 ]; then
  git add data/outlook_summary.json data/outlook_cone.json data/outlook_history.json
  if git diff --cached --quiet; then
    echo "no data change"
  else
    git commit -m "Refresh market analysis data"
    git pull --rebase origin main
    git push origin main
    echo "published $(git rev-parse --short HEAD)"
  fi
fi
