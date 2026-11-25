# Market analysis

A market and single-stock analysis page: rule-based long-term and short-term ratings for about 135 large US stocks, a strategy lab that tests published rules against simply holding the S&P 500, then what is moving the market, the macro backdrop, breadth and volatility, momentum and factors, and for any of about 150 US stocks and ETFs, its trend, likely price range, bottom zones and crash record. Open a stock directly with a link like `#NVDA`.

**Live page:** https://jhyunkn.github.io/market-analysis/

Analysis only, not investment advice. The page states where its own tests show no predictive edge.

## Sections (top to bottom)

- **Stock ratings:** every stock rated for a long-term investor (8 business checks: sales growth, profits, margins, debt, gross profitability, PEG, estimates, analysts; 7 for financials and property trusts) and a short-term investor (6 checks: trend, momentum, strength vs SPY, estimate revisions, last earnings beat, no report in 10 days). Strong = every check passes. Cyclical companies are flagged. A weekly live log grades each Strong list against SPY after 1 and 3 months, and a point-in-time history test from SEC filings (`scripts/backtest_ratings.py`) measures the business checks since 2017. Rules applied the same way to every stock, not personal advice.
- **Looking ahead:** option-implied ranges and earnings moves, the 10-month trend rule, recession odds and GDPNow, with a live scorecard.
- **Strategy lab:** seventeen published rules, including leveraged trend (Gayed and Bilello 2016, both on Faber's monthly signal and as published: a daily 200-day line, for 2x and 3x S&P funds and TQQQ), volatility-managed exposure (Moreira and Muir 2017), holding QQQ or TQQQ, and Laoer's TQQQ infinite buying method. Each is scored against holding SPY over its whole record, each half, since publication and (S&P-only rules) 1951-1992; daily rules also show how 150- and 250-day lines did. Leveraged funds are rebuilt daily from their index and checked against real UPRO and TQQQ. Cards show what each rule holds now and what decides its next switch; the table adds taxes and a live out-of-sample record.

- **Since the close (strip at the top):** S&P 500, Nasdaq, Dow and Russell futures and after-hours or pre-market prices since the last official close, updated with each refresh. Every other section uses official closing prices.

Each comparison is one chart on one shared scale (ranked bars, a heatmap, multi-line charts, a 0–100 percentile chart); the underlying numbers sit under "Show the numbers".


- **Desk summary:** eight lines generated from the data on each refresh (market state, stress, breadth, macro, history, rotation, factors, caveats), each linking to its section.
- **What is moving the market:** written for anyone: a headline and four plain sentences (what happened, how people feel, where the money went, biggest winners and losers), a fear dial, a scoreboard of this week's moves for stocks, tech, chips, bond rates, oil, gold, bitcoin and the dollar (each labeled normal, bigger than usual or very unusual for that market), one chart of who went up and down (day, week or month), what it costs to borrow (Treasury rates and the 30-year mortgage, now vs a year ago), and the next big dates (Fed decision, major earnings, latest official releases). Expert detail (market monitor heatmap, fear by market, yield curve, all Fed and BEA releases) is folded underneath; then news, which stocks move with chips, rates or oil, and a compare chart.
- **Macro:** is the economy helping or hurting stocks? Verdict tiles (the economy, the cost of money, where the cycle is), a plain-language bottom line, and one scorecard of 23 FRED signals: latest reading, where it sits against its full history, which way it moved over 3 months, and whether it is good or bad for stocks (with the threshold behind each call). Then, for each condition, how much better or worse than a normal year the S&P's next 12 months were since 1950, and rate and inflation charts.
- **Today's market against history:** ten stress gauges (VIX, VIX term structure, S&P drawdown, past month, vs 200-day, Nasdaq and bitcoin drawdowns, credit spread, yield curve, 10-year yield), each ranked against its full history with what the S&P did over the next 3 and 12 months after similar readings; and a table of setups like today (VIX 30+, S&P 8–20% off its high, split by credit stress) with episode counts, medians, 1-in-10 outcomes and worst years.
- **Breadth (all S&P 500 stocks):** a takeaway, then how many of the ~500 stocks are in an uptrend, rose vs fell this week, hit new 52-week highs vs lows, and how the average stock (equal-weight) compares with the index; breadth history and breadth by sector. Then volatility, share of stocks above their 50- and 200-day averages over three years; VIX against 3-month VIX and realized volatility; earnings in the next four weeks; S&P bottom zones; sectors ranked by the past month; the week's biggest movers.
- **Momentum and factors:** every stock's past-year trend against this month's strength versus the S&P (Leader, Holding up, Turning up, Breaking down, Weak); which sectors beat the S&P in the 3 months after past sell-offs; a factor monitor (12-1 momentum, 1-month reversal, low volatility, nearness to the 52-week high, trend) rebalanced quarterly since 2018, with spread, hit rate and t-statistic. Factor spreads are not beta-adjusted.
- **Insiders and big money:** insider open-market buying and discretionary selling over 3 months (pre-planned 10b5-1 sales separate), net institutional buying last quarter from 13F filings, how many notable hedge funds added or cut each stock, and the most-shorted stocks. Per stock: insider trades, fund ownership and named fund moves, and a short-interest chart.
- **Where the checks line up now:** stocks ranked by how many checks agree, shown next to a walk-forward test of those checks (no separation so far), so the ranking reads as a description, not a forecast.
- **Stock analysis:** 1-day to 1-year returns; six months of prices with the 50- and 200-day averages, then a twelve-month fan of likely (2 in 3) and possible (9 in 10) outcomes, the path if the S&P repeats past sell-offs, analyst targets and the trend-confirmation level; next earnings; what moved the stock (market, AI and chips, rates, oil, dollar, gold, bitcoin and a remainder; co-movement, not proof of cause).
- **Chart tools (on the bottom-map candle chart):** toggles for events (E earnings with EPS surprise, F Fed rate decisions including upcoming ones, I insider open-market buying, G gaps of 3% or more), trendlines (swing lows or highs at least 15 sessions apart with no break in between, marked broken once price is through them), Fibonacci retracements of the 6-month swing, and RSI (14). They never feed the action labels. Tested: price held the nearest Fibonacci level 23% of the times it reached it, the same as a random level.
- **Bottom map:** daily candles with volume, a one-year volume profile and scored bottom zones. Each zone clusters levels from separate kinds of evidence (swing lows and the 52-week low, heavy-volume prices, the 200-day, the stock's typical drop after past S&P sell-offs, macro base and stress cases scaled by five-year beta). Track record: main zones held 34% of the time when reached, against 33% for a random zone of the same width.
- **Crash replay:** next-year return and worst 3-month drop after each past S&P sell-off like today (VIX 30+, S&P 8–20% off its high), against SPY, from full price history.
- **Action board and All stocks:** every stock grouped by action state, and the 3-month likely range against the average analyst target with the full numbers table.
- **What this is based on:** each input and its weaknesses.

## Data sources

- Yahoo Finance: daily prices and OHLC, headlines, futures and extended-hours quotes. Nasdaq's price history is the backup for daily closes when Yahoo fails.
- Nasdaq company and analyst data: financial statements, earnings surprises, estimates and revisions, PEG (ratings).
- SEC EDGAR XBRL company facts: backup statements and the point-in-time ratings test. SEC requires a contact email; it is read from the `SEC_CONTACT` repository secret and never stored in the repo.
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
- `scripts/record_narrative.py`: appends each day's news-theme counts to `data/narrative.json` (not shown yet; builds a narrative timeline over time).
- `scripts/build_breadth.py`: `breadth.json` (S&P 500 breadth, sector breadth, equal-weight vs index, momentum counts, factor tests); members and sectors from Wikipedia into `config/sp500.json`, weekly.
- `scripts/build_live.py`: `live.json` (index futures and each stock's pre-market or after-hours price since the last official close).
- `scripts/build_cross_asset.py`: `cross_asset.json` (yield curve, fear gauges, FOMC calendar, official releases).
- `scripts/build_flows.py`: `flows.json` (insider trades, 13F ownership changes, notable funds, short interest), daily.
- `scripts/market_history.py`: `market_history.json` (gauges and setups against history).
- `scripts/build_drivers.py`: `drivers.json` (news ranking, drivers, macro table, what moved each stock).
- `scripts/validate_data.py`, `scripts/validate_drivers.py`: fail on stale, incomplete or inconsistent data. A failed drivers check keeps the previous file.
- `scripts/validate_extra.py`: checks the macro, market-history and flows datasets; any that fail are restored to the last published version and flagged as a warning on the run.
- `scripts/refresh.sh [--publish]`: runs every builder and validator locally; `--publish` commits and pushes the data.
- `scripts/build_forward.py`, `scripts/build_strategies.py`: `forward.json` (looking ahead) and `strategies.json` (strategy lab, `decisions.ics`).
- `scripts/build_ratings.py`: `ratings.json` and `ratings_log.json`; `scripts/sec_facts.py` and `scripts/backtest_ratings.py`: SEC filings and the weekly history test (`ratings_backtest.json`).
- `scripts/health_check.py`: is every dataset on the latest NYSE session (holiday-aware); writes `health.json`.
- `scripts/page_test.mjs`: loads the live page in headless Chromium (desktop and phone width) and fails on errors, empty sections, bad values or overflow.
- `.github/workflows/refresh-data.yml` and `live-quotes.yml`: the full build and the quote refresh. GitHub's own cron skips most runs, so three Claude cloud routines (weekdays 9:45 AM, 12:30 PM and 4:30 PM New York time) push a timestamp to `triggers/` to start them.
- `.github/workflows/health.yml`: after every build and twice a day, runs the health check and the page test and opens (or closes) one GitHub issue labelled `data-health`. Run it by hand with `drill: true` to test the alert.
- `config/leveraged.json`: each daily-reset leveraged fund's underlying (TQQQ on QQQ, UPRO on SPY, TECL on XLK, SOXL on SOXX), leverage, inception and assumed yearly fee drag. Their zones, confirmation levels and history paths are built on the underlying.
- `scripts/calibrate_bands.py`: reproducible test of the price bands (non-overlapping windows on a shared calendar, window-clustered bootstrap intervals, terminal coverage and intraperiod touches, high-volatility windows separately, leveraged and ordinary ETFs separately). Writes `data/calibration.json`.
- `config/universe.json`: the curated stocks for per-stock analysis, grouped by sector (edit names and groups here).
- `config/sp500.json`: S&P 500 members and sectors (refreshed weekly) for market-wide statistics.

## Update the data

```bash
scripts/refresh.sh
```

Cached Yahoo responses live in `.cache/market-data`. To preview locally: `python3 -m http.server` in this folder, then open http://localhost:8000.

## Known limits

- Per-stock analysis covers about 160 curated names; market-wide breadth, momentum counts and factor tests use all S&P 500 members. Both use today's members for history, so survivorship bias applies.
- Walk-forward tests of the bottom zones, the check ranking and the macro regimes show no reliable edge; the page presents them as description.
- Nasdaq's analyst counts are smaller than some other providers'; a few names have thin coverage.
- The short-term rating and the PEG, estimate and analyst checks have no free history, so only the live log tests them.
