# Market analysis

A price outlook for about 150 US stocks and ETFs: where each has been, where it could go over the next year, and what would confirm or invalidate the call.

**Live page:** https://jhyunkn.github.io/market-analysis/

## What it shows

- **Action board:** every stock grouped by action state (Probe, Watch, Watch: odds good, trend not yet, Avoid for now), filterable by group.
- **Per-stock chart:** six months of prices with the 50- and 200-day averages, then a twelve-month fan of likely (2 in 3) and possible (9 in 10) outcomes, the path if the S&P repeats past sell-offs, analyst targets, and the confirm and invalidate levels.
- **All stocks:** 3-month likely range against the average analyst target, plus the full numbers table.

## What it is based on

1. Daily closes from Yahoo Finance since 2017.
2. Ranges from each stock's own volatility (today's level fading toward its 1-year average), with no up or down drift.
3. The median S&P 500 path after past sell-offs like today (VIX 30+ with the S&P 8–20% below its high, since 1990), scaled by each stock's trailing five-year beta where available.
4. Each stock's record after past setups like today (same distance below its 52-week high, same side of its 200-day), compared with its normal year; counted only with 4+ episodes.
5. Robinhood analyst consensus targets.
6. Four pre-written rules turn these into an action state. Not a backtested strategy and not investment advice.

The page's "What this is based on" section lists each input's weaknesses.

## Files

- `index.html`: the whole page (no front-end build step). Colors are CSS variables at the top; d3 7.9.0 loads from jsDelivr.
- `scripts/build_data.py`: self-contained market-only generator.
- `config/universe.json`: editable ticker universe and dashboard groups.
- `config/analyst_targets.json`: explicit, dated analyst-consensus snapshot. It stops affecting action scores after 45 days unless refreshed.
- `scripts/validate_data.py`: fails if datasets are stale, incomplete, inconsistent, or use an unexpected beta window.
- `data/outlook_summary.json`, `data/outlook_cone.json`, `data/outlook_history.json`: generated dashboard datasets with structured build metadata.
- `.github/workflows/refresh-data.yml`: runs the generator and validator every four hours, then commits changed datasets.

## Update the data

```bash
python scripts/build_data.py
python scripts/validate_data.py
```

The generator uses only Python's standard library. Cached Yahoo responses live in `.cache/market-data` and are reused for three hours. New listings without five years of history use their full available post-IPO period and say so in `beta_window`. The S&P sell-off analog affects action scores only while VIX and drawdown conditions match the stated regime; outside it, the path remains visible as an unscored stress scenario.

To preview locally: `python -m http.server` in this folder, then open http://localhost:8000.
