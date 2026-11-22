# Market analysis

Search a stock and get a strategy read on it: how it is doing, what the evidence says, an entry plan with prices, how it held up in past crashes, and how big a position matches the risk of an S&P position. Covers about 150 US stocks and ETFs. Open one directly with a link like `#NVDA`.

**Live page:** https://jhyunkn.github.io/market-analysis/

## What it shows

- **What is moving the market:** the current market regime, the day's important news ranked and sorted by theme (geopolitics, Fed, AI and chips, oil, trade, inflation, credit), and which stocks tend to move with each driver.
- **Macro:** rates, inflation, jobs, growth, credit and liquidity from FRED, with each reading's date and 5-year range.
- **What moved each stock:** the last day and 5 days split into market, AI and chips beyond the market, rates, oil, dollar, gold, bitcoin and an unexplained or company-specific remainder, plus related news. This shows what moved together, not proof of cause.
- **Market today:** S&P 500, Nasdaq 100, Russell 2000, Dow and VIX; breadth (share of stocks above their 200-day); a plain-language read of where the market stands, what is moving it, where the S&P may bottom (zones in index points) and what history says; sectors ranked by the past month; the week's biggest movers; best setups ranked by reward to risk; market headlines.
- **Per stock:** headlines that name the company (listicles dropped; news desks and company releases ranked above opinion), the next earnings date (or an estimate from the last report) and the last result versus forecast.
- **Track record:** the bottom map shows how often past main zones held when price reached them, against a random zone of the same width, from a walk-forward test over every stock since 2017.

- **Search:** type a ticker; suggestions appear as you type.
- **How it is doing:** 1-day, 1-week, 1-month, 3-month, year-to-date and 1-year returns.
- **Macro regime:** 23 FRED indicators judged against fixed thresholds; whether the economy and the price of money help or hurt stocks; rate and inflation charts; and the S&P 500's next-year returns after past months with each macro condition since 1950 (`scripts/macro_regime.py`, `data/macro_regime.json`).
- **Momentum:** every stock's past-year trend against this month's strength versus the S&P (Leader, Holding up, Turning up, Breaking down, Weak), and which sectors beat the S&P in the 3 months after past sell-offs.
- **Factor monitor:** standard factors (12-1 momentum, 1-month reversal, low volatility, nearness to the 52-week high, trend) rebalanced quarterly since 2018 across the covered stocks: top-minus-bottom spread, hit rate and t-statistic, with today's top names.
- **Where the checks line up now:** stocks ranked by how many checks agree, shown with a walk-forward track record of those checks (no separation so far), so the ranking reads as a description, not a forecast.
- **Breadth and volatility:** share of stocks above their 50- and 200-day averages over three years; VIX against 3-month VIX and against realized volatility; earnings in the next four weeks.
- **Bottom map:** daily candles with volume, a one-year volume profile, and bottom zones. Each zone clusters candidate levels from separate kinds of evidence (prior swing lows and the 52-week low, heavy-volume prices, the 200-day average, the stock's typical drop after past S&P sell-offs, and macro base and stress cases) and is scored by how many kinds agree. Macro cases come from how much further the S&P fell on past sell-off days like today, split by corporate-credit state (FRED Baa spread), scaled by the stock's five-year beta. Planned entries are marked on the chart, with the typical time-to-low window.
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
- `scripts/build_data.py`: self-contained market-only generator. Also writes `data/market.json` (overview) and `data/news.json` (Yahoo Finance RSS headlines; next earnings date from Nasdaq's public earnings calendar). News and earnings are best effort and never block a build.
- `config/universe.json`: editable ticker universe and dashboard groups.
- `config/analyst_targets.json`: explicit, dated analyst-consensus snapshot. It stops affecting action scores after 45 days unless refreshed.
- `scripts/validate_drivers.py`: freshness and coverage checks for news, drivers and macro. A failed check keeps the previous data and turns the refresh run red.
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
