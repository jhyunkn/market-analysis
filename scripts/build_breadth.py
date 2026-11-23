#!/usr/bin/env python3
"""Market-wide statistics over the whole S&P 500, not just the curated stock list.

Writes data/breadth.json:
- now: share of S&P 500 stocks above their 50- and 200-day averages, new 52-week highs and lows,
  stocks up vs down over the last day and week.
- history: the share above the 50- and 200-day averages, weekly over three years.
- sectors: the same breadth for each of the 11 GICS sectors, with median 1-week and 1-month returns.
- equal_weight: the average stock (RSP, equal-weight S&P 500) against the index (SPY).
- momentum: how many S&P 500 stocks are leaders, holding up, turning up, breaking down or weak.
- factor_monitor: the standard factor test, run on the S&P 500.
Members and sectors come from Wikipedia's list of S&P 500 companies, refreshed weekly into
config/sp500.json (the committed copy is used if the fetch fails). Today's members are used for
history too, so the long-run tests carry survivorship bias.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import html
import json
import re
import statistics
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIST = ROOT / "config" / "sp500.json"
OUT = B.DATA / "breadth.json"
START = dt.date(2017, 1, 1)


def members() -> list[dict]:
    old = json.loads(LIST.read_text(encoding="utf-8")) if LIST.exists() else None
    if old and (dt.date.today() - dt.date.fromisoformat(old["fetched"])).days < 7:
        return old["members"]
    try:
        req = urllib.request.Request("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers={"User-Agent": "market-analysis/1.0 (public dashboard)"})
        page = urllib.request.urlopen(req, timeout=40).read().decode("utf-8", "replace")
        table = page[page.index('id="constituents"'):]
        table = table[:table.index("</table>")]
        rows = []
        for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S):
            cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
            if len(cells) >= 4 and cells[0]:
                rows.append({"symbol": cells[0].replace(".", "-"), "name": cells[1], "sector": cells[2], "industry": cells[3]})
        if len(rows) >= 480:
            LIST.write_text(json.dumps({"fetched": dt.date.today().isoformat(), "source": "Wikipedia: List of S&P 500 companies", "members": rows}, indent=1) + "\n", encoding="utf-8")
            return rows
    except Exception as exc:  # noqa: BLE001
        print(f"breadth: member list fetch failed ({exc}); using the saved copy", file=sys.stderr)
    if old:
        return old["members"]
    raise SystemExit("breadth: no S&P 500 member list available")


def fetch(sym: str) -> tuple[str, dict | None]:
    for attempt in range(2):
        try:
            return sym, B.fetch_symbol(sym, START, "_sp")
        except RuntimeError:
            time.sleep(2)
    return sym, None


def ma(vals: list[float], n: int, i: int) -> float | None:
    return sum(vals[i - n + 1:i + 1]) / n if i >= n - 1 else None


def main() -> int:
    mem = members()
    syms = [m["symbol"] for m in mem]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        prices = dict(pool.map(fetch, syms + ["SPY", "RSP"]))
    prices = {k: v for k, v in prices.items() if v}
    if "SPY" not in prices or sum(1 for s in syms if s in prices) < 400:
        print("breadth: too few S&P 500 price histories; keeping the previous file", file=sys.stderr)
        return 1
    days = sorted(prices["SPY"])
    sector = {m["symbol"]: m["sector"] for m in mem}
    stocks = [s for s in syms if s in prices]
    spy = [prices["SPY"][d] for d in days]
    spy_ret = lambda n: spy[-1] / spy[-1 - n] - 1  # noqa: E731

    rows = []
    for s in stocks:
        c = [prices[s].get(d) for d in days]
        if c[-1] is None or sum(v is not None for v in c[-260:]) < 250:
            continue
        cc = [v for v in c if v is not None]
        last = cc[-1]
        def ret(n):
            return (cc[-1] / cc[-1 - n] - 1) * 100 if len(cc) > n else None
        m200, m50 = ma(cc, 200, len(cc) - 1), ma(cc, 50, len(cc) - 1)
        hi, lo = max(cc[-252:]), min(cc[-252:])
        r = {"symbol": s, "sector": sector[s], "above200": last > m200, "above50": last > m50, "new_high": last >= hi * 0.999, "new_low": last <= lo * 1.001,
             "ret_1d": ret(1), "ret_1w": ret(5), "ret_1m": ret(21), "vs_ma200_pct": (last / m200 - 1) * 100,
             "mom12_1": (cc[-22] / cc[-253] - 1) * 100 if len(cc) > 253 else None}
        r["rel_1m"] = r["ret_1m"] - spy_ret(21) * 100 if r["ret_1m"] is not None else None
        r["state"] = B.momentum_state(r)
        rows.append(r)
    n = len(rows)
    pct = lambda xs: round(sum(xs) / len(xs) * 100, 1) if xs else None  # noqa: E731
    now = {"n": n, "above200_pct": pct([r["above200"] for r in rows]), "above50_pct": pct([r["above50"] for r in rows]),
           "new_highs": sum(r["new_high"] for r in rows), "new_lows": sum(r["new_low"] for r in rows),
           "up_1d": sum((r["ret_1d"] or 0) > 0 for r in rows), "down_1d": sum((r["ret_1d"] or 0) < 0 for r in rows),
           "up_1w": sum((r["ret_1w"] or 0) > 0 for r in rows), "down_1w": sum((r["ret_1w"] or 0) < 0 for r in rows),
           "median_1w": round(statistics.median(r["ret_1w"] for r in rows), 2), "median_1m": round(statistics.median(r["ret_1m"] for r in rows), 2)}
    sectors = []
    for sec in sorted(set(sector.values())):
        rs = [r for r in rows if r["sector"] == sec]
        if rs:
            sectors.append({"sector": sec, "n": len(rs), "above200_pct": pct([r["above200"] for r in rs]), "above50_pct": pct([r["above50"] for r in rs]),
                            "median_1w": round(statistics.median(r["ret_1w"] for r in rs), 2), "median_1m": round(statistics.median(r["ret_1m"] for r in rs), 2),
                            "new_highs": sum(r["new_high"] for r in rs), "new_lows": sum(r["new_low"] for r in rs)})
    sectors.sort(key=lambda x: x["median_1m"], reverse=True)
    groups = {s: sector[s] for s in stocks}
    hist = B.breadth_history(prices, stocks, groups)
    ew = None
    if "RSP" in prices:
        rsp = [prices["RSP"].get(d) for d in days]
        pairs = [(d, a, b) for d, a, b in zip(days, rsp, spy) if a]
        base = pairs[-126]
        ew = {"series": [{"date": d.isoformat(), "rsp": round((a / base[1] - 1) * 100, 2), "spy": round((b / base[2] - 1) * 100, 2)} for d, a, b in pairs[-126:]],
              "rsp_1m": round((pairs[-1][1] / pairs[-22][1] - 1) * 100, 2), "spy_1m": round((pairs[-1][2] / pairs[-22][2] - 1) * 100, 2),
              "rsp_1y": round((pairs[-1][1] / pairs[-253][1] - 1) * 100, 2), "spy_1y": round((pairs[-1][2] / pairs[-253][2] - 1) * 100, 2)}
    mom = {k: sum(r["state"] == k for r in rows) for k in ("Leader", "Holding up", "Turning up", "Breaking down", "Weak")}
    factors = B.factor_monitor({**{s: prices[s] for s in stocks}, "SPY": prices["SPY"]}, stocks, groups)
    payload = {"rows": [{"asof": days[-1].isoformat(), "now": now, "history": hist, "sectors": sectors, "equal_weight": ew, "momentum_counts": mom, "factor_monitor": factors}],
               "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"), "members": len(mem), "with_prices": n,
                        "source": "Yahoo Finance daily closes; S&P 500 members and GICS sectors from Wikipedia (current members, so history has survivorship bias)"}}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"breadth: {n} of {len(mem)} S&P 500 stocks; above 200d {now['above200_pct']}%, above 50d {now['above50_pct']}%, highs {now['new_highs']} lows {now['new_lows']}, "
          f"up/down week {now['up_1w']}/{now['down_1w']}; factors {len(factors.get('factors', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
