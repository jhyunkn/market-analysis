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
- Taxable-account view: the same rule with FIFO tax lots, gains taxed when realized (under 12 months short-term) at
  illustrative federal rates of 32% short-term and 15% long-term, losses offset and carried forward, tax paid from the
  account each year end. Dividends and interest are left out for every rule, so it isolates the tax caused by selling.
- New money: every month-end start since 1993, $1 into SPY all at once, spread over 6 or 12 months, held until the
  trend rule is in, or held until a 10% drop from the highest month-end close (12-month limit); value 24 months later,
  with cash waiting in T-bills. Also shown for starts that came after a 5%+ pullback.
- Plan checks: for each rule, how far the latest close is from changing it at the coming month-end. A subscribable
  calendar (data/decisions.ics) lists the decision dates (last NYSE trading day of each month) with a heads-up.
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


# When each rule was published: the record after that date was out of sample for its authors.
PUBLISHED = {"trend_spy": ("2007-01", "Faber, 2007"), "five_asset_trend": ("2007-01", "Faber, 2007"),
             "dual_momentum": ("2015-01", "Antonacci, 2014"), "sector_momentum": ("2000-01", "Moskowitz and Grinblatt, 1999"),
             "sector_momentum_trend": ("2007-01", "the trend filter, Faber, 2007")}
ST_RATE, LT_RATE = 0.32, 0.15  # illustrative federal rates for the tax view; the page says so


def _net_tax(a: float, b: float, carry: float, sr: float, lr: float) -> tuple[float, float]:
    """Tax on a year's short-term (a) and long-term (b) gains after netting and any loss carried forward."""
    if carry < 0:
        use = max(carry, -max(a, 0)); a += use; carry -= use
        use = max(carry, -max(b, 0)); b += use; carry -= use
    if a < 0 < b:
        b += a; a = 0
    elif b < 0 < a:
        a += b; b = 0
    if a + b < 0 or (a <= 0 and b <= 0):
        return 0.0, carry + min(a, 0) + min(b, 0)
    return sr * max(a, 0) + lr * max(b, 0), carry


TAX_FED = [(12, 0), (22, 15), (24, 15), (32, 15), (35, 20), (37, 20)]  # federal ordinary / long-term rate pairs
TAX_STATE = [0, 5, 10]


def tax_sim(fn, px: dict, tb: dict, months: list[str], start: str, sr: float = None, lr: float = None) -> dict | None:
    """Same rule in a taxable account: FIFO lots, gains taxed in the year they are realized (held under 12 months =
    short-term), losses offset gains and carry forward, tax paid from the account at each year end. Dividends and
    T-bill interest are left out for every rule, so this isolates the tax caused by selling. Unsold gains stay deferred."""
    sr = ST_RATE if sr is None else sr
    lr = LT_RATE if lr is None else lr
    if start not in months:
        return None
    i0 = months.index(start) - 1
    cidx, c = {}, 1.0
    for m in months:
        c *= 1 + tb.get(m, 0) / 100 / 12
        cidx[m] = c
    price = lambda s, m: cidx[m] if s == CASH else px[s].get(m)  # noqa: E731
    lots: dict[str, list[list[float]]] = {}
    st = lt = carry = 0.0
    st_gross = lt_gross = 0.0
    sale_months = 0
    v0 = None
    for i in range(i0, len(months) - 1):
        m = months[i]
        w = fn(m)
        if w is None or any(s != CASH and m not in px[s] for s in w):
            return None
        val = {s: sum(u * price(s, m) for _, u, _ in ls) for s, ls in lots.items()}
        V = sum(val.values()) if lots else 1.0
        if v0 is None:
            v0 = V
        target = {s: V * wt for s, wt in w.items()}
        sells = {s: val.get(s, 0) - target.get(s, 0) for s in set(val) | set(target) if val.get(s, 0) - target.get(s, 0) > 1e-12}
        buys = {s: target.get(s, 0) - val.get(s, 0) for s in set(val) | set(target) if target.get(s, 0) - val.get(s, 0) > 1e-12}
        sold_taxable = False
        for s, amt in sells.items():
            p, left = price(s, m), amt
            for lot in lots.get(s, []):
                if left <= 1e-12:
                    break
                lv = lot[1] * p
                take = min(lv, left)
                frac = take / lv if lv else 0
                gain = take - lot[2] * frac
                if s != CASH:
                    sold_taxable = True
                    if i - lot[0] < 12:
                        st += gain; st_gross += max(gain, 0)
                    else:
                        lt += gain; lt_gross += max(gain, 0)
                lot[1] *= 1 - frac; lot[2] *= 1 - frac
                left -= take
            lots[s] = [lt_ for lt_ in lots.get(s, []) if lt_[1] > 1e-12]
        sale_months += sold_taxable
        tot_buy = sum(buys.values())
        cost = COST * (sum(sells.values()) + tot_buy)
        for s, amt in buys.items():
            spend = amt - cost * amt / tot_buy if tot_buy else amt
            lots.setdefault(s, []).append([i, spend / price(s, m), spend])
        if m.endswith("-12") or i == len(months) - 2:
            tax, carry = _net_tax(st, lt, carry, sr, lr)
            st = lt = 0.0
            Vn = sum(u * price(s, m) for s, ls in lots.items() for _, u, _ in ls)
            if tax > 0 and Vn > 0:
                k = 1 - tax / Vn
                for ls in lots.values():
                    for lot in ls:
                        lot[1] *= k; lot[2] *= k
    end = months[-1]
    Vend = sum(u * price(s, end) for s, ls in lots.items() for _, u, _ in ls)
    yrs = (len(months) - 1 - i0) / 12
    g = st_gross + lt_gross
    # If everything were sold at the end: tax the unrealized gains too
    a = b = 0.0
    iend = len(months) - 1
    for sym, ls in lots.items():
        if sym == CASH:
            continue
        for e, u, basis in ls:
            gain = u * price(sym, end) - basis
            if iend - e < 12:
                a += gain
            else:
                b += gain
    tax_end, _ = _net_tax(a, b, carry, sr, lr)
    return {"after_tax_cagr_pct": round(((Vend / v0) ** (1 / yrs) - 1) * 100, 1),
            "sold_at_end_cagr_pct": round((((Vend - tax_end) / v0) ** (1 / yrs) - 1) * 100, 1), "sale_months_per_year": round(sale_months / yrs, 1),
            "short_term_share_pct": round(st_gross / g * 100) if g > 0 else None}


def new_money(spx: dict, tb: dict, months: list[str], horizon: int = 24, asset: dict | None = None) -> dict:
    """Putting a lump of new cash in: all at once, spread over 6 or 12 months, wait for the trend rule, or wait for a
    10% drop. Every month-end start with enough history; value after `horizon` months; cash waiting earns T-bills.
    The money goes into SPY, or into `asset` (a rule's month-end value series) when given; the waiting signals are
    always read on SPY, as the plan card states them."""
    sm = [m for m in months if m in spx]
    above_m, peak_m, hi = {}, {}, 0.0
    for j, m in enumerate(sm):
        hi = max(hi, spx[m]); peak_m[m] = hi
        if j >= 9:
            above_m[m] = spx[m] > statistics.mean(spx[k] for k in sm[j - 9:j + 1])
    src = asset if asset is not None else spx
    ms = [m for m in sm if m in src]
    p = [src[m] for m in ms]
    s = [spx[m] for m in ms]
    bill = [tb.get(m, 0) / 100 / 12 for m in ms]
    above = [above_m.get(m) for m in ms]
    peak = [peak_m[m] for m in ms]

    def run(k, plan):
        cash, units = 1.0, 0.0
        for j in range(k, k + horizon + 1):
            if j > k:
                cash *= 1 + bill[j]
            amt = plan(k, j, cash)
            if amt > 0:
                units += amt / p[j]; cash -= amt
        return units * p[k + horizon] + cash - 1

    def spread(n):
        return lambda k, j, cash: cash / (n - (j - k)) if j - k < n else 0.0

    approaches = [
        ("lump", "All at once", "Invest everything at the month-end close.", lambda k, j, cash: cash if j == k else 0.0),
        ("dca6", "Spread over 6 months", "One sixth each month-end, the rest waiting in T-bills.", spread(6)),
        ("dca12", "Spread over 12 months", "One twelfth each month-end.", spread(12)),
        ("trend", "Wait for the trend rule", "Invest at the first month-end close above the 10-month average (at once if it already is); otherwise wait in T-bills.",
         lambda k, j, cash: cash if above[j] else 0.0),
        ("dip", "Wait for a 10% drop", "Invest once SPY closes a month 10% or more below its highest month-end close; if that has not happened within 12 months, invest anyway.",
         lambda k, j, cash: cash if (s[j] <= 0.9 * peak[j] or j - k >= 12) else 0.0),
    ]
    starts = [k for k in range(len(ms) - horizon) if above[k] is not None]
    res = {a[0]: [run(k, a[3]) for k in starts] for a in approaches}
    down5 = [i for i, k in enumerate(starts) if s[k] <= 0.95 * peak[k]]
    out = []
    for key, name, how, _ in approaches:
        r, l = res[key], res["lump"]
        def summ(idx):
            if not idx:
                return None
            rr = [r[i] for i in idx]
            return {"median_pct": round(statistics.median(rr) * 100, 1), "worst_pct": round(min(rr) * 100, 1),
                    "beat_lump_pct": round(sum(r[i] > l[i] + 0.0005 for i in idx) / len(idx) * 100) if key != "lump" else None,
                    "same_pct": round(sum(abs(r[i] - l[i]) <= 0.0005 for i in idx) / len(idx) * 100) if key != "lump" else None,
                    "avg_vs_lump_pct": round(statistics.mean(r[i] - l[i] for i in idx) * 100, 1) if key != "lump" else None, "n": len(idx)}
        out.append({"key": key, "name": name, "how": how, "all": summ(list(range(len(starts)))), "after_pullback": summ(down5)})
    return {"horizon_months": horizon, "first_start": ms[starts[0]], "last_start": ms[starts[-1]], "starts": len(starts),
            "pullback_starts": len(down5), "approaches": out}


def _easter(y: int) -> dt.date:
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    mo = (h + l_ - 7 * m + 114) // 31
    return dt.date(y, mo, (h + l_ - 7 * m + 114) % 31 + 1)


def _nth_weekday(y: int, mo: int, wd: int, n: int) -> dt.date:
    d = dt.date(y, mo, 1)
    d += dt.timedelta(days=(wd - d.weekday()) % 7)
    if n > 0:
        return d + dt.timedelta(weeks=n - 1)
    last = (dt.date(y, mo, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    return last - dt.timedelta(days=(last.weekday() - wd) % 7)


def _observed(d: dt.date) -> dt.date:
    return d - dt.timedelta(days=1) if d.weekday() == 5 else d + dt.timedelta(days=1) if d.weekday() == 6 else d


def nyse_holidays(y: int) -> set[dt.date]:
    h = {_nth_weekday(y, 1, 0, 3), _nth_weekday(y, 2, 0, 3), _easter(y) - dt.timedelta(days=2), _nth_weekday(y, 5, 0, -1),
         _observed(dt.date(y, 6, 19)), _observed(dt.date(y, 7, 4)), _nth_weekday(y, 9, 0, 1), _nth_weekday(y, 11, 3, 4),
         _observed(dt.date(y, 12, 25))}
    if dt.date(y, 1, 1).weekday() != 5:  # a Saturday New Year is not observed on the prior Friday
        h.add(_observed(dt.date(y, 1, 1)))
    return h


def is_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in nyse_holidays(d.year)


def last_trading_day(y: int, mo: int) -> dt.date:
    """Last NYSE trading day of the month."""
    d = (dt.date(y, mo, 28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def write_calendar(first: dt.date, n: int = 12) -> None:
    """data/decisions.ics: a subscribable calendar of month-end decision dates, with a heads-up two trading days before."""
    from zoneinfo import ZoneInfo
    ny, url = ZoneInfo("America/New_York"), "https://jhyunkn.github.io/market-analysis/#plan"
    stamp = NOW.strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//market-analysis//strategy lab//EN", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Strategy lab decisions", "X-WR-CALDESC:Month-end decision dates for the market-analysis strategy lab",
             "REFRESH-INTERVAL;VALUE=DURATION:P1D", "X-PUBLISHED-TTL:P1D"]
    y, mo = first.year, first.month
    for _ in range(n):
        d = last_trading_day(y, mo)
        start = dt.datetime(d.year, d.month, d.day, 16, 45, tzinfo=ny).astimezone(dt.timezone.utc)
        heads, k = d, 0
        while k < 2:
            heads -= dt.timedelta(days=1)
            k += is_trading_day(heads)
        tag = f"{d.year}{d.month:02d}"
        lines += ["BEGIN:VEVENT", f"UID:decision-{tag}@market-analysis", f"DTSTAMP:{stamp}",
                  f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{(start + dt.timedelta(minutes=30)).strftime('%Y%m%dT%H%M%SZ')}",
                  "SUMMARY:Month-end decision: check your rule",
                  f"DESCRIPTION:The month-end close is in. The page rebuilds about 4:40 PM ET; open it and act only if your rule's holdings changed. {url}",
                  f"URL:{url}", "BEGIN:VALARM", "ACTION:DISPLAY", "DESCRIPTION:Month-end decision", "TRIGGER:PT0M", "END:VALARM", "END:VEVENT",
                  "BEGIN:VEVENT", f"UID:headsup-{tag}@market-analysis", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{heads.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(heads + dt.timedelta(days=1)).strftime('%Y%m%d')}",
                  f"SUMMARY:Decision on {d.strftime('%a %b')} {d.day}: see how close your rule is",
                  f"DESCRIPTION:Two trading days to the month-end decision. The plan card shows whether your rule would change at today's price. {url}",
                  f"URL:{url}", "TRANSP:TRANSPARENT", "END:VEVENT"]
        y, mo = (y + 1, 1) if mo == 12 else (y, mo + 1)
    lines.append("END:VCALENDAR")
    (DATA / "decisions.ics").write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")


def plan_checks(px: dict, tb: dict, months: list[str], latest: dict, partial: str | None) -> dict:
    """For each rule, what decides it at the coming month-end and how far today's price is from changing it."""
    def line(s):  # the month-end close at which price equals its 10-month average (with this month as the 10th)
        return statistics.mean(px[s][m] for m in months[-9:])

    def gap(s):
        ln = line(s)
        return {"symbol": s, "now": round(latest[s]["close"], 2), "line": round(ln, 2), "gap_pct": round((latest[s]["close"] / ln - 1) * 100, 1)}

    out = {"hold_spy": [{"kind": "none"}], "sixty_forty": [{"kind": "rebalance"}],
           "trend_spy": [{"kind": "line", **gap("SPY")}],
           "five_asset_trend": [{"kind": "line", **gap(s)} for s in ("SPY", "EFA", "EEM", "TLT", "GLD")]}
    if partial:
        pm = months + [partial]
        ppx = {s: {**px[s], partial: latest[s]["close"]} for s in px}
        tbx = {**tb, partial: tb.get(partial, tb[max(tb)])}
        r = lambda s: ret(ppx, s, partial, 12, pm)  # noqa: E731
        out["dual_momentum"] = [{"kind": "compare", "what": "SPY 12-month return vs T-bills", "a": round(r("SPY") * 100, 1), "b": round(bill_12m(tbx, partial, pm) * 100, 1)},
                                {"kind": "compare", "what": "SPY vs EFA, 12-month return", "a": round(r("SPY") * 100, 1), "b": round(r("EFA") * 100, 1)}]
        ranks = sorted(((ret(ppx, s, partial, 12, pm, skip=1), s) for s in SECTORS), reverse=True)
        sec = {"kind": "rank", "top": [s for _, s in ranks[:3]], "fourth": ranks[3][1], "margin_pct": round((ranks[2][0] - ranks[3][0]) * 100, 1), "fixed": True}
        out["sector_momentum"] = [sec]
        out["sector_momentum_trend"] = [{"kind": "line", **gap("SPY")}, sec]
    return out


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
        if key in PUBLISHED:
            frm, src = PUBLISHED[key]
            idx2 = [i for i, d in enumerate(sim["dates"]) if d >= frm]
            if len(idx2) >= 36:
                ds = [sim["dates"][i] for i in idx2]
                mine2, _ = stats([sim["rets"][i] for i in idx2], ds, tb)
                spy2, _ = stats([spy_by[d] for d in ds if d in spy_by], [d for d in ds if d in spy_by], tb)
                out_rows[-1]["since_published"] = {"from": mine2["start"], "source": src, "cagr_pct": mine2["cagr_pct"], "max_dd_pct": mine2["max_dd_pct"],
                                                   "spy_cagr_pct": spy2["cagr_pct"], "spy_max_dd_pct": spy2["max_dd_pct"], "years": mine2["years"]}
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
    me = last_trading_day(next_end.year, next_end.month)
    # Taxable-account view of each rule on the common period
    for r in out_rows:
        r["tax"] = tax_sim(rs[r["key"]][2], px, tb, months, common_start)
        grid = {}
        for f_st, f_lt in TAX_FED:
            for st_ in TAX_STATE:
                t_ = tax_sim(rs[r["key"]][2], px, tb, months, common_start, (f_st + st_) / 100, (f_lt + st_) / 100)
                if t_:
                    grid[f"{f_st}/{f_lt}/{st_}"] = [t_["after_tax_cagr_pct"], t_["sold_at_end_cagr_pct"]]
        r["tax_grid"] = grid
    checks = plan_checks(px, tb_full, months, latest, partial)
    nm = new_money(px["SPY"], tb, months)
    nm_rules = {}
    for key, sim in sims.items():
        if not sim:
            continue
        base = months[months.index(sim["dates"][0]) - 1]
        cur, v = {base: 1.0}, 1.0
        for d, rr in zip(sim["dates"], sim["rets"]):
            v *= 1 + rr; cur[d] = v
        res = new_money(px["SPY"], tb, months, asset=cur)
        nm_rules[key] = {"first_start": res["first_start"], "starts": res["starts"], "approaches": {a["key"]: a["all"] for a in res["approaches"]}}
    peak = max(max(px["SPY"].values()), latest["SPY"]["close"])
    spy_ln = statistics.mean(px["SPY"][m] for m in months[-10:])
    nm["now"] = {"asof": latest["SPY"]["date"], "close": round(latest["SPY"]["close"], 2), "below_high_pct": round((latest["SPY"]["close"] / peak - 1) * 100, 1),
                 "dip_line": round(0.9 * peak, 2), "trend_in_decided": px["SPY"][months[-1]] > spy_ln, "trend_line": checks["trend_spy"][0]["line"]}
    try:
        write_calendar(me)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::calendar not written: {e}")
    payload = {"rows": [{"decided_at": months[-1], "next_decision": me.isoformat(), "common_start": common_start, "strategies": out_rows,
                         "curves": curves, "provisional": prov, "sectors": sector_table, "live": live, "checks": checks, "new_money": nm, "new_money_by_rule": nm_rules,
                         "tax_rates": {"short_term_pct": round(ST_RATE * 100), "long_term_pct": round(LT_RATE * 100)},
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
