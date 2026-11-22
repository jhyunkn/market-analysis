# Market analysis

A market and single-stock analysis page: what is moving the market, the macro backdrop, breadth and volatility, momentum and factors, and for any of about 150 US stocks and ETFs, its trend, likely price range, bottom zones and crash record. Open a stock directly with a link like `#NVDA`.

**Live page:** https://jhyunkn.github.io/market-analysis/

Analysis only, not investment advice. The page states where its own tests show no predictive edge.

## Sections (top to bottom)

Each comparison is one chart on one shared scale (ranked bars, a heatmap, multi-line charts, a 0–100 percentile chart); the underlying numbers sit under "Show the numbers".


- **Desk summary:** eight lines generated from the data on each refresh (market state, stress, breadth, macro, history, rotation, factors, caveats), each linking to its section.
- **What is moving the market:** the market regime, the day's important news ranked and grouped by theme, and which stocks tend to move with each driver.
- **Macro:** is the economy helping or hurting stocks? Verdict tiles (the economy, the cost of money, where the cycle is), a plain-language bottom line, and one scorecard of 23 FRED signals: latest reading, where it sits against its full history, which way it moved over 3 months, and whether it is good or bad for stocks (with the threshold behind each call). Then how the S&P did in the 12 months after past months with each condition since 1950, and rate and inflation charts.
- **Today's market against history:** ten stress gauges (VIX, VIX term structure, S&P drawdown, past month, vs 200-day, Nasdaq and bitcoin drawdowns, credit spread, yield curve, 10-year yield), each ranked against its full history with what the S&P did over the next 3 and 12 months after similar readings; and a table of setups like today (VIX 30+, S&P 8–20% off its high, split by credit stress) with episode counts, medians, 1-in-10 outcomes and worst years.
- **Breadth, volatility, bottoms and sectors:** share of stocks above their 50- and 200-day averages over three years; VIX against 3-month VIX and realized volatility; earnings in the next four weeks; S&P bottom zones; sectors ranked by the past month; the week's biggest movers.
- **Momentum and factors:** every stock's past-year trend against this month's strength versus the S&P (Leader, Holding up, Turning up, Breaking down, Weak); which sectors beat the S&P in the 3 months after past sell-offs; a factor monitor (12-1 momentum, 1-month reversal, low volatility, nearness to the 52-week high, trend) rebalanced quarterly since 2018, with spread, hit rate and t-statistic. Factor spreads are not beta-adjusted.
- **Insiders and big money:** insider open-market buying and discretionary selling over 3 months (pre-planned 10b5-1 sales separate), net institutional buying last quarter from 13F filings, how many notable hedge funds added or cut each stock, and the most-shorted stocks. Per stock: insider trades, fund ownership and named fund moves, and a short-interest chart.
- **Where the checks line up now:** stocks ranked by how many checks agree, shown next to a walk-forward test of those checks (no separation so far), so the ranking reads as a description, not a forecast.
- **Stock analysis:** 1-day to 1-year returns; six months of prices with the 50- and 200-day averages, then a twelve-month fan of likely (2 in 3) and possible (9 in 10) outcomes, the path if the S&P repeats past sell-offs, analyst targets and the trend-confirmation level; next earnings; what moved the stock (market, AI and chips, rates, oil, dollar, gold, bitcoin and a remainder; co-movement, not proof of cause).
- **Bottom map:** daily candles with volume, a one-year volume profile and scored bottom zones. Each zone clusters levels from separate kinds of evidence (swing lows and the 52-week low, heavy-volume prices, the 200-day, the stock's typical drop after past S&P sell-offs, macro base and stress cases scaled by five-year beta). Track record: main zones held 34% of the time when reached, against 33% for a random zone of the same width.
- **Crash replay:** next-year return and worst 3-month drop after each past S&P sell-off like today (VIX 30+, S&P 8–20% off its high), against SPY, from full price history.
- **Action board and All stocks:** every stock grouped by action state, and the 3-month likely range against the average analyst target with the full numbers table.
- **What this is based on:** each input and its weaknesses.

## Data sources

- Yahoo Finance: daily prices and OHLC, headlines.
- FRED: macro series (public `fredgraph.csv`).
- Nasdaq public earnings calendar.
- Nasdaq company pages for insider trades (SEC Form 4), institutional holdings (SEC 13F) and short interest (FINRA).
- Nasdaq public analyst price targets, refreshed on every build by `scripts/refresh_analysts.py` into `config/analyst_targets.json`. Each stock has its own `as_of` date; one that stops refreshing keeps its last value and is marked stale (not scored) after 45 days.

The "Data sources and freshness" panel at the top of the page shows the age of each source.

## Files

- `index.html`: the whole page (no build step). Colors are CSS variables at the top; d3 7.9.0 loads from jsDelivr.
- `drivers.js`, `drivers.css`: the market drivers, news and macro sections.
- `scripts/refresh_analysts.py`: analyst consensus from Nasdaq; keeps the previous file if fewer than half the names return.
- `scripts/build_data.py`: stock datasets (`outlook_summary`, `outlook_cone`, `outlook_history`), `market.json` and `news.json`. Standard library only.
- `scripts/macro_regime.py`: `macro_regime.json`.
- `scripts/build_flows.py`: `flows.json` (insider trades, 13F ownership changes, notable funds, short interest), daily.
- `scripts/market_history.py`: `market_history.json` (gauges and setups against history).
- `scripts/build_drivers.py`: `drivers.json` (news ranking, drivers, macro table, what moved each stock).
- `scripts/validate_data.py`, `scripts/validate_drivers.py`: fail on stale, incomplete or inconsistent data. A failed drivers check keeps the previous file.
- `scripts/validate_extra.py`: checks the macro, market-history and flows datasets; any that fail are restored to the last published version and flagged as a warning on the run.
- `scripts/refresh.sh [--publish]`: runs every builder and validator locally; `--publish` commits and pushes the data.
- `.github/workflows/refresh-data.yml`: the same refresh every four hours on GitHub.
- `config/universe.json`: the ticker universe and groups.

## Update the data

```bash
scripts/refresh.sh
```

Cached Yahoo responses live in `.cache/market-data`. To preview locally: `python3 -m http.server` in this folder, then open http://localhost:8000.

## Known limits

- The universe is about 150 large US names with data since 2017, so survivorship bias applies.
- Walk-forward tests of the bottom zones, the check ranking and the macro regimes show no reliable edge; the page presents them as description.
- Nasdaq's analyst counts are smaller than some other providers', and a few names (for example BRK-B) have no Nasdaq coverage, so they will age out.
