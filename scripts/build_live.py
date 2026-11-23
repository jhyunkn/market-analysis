#!/usr/bin/env python3
"""Prices since the last official close: index futures and each stock's extended-hours price.

Writes data/live.json:
- futures: S&P 500, Nasdaq 100, Dow and Russell 2000 futures (trade nearly 24 hours from Sunday 6 PM ET),
  with the change since the stock market's last official close.
- stocks: for every symbol, the last completed official close and the latest price after it (pre-market,
  regular session or after-hours), with its time and session (Yahoo Finance; the 8 PM-4 AM overnight session
  is not included).
The analysis on the page stays on official closes; this is a separate "since the close" view.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "live.json"
UNIVERSE = ROOT / "config" / "universe.json"
ET = ZoneInfo("America/New_York")
FUTURES = [("ES=F", "S&P 500 futures"), ("NQ=F", "Nasdaq 100 futures"), ("YM=F", "Dow futures"), ("RTY=F", "Russell 2000 futures")]


def chart(sym: str, range_: str = "5d", interval: str = "5m", prepost: bool = True) -> dict | None:
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}"
           f"?range={range_}&interval={interval}&includePrePost={'true' if prepost else 'false'}")
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as resp:
                return json.load(resp)["chart"]["result"][0]
        except Exception:
            time.sleep(1 + attempt)
    return None


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def bars_of(r: dict) -> list[tuple[int, float]]:
    return [(t, c) for t, c in zip(r.get("timestamp") or [], (r["indicators"]["quote"][0].get("close") or [])) if c is not None]


def future(sym: str, name: str, ref_t: int | None) -> dict | None:
    """Change since the stock market's last official close (ref_t), measured on the futures price at that moment."""
    r = chart(sym)
    if not r:
        return None
    m = r["meta"]; px = m.get("regularMarketPrice")
    ref = [c for t, c in bars_of(r) if ref_t and t <= ref_t]
    if not px or not ref:
        return None
    return {"symbol": sym, "name": name, "price": round(px, 2 if px >= 1 else 6), "at_close": round(ref[-1], 2 if ref[-1] >= 1 else 6), "chg_pct": round((px / ref[-1] - 1) * 100, 2), "time": iso(m["regularMarketTime"])}


def stock(sym: str) -> dict | None:
    """Latest price against the last completed official close, labeled by session:
    pre-market (4-9:30 AM ET), regular (9:30 AM-4 PM, quotes delayed) or after-hours (4-8 PM)."""
    intra, daily = chart(sym, "1d", "5m", True), chart(sym, "5d", "1d", False)
    if not intra or not daily:
        return None
    now = time.time()
    tp = intra["meta"].get("currentTradingPeriod") or {}
    reg = tp.get("regular") or {}
    today = dt.datetime.fromtimestamp(reg.get("start", now), ET).date()
    closes = [(dt.datetime.fromtimestamp(t, ET).date(), c) for t, c in bars_of(daily)]
    if not closes:
        return None
    regular_done = now >= reg.get("end", 0)
    completed = [(d, c) for d, c in closes if d < today or (d == today and regular_done)]
    if not completed:
        return None
    close_day, close = completed[-1]
    prev = [c for d, c in completed[:-1]]
    close_time = dt.datetime.combine(close_day, dt.time(16, 0), ET)
    bars = bars_of(intra)
    m = intra["meta"]
    last_t, last_p = (bars[-1] if bars else (m.get("regularMarketTime"), m.get("regularMarketPrice")))
    if m.get("regularMarketTime") and m["regularMarketTime"] > (last_t or 0):
        last_t, last_p = m["regularMarketTime"], m["regularMarketPrice"]
    out = {"symbol": sym, "close": round(close, 4), "close_time": iso(int(close_time.timestamp()))}
    if regular_done and close_day == today and prev:
        out["day_chg_pct"] = round((close / prev[-1] - 1) * 100, 2)  # today's regular session, already in the close
    if last_t and last_p and last_t > close_time.timestamp() + 60:
        local = dt.datetime.fromtimestamp(last_t, ET)
        hm = local.hour * 60 + local.minute
        session = "regular" if (local.date() == today and reg and reg["start"] <= last_t < reg["end"]) else "pre-market" if hm < 9 * 60 + 30 else "after-hours"
        out.update({"ext_price": round(last_p, 4), "ext_time": iso(int(last_t)), "ext_chg_pct": round((last_p / close - 1) * 100, 2), "session": session})
    return out


def main() -> int:
    syms = [u["symbol"] for u in json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]]
    coins = [x for x in syms if x.endswith("-USD")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        stocks = [s for s in pool.map(stock, [x for x in syms if x not in coins]) if s]
    spy = next((s for s in stocks if s["symbol"] == "SPY"), None)
    ref_t = int(dt.datetime.fromisoformat(spy["close_time"].replace("Z", "+00:00")).timestamp()) if spy else None
    for c in coins:
        f = future(c, c, ref_t)
        if f:
            stocks.append({"symbol": c, "close": f["at_close"], "close_time": spy["close_time"] if spy else None, "ext_price": f["price"],
                           "ext_time": f["time"], "ext_chg_pct": f["chg_pct"], "session": "24/7"})
    futures = [f for f in (future(s, n, ref_t) for s, n in FUTURES) if f]
    if len(stocks) < 0.6 * len(syms) and not futures:
        print(f"live: only {len(stocks)} of {len(syms)} quotes; keeping the previous file", file=sys.stderr)
        return 1
    payload = {"rows": [{"futures": futures, "stocks": stocks, "market_close": spy["close_time"] if spy else None}],
               "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "source": "Yahoo Finance: index futures, and each stock latest pre-market, regular-session (delayed about 15 minutes) or after-hours price against its last completed official close; the 8 PM-4 AM overnight session is not included"}}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    ext = sum(1 for s in stocks if s.get("ext_price"))
    print(f"live: {len(futures)} futures, {len(stocks)} stocks ({ext} with extended-hours prices)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
