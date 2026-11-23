#!/usr/bin/env python3
"""Prices since the last official close: index futures and each stock's extended-hours price.

Writes data/live.json:
- futures: S&P 500, Nasdaq 100, Dow and Russell 2000 futures (trade nearly 24 hours from Sunday 6 PM ET),
  with the change since the stock market's last official close.
- stocks: for every symbol, the official close, the latest pre-market or after-hours price, its time and
  the session it came from (Yahoo Finance; Robinhood's 8 PM-4 AM overnight session is not included).
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


def chart(sym: str) -> dict | None:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym, safe='')}?range=5d&interval=5m&includePrePost=true"
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"}), timeout=25) as resp:
                return json.load(resp)["chart"]["result"][0]
        except Exception:
            time.sleep(1 + attempt)
    return None


def iso(ts: int) -> str:
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def future(sym: str, name: str, ref_t: int | None) -> dict | None:
    """Change since the stock market's last official close (ref_t), measured on the futures price at that moment."""
    r = chart(sym)
    if not r:
        return None
    m = r["meta"]; px = m.get("regularMarketPrice")
    bars = [(t, c) for t, c in zip(r.get("timestamp") or [], (r["indicators"]["quote"][0].get("close") or [])) if c is not None]
    ref = [c for t, c in bars if ref_t and t <= ref_t]
    if not px or not ref:
        return None
    return {"symbol": sym, "name": name, "price": round(px, 2), "at_close": round(ref[-1], 2), "chg_pct": round((px / ref[-1] - 1) * 100, 2), "time": iso(m["regularMarketTime"])}


def stock(sym: str) -> dict | None:
    r = chart(sym)
    if not r:
        return None
    m = r["meta"]; close, close_t = m.get("regularMarketPrice"), m.get("regularMarketTime")
    bars = [(t, c) for t, c in zip(r.get("timestamp") or [], (r["indicators"]["quote"][0].get("close") or [])) if c is not None]
    if not close or not close_t:
        return None
    out = {"symbol": sym, "close": round(close, 4), "close_time": iso(close_t)}
    ext = [(t, c) for t, c in bars if t > close_t + 60]
    if ext:
        t, c = ext[-1]
        local = dt.datetime.fromtimestamp(t, ET)
        close_local = dt.datetime.fromtimestamp(close_t, ET)
        session = "after-hours" if local.date() == close_local.date() else "pre-market"
        out.update({"ext_price": round(c, 4), "ext_time": iso(t), "ext_chg_pct": round((c / close - 1) * 100, 2), "session": session})
    return out


def main() -> int:
    syms = [u["symbol"] for u in json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        stocks = [s for s in pool.map(stock, syms) if s]
    spy = next((s for s in stocks if s["symbol"] == "SPY"), None)
    ref_t = int(dt.datetime.fromisoformat(spy["close_time"].replace("Z", "+00:00")).timestamp()) if spy else None
    futures = [f for f in (future(s, n, ref_t) for s, n in FUTURES) if f]
    if len(stocks) < 0.6 * len(syms) and not futures:
        print(f"live: only {len(stocks)} of {len(syms)} quotes; keeping the previous file", file=sys.stderr)
        return 1
    payload = {"rows": [{"futures": futures, "stocks": stocks, "market_close": spy["close_time"] if spy else None}],
               "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "source": "Yahoo Finance (futures; pre-market 4-9:30 AM and after-hours 4-8 PM ET; not the overnight session)"}}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    ext = sum(1 for s in stocks if s.get("ext_price"))
    print(f"live: {len(futures)} futures, {len(stocks)} stocks ({ext} with extended-hours prices)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
