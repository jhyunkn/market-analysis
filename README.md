# Market analysis

Search a stock and get a strategy read on it: how it is doing, what the evidence says, an entry plan with prices, how it held up in past crashes, and how big a position matches the risk of an S&P position. Covers about 150 US stocks and ETFs. Open one directly with a link like `#NVDA`.

**Live page:** https://jhyunkn.github.io/market-analysis/

## What it shows

- **Search:** type a ticker; suggestions appear as you type.
- **How it is doing:** 1-day, 1-week, 1-month, 3-month, year-to-date and 1-year returns.
- **Bottom map:** daily candles with volume, a one-year volume profile, and bottom zones. Each zone clusters candidate levels from separate kinds of evidence (prior swing lows and the 52-week low, heavy-volume prices, the 200-day average, the stock's typical drop after past S&P sell-offs, and macro base and stress cases) and is scored by how many kinds agree. Macro cases come from how much further the S&P fell on past sell-off days like today, split by corporate-credit state (FRED Baa spread), scaled by the stock's five-year beta. Planned entries are marked on the chart, with the typical time-to-low window.
- **Entry plan:** 15% starter on a reversal day, 20% on follow-through, 25% limit at the top of the main bottom zone, 15% limit at the deeper zone, 25% on confirmation, and a stop one normal week below the lowest zone it buys.
- **How much:** risk-matched size versus SPY, from how far the stock fell within 3 months of past sell-off starts compared with SPY; and a calculator that turns a dollar amount and shares already owned into share counts per step. Inputs stay in the browser.
- **Crash replay:** next-year return and worst 3-month drop after each past S&P sell-off like today (VIX 30+, S&P 8–20% off its high) that the stock traded through, against SPY, from full price history since 1985 or listing.

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
