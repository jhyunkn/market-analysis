#!/bin/sh
# Rebuild the outlook data from a Capital Radar checkout, validate it, and update data/.
# Usage: scripts/refresh.sh [--publish] [path-to-openclaw-capital-radar]
#   --publish  commit and push when the data changed
set -eu
PUBLISH=0
if [ "${1:-}" = "--publish" ]; then PUBLISH=1; shift; fi
MA=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
CR="${1:-$HOME/openclaw-capital-radar}"
ENG="$CR/risk-lab/engines"
PY="$CR/risk-lab/.venv/bin/python"
[ -f "$ENG/price_outlook.py" ] || { echo "refresh failed: no risk lab in $CR (check out feat/risk-lab)" >&2; exit 1; }
[ -x "$PY" ] || { echo "refresh failed: missing $PY" >&2; exit 1; }

LOCK="${TMPDIR:-/tmp}/market-analysis-refresh.lock"
mkdir "$LOCK" 2>/dev/null || { echo "refresh failed: another refresh is running ($LOCK)" >&2; exit 1; }
OUT=$(mktemp -d "${TMPDIR:-/tmp}/market-analysis-data.XXXXXX")
trap 'rm -rf "$OUT"; rmdir "$LOCK"' EXIT

if [ "$PUBLISH" = 1 ]; then
  [ -z "$(git -C "$MA" status --porcelain -- data)" ] || { echo "refresh failed: data/ has uncommitted changes" >&2; exit 1; }
  git -C "$MA" pull -q --ff-only
fi

RISK_LAB_OUT_PUBLIC="$OUT" "$PY" "$ENG/price_outlook.py" "$CR/risk-lab/out-private" >/dev/null
RISK_LAB_OUT_PUBLIC="$OUT" "$PY" "$ENG/make_public.py" >/dev/null
"$PY" -I "$MA/scripts/validate_data.py" "$OUT"

if "$PY" -I "$MA/scripts/validate_data.py" --same "$OUT" "$MA/data"; then
  echo "no data change"
  exit 0
fi
for f in outlook_summary outlook_cone outlook_history; do cp "$OUT/$f.json" "$MA/data/$f.json"; done
echo "data updated"
if [ "$PUBLISH" = 1 ]; then
  git -C "$MA" add data
  git -C "$MA" commit -q -m "Refresh outlook data"
  git -C "$MA" push -q origin main
  echo "published $(git -C "$MA" rev-parse --short HEAD)"
fi
