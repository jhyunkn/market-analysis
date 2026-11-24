#!/usr/bin/env python3
"""Strategy lab: published, rules-only ways to decide WHEN to be invested and WHAT to hold, tested month by month.

Writes data/strategies.json. Standard library only. Run after scripts/build_data.py (it reads the price cache).

Method
- Every rule and setting comes from published research and was fixed before testing; nothing is tuned to this data,
  and every candidate is shown, including the ones that lost. Decisions use month-end closes only (adjusted for
  dividends and splits) and take effect for the next month. Out of the market means 3-month T-bills (FRED TB3MS).
  Costs: 0.10% of the value traded at each rebalance.
- Candidates
  1. Hold SPY: the benchmark.
  2. 60/40: 60% SPY, 40% TLT (20-year Treasuries), rebalanced monthly.
  3. Trend SPY (Faber 2007): SPY while its month-end close is above its 10-month average, else T-bills.
  4. Dual momentum (Antonacci 2014): if SPY's 12-month return beats T-bills, hold whichever of SPY or EFA (developed
     markets outside the US) did better over 12 months; otherwise TLT. (Antonacci uses aggregate bonds; TLT is the
     closest long-history bond fund here and is more rate-sensitive.)
  5. Sector momentum (Moskowitz and Grinblatt 1999): the 3 of the 9 original S&P sector funds with the best 12-month
     return excluding the latest month, equal weight.
  6. Sector momentum with trend: the same, but only while SPY is above its 10-month average; otherwise T-bills.
  7. Five-asset trend (Faber 2007, five asset classes): 20% each in SPY, EFA, EEM, TLT, GLD, each held only while above
     its own 10-month average, otherwise that 20% sits in T-bills.
- Each is scored over its whole history and separately over each half, so a rule that only worked in one era shows
  it. Survivorship: the ETFs here all still exist; sector and index funds are little affected.
- Live record: each month-end, every rule's holdings are logged and graded the following month, so the record keeps
  growing out of sample from 2026-11 on.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import os
import statistics
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "market-data"
NOW = dt.datetime.now(dt.timezone.utc)
COST = 0.001
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
ASSETS = sorted(set(SECTORS + ["SPY", "TLT", "EFA", "EEM", "GLD"]))
CASH = "T-bills"


def fred(series: str) -> dict[str, float]:
    req = urllib.request.Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers={"User-Agent": "curl/8.7.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                rows = r.read().decode().splitlines()[1:]
            return {d[:7]: float(v) for d, _, v in (x.partition(",") for x in rows) if v not in (".", "")}
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return {}


def monthly(sym: str) -> tuple[dict[str, float], str | None, dict]:
    """Completed month-end closes; the current month counts once its last weekday's close is in."""
    for tag in ("_long", ""):
        p = CACHE / f"{sym}{tag}.json"
        if p.exists():
            raw = {dt.date.fromisoformat(k): v for k, v in json.loads(p.read_text()).items()}
            break
    else:
        return {}, None, {}
    by: dict[str, tuple[dt.date, float]] = {}
    for d in sorted(raw):
        by[f"{d.year}-{d.month:02d}"] = (d, raw[d])
    last_day = max(raw)
    nxt = last_day + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    months = {k: v[1] for k, v in by.items()}
    partial = None
    if nxt.month == last_day.month:
        partial = f"{last_day.year}-{last_day.month:02d}"
    latest = {"date": last_day.isoformat(), "close": raw[last_day]}
    return months, partial, latest


def ret(px: dict, sym: str, m: str, back: int, months: list[str], skip: int = 0) -> float | None:
    i = months.index(m)
    if i - back < 0:
        return None
    a, b = months[i - back], months[i - skip]
    if a not in px[sym] or b not in px[sym]:
        return None
    return px[sym][b] / px[sym][a] - 1


def sma_above(px: dict, sym: str, m: str, months: list[str], n: int = 10) -> bool | None:
    i = months.index(m)
    window = months[i - n + 1:i + 1]
    if i - n + 1 < 0 or any(k not in px[sym] for k in window):
        return None
    return px[sym][m] > statistics.mean(px[sym][k] for k in window)


def bill_12m(tb: dict, m: str, months: list[str]) -> float:
    i = months.index(m)
    return sum(tb.get(k, 0) / 100 / 12 for k in months[max(0, i - 11):i + 1])


def rules(px: dict, tb: dict, months: list[str]) -> dict:
    """Each rule maps a decision month to target weights for the next month (None = not enough history yet)."""
    def hold_spy(m):
        return {"SPY": 1.0} if m in px["SPY"] else None

    def sixty_forty(m):
        return {"SPY": 0.6, "TLT": 0.4} if m in px["SPY"] and m in px["TLT"] else None

    def trend_spy(m):
        a = sma_above(px, "SPY", m, months)
        return None if a is None else ({"SPY": 1.0} if a else {CASH: 1.0})

    def dual(m):
        rs, re_, rt = ret(px, "SPY", m, 12, months), ret(px, "EFA", m, 12, months), ret(px, "TLT", m, 12, months)
        if None in (rs, re_, rt):
            return None
        if rs <= bill_12m(tb, m, months):
            return {"TLT": 1.0}
        return {"SPY": 1.0} if rs >= re_ else {"EFA": 1.0}

    def sector_mom(m, filtered=False):
        scores = [(ret(px, s, m, 12, months, skip=1), s) for s in SECTORS]
        if any(v is None for v, _ in scores):
            return None
        if filtered:
            a = sma_above(px, "SPY", m, months)
            if a is None:
                return None
            if not a:
                return {CASH: 1.0}
        top = [s for _, s in sorted(scores, reverse=True)[:3]]
        return {s: 1 / 3 for s in top}

    def five_trend(m):
        w = {}
        for s in ("SPY", "EFA", "EEM", "TLT", "GLD"):
            a = sma_above(px, s, m, months)
            if a is None:
                return None
            k = s if a else CASH
            w[k] = w.get(k, 0) + 0.2
        return w

    return {
        "hold_spy": ("Hold SPY", "Buy the S&P 500 and never trade. The benchmark every other rule has to beat.", hold_spy),
        "sixty_forty": ("60/40 stocks and bonds", "60% SPY and 40% long Treasuries (TLT), rebalanced each month.", sixty_forty),
        "trend_spy": ("Trend: S&P or T-bills", "SPY while its month-end close is above its 10-month average, otherwise T-bills (Faber 2007).", trend_spy),
        "dual_momentum": ("Dual momentum", "If US stocks beat T-bills over 12 months, hold the stronger of US (SPY) or developed markets outside the US (EFA); otherwise long Treasuries (TLT) (Antonacci 2014).", dual),
        "sector_momentum": ("Sector momentum", "The 3 strongest of the 9 original S&P sectors over the past 12 months (skipping the latest month), equal weight (Moskowitz and Grinblatt 1999).", lambda m: sector_mom(m)),
        "sector_momentum_trend": ("Sector momentum with trend", "The same 3 strongest sectors, but only while SPY is above its 10-month average; otherwise T-bills.", lambda m: sector_mom(m, True)),
        "five_asset_trend": ("Five-asset trend", "20% each in US stocks, developed and emerging markets, long Treasuries and gold, each held only while above its own 10-month average, otherwise T-bills (Faber 2007).", five_trend),
    }


def simulate(fn, px: dict, tb: dict, months: list[str]) -> dict | None:
    rets, dates, prev_w, turnovers, in_mkt = [], [], {}, [], []
    for i in range(len(months) - 1):
        m, nxt = months[i], months[i + 1]
        w = fn(m)
        if w is None:
            continue
        r = 0.0
        for s, wt in w.items():
            if s == CASH:
                r += wt * tb.get(nxt, 0) / 100 / 12
            else:
                if nxt not in px[s]:
                    r = None
                    break
                r += wt * (px[s][nxt] / px[s][m] - 1)
        if r is None:
            continue
        keys = set(w) | set(prev_w)
        to = sum(abs(w.get(k, 0) - prev_w.get(k, 0)) for k in keys) if prev_w else 1.0
        r -= COST * to
        # drift weights to month end so next month's turnover is measured from what is actually held
        grown = {s: wt * ((tb.get(nxt, 0) / 100 / 12 + 1) if s == CASH else px[s][nxt] / px[s][m]) for s, wt in w.items()}
        tot = sum(grown.values())
        prev_w = {s: v / tot for s, v in grown.items()}
        rets.append(r); dates.append(nxt); turnovers.append(to); in_mkt.append(1 - w.get(CASH, 0))
    if len(rets) < 36:
        return None
    return {"rets": rets, "dates": dates, "turnover": turnovers, "in_mkt": in_mkt}


def stats(rets: list[float], dates: list[str], tb: dict, in_mkt: list[float] | None = None) -> dict:
    eq, pk, dd, curve = 1.0, 1.0, 0.0, []
    for r, d in zip(rets, dates):
        eq *= 1 + r; pk = max(pk, eq); dd = min(dd, eq / pk - 1); curve.append((d, eq))
    yrs = len(rets) / 12
    ex = [r - tb.get(d, 0) / 100 / 12 for r, d in zip(rets, dates)]
    sd = statistics.pstdev(rets) * math.sqrt(12)
    years = {}
    for r, d in zip(rets, dates):
        years[d[:4]] = years.get(d[:4], 1.0) * (1 + r)
    full_years = {y: v for y, v in years.items() if sum(1 for d in dates if d[:4] == y) == 12}
    out = {"start": dates[0], "end": dates[-1], "years": round(yrs, 1), "cagr_pct": round((eq ** (1 / yrs) - 1) * 100, 1),
           "max_dd_pct": round(dd * 100), "vol_pct": round(sd * 100), "sharpe": round(statistics.mean(ex) * 12 / sd, 2) if sd else None,
           "worst_year_pct": round((min(full_years.values()) - 1) * 100) if full_years else None,
           "worst_year": min(full_years, key=full_years.get) if full_years else None, "growth_of_1": round(eq, 2)}
    if in_mkt:
        out["in_market_pct"] = round(statistics.mean(in_mkt) * 100)
    return out, curve


def main() -> int:
    tb = fred("TB3MS")
    if not tb:
        print("::warning::FRED T-bill series unavailable; strategies not rebuilt")
        return 1
    px, partial, latest = {}, None, {}
    for s in ASSETS:
        m, p, l = monthly(s)
        if not m:
            print(f"::warning::no price history for {s}")
            return 1
        px[s], latest[s] = m, l
        partial = partial or p
    months = sorted(set().union(*[set(v) for v in px.values()]) - ({partial} if partial else set()))
    # Ignore the unfinished month in decisions; the provisional read below shows where it stands now.
    for s in px:
        px[s] = {k: v for k, v in px[s].items() if k in months}
    tb_full = dict(tb)
    rs = rules(px, tb, months)
    sims = {k: simulate(fn, px, tb, months) for k, (_, _, fn) in rs.items()}
    common_start = max(v["dates"][0] for v in sims.values() if v)
    out_rows, curves = [], {}
    for key, (name, how, fn) in rs.items():
        sim = sims[key]
        if not sim:
            continue
        full, curve = stats(sim["rets"], sim["dates"], tb, sim["in_mkt"])
        idx = [i for i, d in enumerate(sim["dates"]) if d >= common_start]
        same, ccurve = stats([sim["rets"][i] for i in idx], [sim["dates"][i] for i in idx], tb, [sim["in_mkt"][i] for i in idx])
        half = len(idx) // 2
        first, _ = stats([sim["rets"][i] for i in idx[:half]], [sim["dates"][i] for i in idx[:half]], tb)
        second, _ = stats([sim["rets"][i] for i in idx[half:]], [sim["dates"][i] for i in idx[half:]], tb)
        # 3-year rolling windows on the common period: how often it beat holding SPY
        spy = sims["hold_spy"]
        spy_by = dict(zip(spy["dates"], spy["rets"]))
        mine = [(sim["dates"][i], sim["rets"][i]) for i in idx]
        wins = n = 0
        for j in range(0, len(mine) - 36 + 1, 3):
            win = mine[j:j + 36]
            a = math.prod(1 + r for _, r in win)
            b = math.prod(1 + spy_by[d] for d, _ in win)
            wins += a > b; n += 1
        last_m = months[-1]
        now_w = fn(last_m)
        prev_m = months[-2]
        prev_w = fn(prev_m)
        out_rows.append({"key": key, "name": name, "how": how, "full": full, "common": same, "first_half": first, "second_half": second,
                         "beat_spy_3y_pct": round(wins / n * 100) if n and key != "hold_spy" else None, "windows_3y": n,
                         "avg_turnover_pct": round(statistics.mean(sim["turnover"][1:]) * 100) if len(sim["turnover"]) > 1 else None,
                         "holdings": {k: round(v, 3) for k, v in (now_w or {}).items()}, "decided_at": last_m,
                         "changed": bool(prev_w) and set(k for k, v in (now_w or {}).items() if v > 0) != set(k for k, v in prev_w.items() if v > 0)})
        curves[key] = [[d, round(v, 4)] for d, v in ccurve]
    # Provisional read: what each rule would hold if the current month ended at the latest close.
    prov = None
    if partial:
        ppx = {s: {**px[s], partial: latest[s]["close"]} for s in px}
        pmonths = months + [partial]
        prs = rules(ppx, {**tb_full, partial: tb_full.get(partial, tb_full[max(tb_full)])}, pmonths)
        prov = {"month": partial, "asof": latest["SPY"]["date"],
                "holdings": {k: {s: round(w, 3) for s, w in (fn(partial) or {}).items()} for k, (_, _, fn) in prs.items()}}
    # Momentum table for the sector rule, at the decision month and provisionally now
    sector_table = []
    for s in SECTORS:
        sector_table.append({"symbol": s, "mom_12_1_pct": round(ret(px, s, months[-1], 12, months, skip=1) * 100, 1),
                             "above_10m": sma_above(px, s, months[-1], months)})
    sector_table.sort(key=lambda x: -x["mom_12_1_pct"])
    # Live record: log each rule's holdings per decision month; grade with the next month's actual return.
    path = DATA / "strategies.json"
    live = []
    if path.exists():
        try:
            live = json.loads(path.read_text())["rows"][0].get("live", [])
        except Exception:  # noqa: BLE001
            live = []
    for r in out_rows:
        if not any(x["key"] == r["key"] and x["m"] == r["decided_at"] for x in live):
            live.append({"key": r["key"], "m": r["decided_at"], "w": r["holdings"]})
    for x in live:
        if x.get("ret") is not None or x["m"] not in months:
            continue
        i = months.index(x["m"])
        if i + 1 >= len(months):
            continue
        nxt = months[i + 1]
        r = 0.0
        for s, w in x["w"].items():
            r += w * ((tb.get(nxt, 0) / 100 / 12) if s == CASH else (px[s][nxt] / px[s][x["m"]] - 1))
        x["ret"] = round(r * 100, 2)
    next_end = dt.date(int(months[-1][:4]) + (months[-1][5:] == "12"), int(months[-1][5:]) % 12 + 1, 1)
    me = (next_end.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    while me.weekday() >= 5:
        me -= dt.timedelta(days=1)
    payload = {"rows": [{"decided_at": months[-1], "next_decision": me.isoformat(), "common_start": common_start, "strategies": out_rows,
                         "curves": curves, "provisional": prov, "sectors": sector_table, "live": live,
                         "names": {s: n for s, n in (("SPY", "S&P 500"), ("TLT", "20+ year Treasuries"), ("EFA", "Developed markets ex-US"), ("EEM", "Emerging markets"), ("GLD", "Gold"),
                                                     ("XLB", "Materials"), ("XLE", "Energy"), ("XLF", "Financials"), ("XLI", "Industrials"), ("XLK", "Technology"), ("XLP", "Consumer staples"),
                                                     ("XLU", "Utilities"), ("XLV", "Health care"), ("XLY", "Consumer discretionary"), (CASH, "3-month Treasury bills"))}}],
               "meta": {"generated_at": NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "generator": "scripts/build_strategies.py",
                        "sources": ["Yahoo Finance adjusted prices (via the build cache)", "FRED TB3MS"], "method": __doc__.split("Method", 1)[1].strip()}}
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(tmp, path)
    for r in out_rows:
        c = r["common"]
        print(f"{r['name']:28s} {c['start']}..{c['end']} cagr {c['cagr_pct']:5.1f} dd {c['max_dd_pct']:4d} sharpe {c['sharpe']} "
              f"halves {r['first_half']['cagr_pct']}/{r['second_half']['cagr_pct']} beat3y {r['beat_spy_3y_pct']} now {r['holdings']}")
    print("provisional", prov)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
