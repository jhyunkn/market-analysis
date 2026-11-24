#!/usr/bin/env python3
"""Forward-looking reads: what option prices imply, a published trend rule, recession odds, GDPNow, and a live scorecard.

Writes data/forward.json (the page's forward view) and appends to data/forecast_log.json (every range the page
showed, graded once its date arrives). Standard library only. Run after scripts/build_data.py, which fills the price
cache and writes outlook_summary.json and news.json.

Method
- Options-implied range: Nasdaq's public option chains, near-the-money strikes. For each expiry the spot price is
  inferred from put-call parity (strike + call - put at the strike where the two are closest), and the at-the-money
  straddle (call + put, bid/ask midpoints, interpolated between the strikes either side of spot) gives the implied
  standard deviation of the move to expiry: straddle / (0.798 x spot) (Brenner and Subrahmanyam 1988). The 2-in-3
  range is spot x exp(+/- that move), the same convention as the page's historical bands. Option prices carry an
  insurance premium, so implied ranges tend to run a little wider than what follows; the scorecard measures it.
- Earnings move: total implied variance to the first expiry after the report minus the variance the expiry before it
  implies for the same number of days, i.e. the extra move priced for the report itself.
- Trend rule (Faber 2007; Moskowitz, Ooi and Pedersen 2012; Hurst, Ooi and Pedersen 2017): at each month-end close,
  in the market if the price is above its 10-month average of month-end closes, else in Treasury bills. Next
  month-end the rule flips exactly where the close crosses the average of the last 9 completed month-ends (the switch
  line). Track record per ETF over its full history with 3-month T-bills (FRED TB3MS) out of the market and 0.10% per
  switch. No parameter is fitted here.
- Recession odds: the New York Fed's yield-curve model, P = N(-0.5333 - 0.6330 x spread), spread = the month's average
  10-year minus 3-month Treasury yield (FRED T10Y3M, constant-maturity, a close approximation of the Fed's input).
- GDPNow: the Atlanta Fed's running estimate of this quarter's real GDP growth (FRED GDPNOW).
- Scorecard: each build logs every implied and historical range (as a log-return width from the reference close), and
  once the expiry passes, grades whether the actual close landed inside. A well-calibrated 2-in-3 range lands about 68%.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import os
import statistics
import sys
import time
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "market-data"
ET = ZoneInfo("America/New_York")
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.astimezone(ET).date()
ETF_GROUPS = {"Market & sector ETFs", "Leveraged ETFs"}
TREND_TRACK = ["SPY", "QQQ", "IWM", "DIA", "TQQQ", "UPRO", "SOXL", "TECL", "SMH", "XLK"]
LOG_KEEP_DAYS = 400


def http_json(url: str, headers: dict | None = None, timeout: int = 30, tries: int = 3):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json", **(headers or {})})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            if attempt < tries - 1:
                time.sleep(2 * (attempt + 1))
    return None


def fred(series: str) -> dict[str, float]:
    req = urllib.request.Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers={"User-Agent": "curl/8.7.1"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=40) as r:
                lines = r.read().decode().splitlines()[1:]
            out = {}
            for line in lines:
                d, _, v = line.partition(",")
                if v not in (".", ""):
                    out[d] = float(v)
            return out
        except Exception:  # noqa: BLE001
            time.sleep(2 * (attempt + 1))
    return {}


def load_prices(sym: str) -> dict[dt.date, float]:
    for tag in ("_long", ""):
        p = CACHE / f"{sym.replace('^', '_')}{tag}.json"
        if p.exists():
            return {dt.date.fromisoformat(k): v for k, v in json.loads(p.read_text()).items()}
    return {}


def num(x) -> float:
    return float(str(x).replace(',', ''))


def mid(b: str | None, a: str | None) -> float | None:
    try:
        b, a = num(b), num(a)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a < b or (a - b) > 0.5 * (a + b) / 2 + 0.05:
        return None
    return (a + b) / 2


def chain(sym: str, etf: bool) -> dict[dt.date, list[tuple[float, float, float]]]:
    """Near-the-money (strike, call mid, put mid) by expiry over the next ~4 months."""
    q = f"assetclass={'etf' if etf else 'stocks'}&limit=3000&fromdate={TODAY + dt.timedelta(days=1)}&todate={TODAY + dt.timedelta(days=130)}&money=at&type=all"
    d = http_json(f"https://api.nasdaq.com/api/quote/{sym.replace('-', '.')}/option-chain?{q}")
    rows = (((d or {}).get("data") or {}).get("table") or {}).get("rows") or []
    out: dict[dt.date, list] = {}
    exp = None
    for r in rows:
        if r.get("expirygroup"):
            try:
                exp = dt.datetime.strptime(r["expirygroup"], "%B %d, %Y").date()
            except ValueError:
                exp = None
            continue
        if not exp or not r.get("strike"):
            continue
        c, p = mid(r.get("c_Bid"), r.get("c_Ask")), mid(r.get("p_Bid"), r.get("p_Ask"))
        if c is None or p is None:
            continue
        out.setdefault(exp, []).append((num(r["strike"]), c, p))
    return out


def expiry_read(rows: list[tuple[float, float, float]], exp: dt.date) -> dict | None:
    rows = sorted(rows)
    if len(rows) < 3:
        return None
    k0, c0, p0 = min(rows, key=lambda x: abs(x[1] - x[2]))
    spot = k0 + c0 - p0
    below = [r for r in rows if r[0] <= spot]
    above = [r for r in rows if r[0] >= spot]
    if not below or not above:
        return None
    a, b = below[-1], above[0]
    sa, sb = a[1] + a[2], b[1] + b[2]
    straddle = sa if b[0] == a[0] else sa + (sb - sa) * (spot - a[0]) / (b[0] - a[0])
    close_at = dt.datetime.combine(exp, dt.time(16, 0), tzinfo=ET)
    t = max((close_at - NOW).total_seconds(), 3600) / (365 * 86400)  # calendar time to the 4 PM close on expiry
    sd = straddle / (0.798 * spot)
    if not (0.002 < sd < 1.5) or t <= 0:
        return None
    return {"expiry": exp.isoformat(), "days": (exp - TODAY).days, "spot": round(spot, 2), "straddle": round(straddle, 2),
            "move_pct": round(sd * 100, 2), "iv_pct": round(sd / math.sqrt(t) * 100, 1),
            "lo": round(spot * math.exp(-sd), 2), "hi": round(spot * math.exp(sd), 2), "_var": sd * sd, "_t": t}


def trading_days(a: dt.date, b: dt.date) -> int:
    n, d = 0, a
    while d < b:
        d += dt.timedelta(days=1)
        n += d.weekday() < 5
    return n


def options_for(u: dict, summ: dict, earn: dict) -> dict | None:
    sym = u["symbol"]
    ch = chain(sym, u["group"] in ETF_GROUPS)
    reads = {e: expiry_read(rows, e) for e, rows in ch.items()}
    reads = {e: r for e, r in reads.items() if r}
    if not reads:
        return None
    exps = sorted(reads)
    pick = lambda target, lo: min((e for e in exps if (e - TODAY).days >= lo), key=lambda e: abs((e - TODAY).days - target), default=None)
    m1, m3 = pick(30, 14), pick(91, 60)
    out: dict = {"symbol": sym}
    s = summ.get(sym) or {}
    if m1:
        r = reads[m1]
        out["m1"] = {k: v for k, v in r.items() if not k.startswith("_")}
        if s.get("m1_hi") and s.get("price"):
            td = trading_days(TODAY, m1)
            hsd = math.log(s["m1_hi"] / s["price"]) * math.sqrt(max(td, 1) / 21)
            out["m1"]["hist_move_pct"] = round(hsd * 100, 2)
            out["m1"]["vs_hist"] = round(r["move_pct"] / (hsd * 100), 2) if hsd > 0 else None
    if m3 and m3 != m1:
        out["m3"] = {k: v for k, v in reads[m3].items() if not k.startswith("_")}
    e = earn.get(sym) or {}
    if e.get("date"):
        ed = dt.date.fromisoformat(e["date"])
        after_close = "after" in (e.get("timing") or "")
        if ed >= TODAY:
            first_hit = ed + dt.timedelta(days=1) if after_close else ed
            aft = next((x for x in exps if x >= first_hit), None)
            bef = next((x for x in reversed(exps) if x < first_hit and (x - TODAY).days >= 2), None)
            em = None
            if aft:
                ra = reads[aft]
                if bef:
                    rb = reads[bef]
                    extra = ra["_var"] - rb["_var"] / rb["_t"] * ra["_t"]
                    em = math.sqrt(extra) if extra > 0 else None
                out["earnings"] = {"date": e["date"], "timing": e.get("timing"), "estimated": bool(e.get("estimated")),
                                   "expiry": aft.isoformat(), "move_pct": round(em * 100, 2) if em else None,
                                   "to_expiry_move_pct": ra["move_pct"],
                                   "why_none": None if em else ("inverted" if bef else "no_earlier_expiry")}
            if m1 and first_hit <= m1:
                out["m1"]["includes_earnings"] = True
    return out


def month_closes(prices: dict[dt.date, float]) -> tuple[list[tuple[str, float]], bool]:
    """Month-end closes of completed months (the current month counts once its last weekday's close is in)."""
    by: dict[str, tuple[dt.date, float]] = {}
    for d in sorted(prices):
        by[f"{d.year}-{d.month:02d}"] = (d, prices[d])
    keys = sorted(by)
    last_day = by[keys[-1]][0]
    nxt = last_day + dt.timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += dt.timedelta(days=1)
    complete = nxt.month != last_day.month
    if not complete:
        keys = keys[:-1]
    return [(k, by[k][1]) for k in keys], complete


def trend_state(prices: dict[dt.date, float]) -> dict | None:
    m, _ = month_closes(prices)
    if len(m) < 11:
        return None
    px = m[-1][1]
    sma10 = statistics.mean(v for _, v in m[-10:])
    switch = statistics.mean(v for _, v in m[-9:])
    prev_in = m[-2][1] > statistics.mean(v for _, v in m[-11:-1])
    last = max(prices)
    return {"month": m[-1][0], "month_close": round(px, 2), "sma10": round(sma10, 2), "state": "in" if px > sma10 else "out",
            "changed": (px > sma10) != prev_in, "switch_line": round(switch, 2),
            "last_close": round(prices[last], 2), "last_date": last.isoformat()}


def trend_track(prices: dict[dt.date, float], tbill: dict[str, float]) -> dict | None:
    m, _ = month_closes(prices)
    if len(m) < 36:
        return None
    hold, rule, on_prev, switches, inside = [], [], True, 0, 0
    for i in range(10, len(m) - 1):
        on = m[i][1] > statistics.mean(v for _, v in m[i - 9:i + 1])
        nxt = m[i + 1][1] / m[i][1] - 1
        bill = tbill.get(m[i + 1][0] + "-01", 0) / 100 / 12
        r = nxt if on else bill
        if on != on_prev:
            r -= 0.001; switches += 1
        on_prev = on; inside += on
        hold.append(nxt); rule.append(r)

    def stats(rets):
        eq, pk, dd = 1.0, 1.0, 0.0
        for r in rets:
            eq *= 1 + r; pk = max(pk, eq); dd = min(dd, eq / pk - 1)
        return {"cagr_pct": round((eq ** (12 / len(rets)) - 1) * 100, 1), "max_dd_pct": round(dd * 100), "vol_pct": round(statistics.pstdev(rets) * math.sqrt(12) * 100)}
    return {"start": m[10][0], "end": m[-1][0], "years": round(len(rule) / 12, 1), "hold": stats(hold), "rule": stats(rule),
            "in_market_pct": round(inside / len(rule) * 100), "switches": switches}


def norm_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def recession() -> dict | None:
    s = fred("T10Y3M")
    if not s:
        return None
    by: dict[str, list[float]] = {}
    for d, v in s.items():
        by.setdefault(d[:7], []).append(v)
    months = sorted(by)
    prob = lambda m: norm_cdf(-0.5333 - 0.6330 * statistics.mean(by[m]))
    hist = [{"month": m, "spread": round(statistics.mean(by[m]), 2), "prob_pct": round(prob(m) * 100, 1)} for m in months[-36:]]
    yago = months[-13] if len(months) > 13 else None
    return {"month": months[-1], "partial_month": True, "spread": hist[-1]["spread"], "prob_pct": hist[-1]["prob_pct"],
            "prob_12m_ago_pct": round(prob(yago) * 100, 1) if yago else None, "asof": max(s), "history": hist}


def gdpnow() -> dict | None:
    s = fred("GDPNOW")
    if not s:
        return None
    q = max(s)
    d = dt.date.fromisoformat(q)
    prev = sorted(s)[-2] if len(s) > 1 else None
    return {"quarter": f"Q{(d.month - 1) // 3 + 1} {d.year}", "growth_pct": round(s[q], 2),
            "prev_quarter_estimate_pct": round(s[prev], 2) if prev else None}


def update_log(options: list[dict], trend: dict[str, dict], summ: dict) -> dict:
    path = DATA / "forecast_log.json"
    log = json.loads(path.read_text()) if path.exists() else {"ranges": [], "trend": []}
    asof = max((r.get("asof") for r in summ.values() if r.get("asof")), default=None)
    if asof:
        log["ranges"] = [r for r in log["ranges"] if r["d"] != TODAY.isoformat() or r.get("g") is not None]
        for o in options:
            m1 = o.get("m1")
            if m1 and m1.get("hist_move_pct"):
                log["ranges"].append({"d": TODAY.isoformat(), "ref": asof, "s": o["symbol"], "exp": m1["expiry"],
                                      "imp": m1["move_pct"], "hist": m1["hist_move_pct"], "earn": bool(m1.get("includes_earnings"))})
        for sym, t in trend.items():
            if not any(x["s"] == sym and x["m"] == t["month"] for x in log["trend"]):
                log["trend"].append({"m": t["month"], "s": sym, "st": t["state"], "px": t["month_close"]})
    # Grade every range whose expiry close is in, from the reference close's adjusted return (dividend-safe enough).
    cache: dict[str, dict] = {}
    for r in log["ranges"]:
        if r.get("g") is not None or r["exp"] > TODAY.isoformat():
            continue
        p = cache.setdefault(r["s"], load_prices(r["s"]))
        if not p:
            continue
        exp, ref = dt.date.fromisoformat(r["exp"]), dt.date.fromisoformat(r["ref"])
        if max(p) < exp or ref not in p:
            continue
        end = max(d for d in p if d <= exp)
        move = math.log(p[end] / p[ref]) * 100
        r["g"] = {"move": round(move, 2), "in_imp": abs(move) <= r["imp"], "in_hist": abs(move) <= r["hist"]}
    for t in log["trend"]:
        if t.get("next") is not None:
            continue
        p = cache.setdefault(t["s"], load_prices(t["s"]))
        if not p:
            continue
        mc, _ = month_closes(p)
        keys = [k for k, _ in mc]
        if t["m"] in keys and keys.index(t["m"]) + 1 < len(keys):
            i = keys.index(t["m"])
            t["next"] = round((mc[i + 1][1] / mc[i][1] - 1) * 100, 2)
    cutoff = (TODAY - dt.timedelta(days=LOG_KEEP_DAYS)).isoformat()
    log["ranges"] = [r for r in log["ranges"] if r["d"] >= cutoff]
    log["started"] = log.get("started") or TODAY.isoformat()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(log, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)
    graded = [r for r in log["ranges"] if r.get("g")]
    pct = lambda xs: round(sum(xs) / len(xs) * 100) if xs else None
    by_day = {}
    for r in graded:
        by_day.setdefault(r["d"], []).append(r)
    tg = [t for t in log["trend"] if t.get("next") is not None]
    return {"started": log["started"], "logged": len(log["ranges"]), "graded": len(graded), "graded_days": len(by_day),
            "pending": sum(1 for r in log["ranges"] if not r.get("g")),
            "next_grade": min((r["exp"] for r in log["ranges"] if not r.get("g")), default=None),
            "implied_in_pct": pct([r["g"]["in_imp"] for r in graded]), "hist_in_pct": pct([r["g"]["in_hist"] for r in graded]),
            "implied_in_pct_no_earn": pct([r["g"]["in_imp"] for r in graded if not r["earn"]]),
            "avg_implied_pct": round(statistics.mean(r["imp"] for r in graded), 2) if graded else None,
            "avg_hist_pct": round(statistics.mean(r["hist"] for r in graded), 2) if graded else None,
            "avg_actual_pct": round(statistics.mean(abs(r["g"]["move"]) for r in graded), 2) if graded else None,
            "trend_graded": len(tg),
            "trend_in_avg_pct": round(statistics.mean(t["next"] for t in tg if t["st"] == "in"), 2) if any(t["st"] == "in" for t in tg) else None,
            "trend_out_avg_pct": round(statistics.mean(t["next"] for t in tg if t["st"] == "out"), 2) if any(t["st"] == "out" for t in tg) else None}


def main() -> int:
    universe = [u for u in json.loads((ROOT / "config/universe.json").read_text())["symbols"] if not u["symbol"].endswith("-USD")]
    summ = {r["symbol"]: r for r in json.loads((DATA / "outlook_summary.json").read_text())["rows"]}
    earn = {r["symbol"]: r.get("earnings") or {} for r in json.loads((DATA / "news.json").read_text())["rows"]}
    et_now = NOW.astimezone(ET)
    if "--options-only" in sys.argv and (et_now.weekday() >= 5 or not dt.time(9, 40) <= et_now.time() <= dt.time(16, 10)):
        print("options-only run outside market hours; nothing to refresh")
        return 0
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        options = [o for o in pool.map(lambda u: options_for(u, summ, earn), universe) if o]
    print(f"options: {len(options)} of {len(universe)} symbols in {time.time() - t0:.0f}s")
    prev_path = DATA / "forward.json"
    prev_row = json.loads(prev_path.read_text())["rows"][0] if prev_path.exists() else {}
    if "--options-only" in sys.argv:
        # Intraday run (no price cache): refresh the option ranges, keep everything else from the last full build.
        if len(options) < 0.6 * len(universe):
            print(f"::warning::options data for only {len(options)} symbols; keeping the previous file")
            return 0
        row = {**prev_row, "options": sorted(options, key=lambda o: o["symbol"]), "options_at": NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z")}
        meta = json.loads(prev_path.read_text())["meta"] if prev_path.exists() else {}
        meta["generated_at"] = row["options_at"]
        tmp = DATA / "forward.json.tmp"
        tmp.write_text(json.dumps({"rows": [row], "meta": meta}, separators=(",", ":"), allow_nan=False), encoding="utf-8")
        os.replace(tmp, DATA / "forward.json")
        print(f"forward (options only): {len(options)} option reads")
        return 0
    tb = fred("TB3MS")
    trend, track = {}, {}
    for u in universe:
        p = load_prices(u["symbol"])
        if p:
            t = trend_state(p)
            if t:
                trend[u["symbol"]] = t
            if u["symbol"] in TREND_TRACK and tb:
                tr = trend_track(p, tb)
                if tr:
                    track[u["symbol"]] = tr
    rec, gdp = recession(), gdpnow()
    score = update_log(options, {s: trend[s] for s in TREND_TRACK if s in trend}, summ)
    if len(options) < 0.6 * len(universe):
        print(f"::warning::options data for only {len(options)} symbols; keeping the previous option ranges")
        if prev_row:
            old = {r["symbol"]: r for r in prev_row.get("options", [])}
            got = {o["symbol"] for o in options}
            options += [o for s, o in old.items() if s not in got]
    payload = {"rows": [{"asof": TODAY.isoformat(), "options_at": NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "options": sorted(options, key=lambda o: o["symbol"]), "trend": trend, "trend_track": track,
                         "recession": rec, "gdpnow": gdp, "scorecard": score}],
               "meta": {"generated_at": NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "generator": "scripts/build_forward.py",
                        "sources": ["Nasdaq public option chains (bid/ask midpoints)", "Yahoo Finance adjusted prices (via the build cache)",
                                    "FRED T10Y3M, TB3MS, GDPNOW"], "method": __doc__.split("Method", 1)[1].strip()}}
    tmp = DATA / "forward.json.tmp"
    tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(tmp, DATA / "forward.json")
    print(f"forward: {len(options)} option reads, {len(trend)} trend states, {len(track)} track records, "
          f"recession {rec and rec['prob_pct']}%, GDPNow {gdp and gdp['growth_pct']}%, scorecard {score['logged']} logged / {score['graded']} graded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
