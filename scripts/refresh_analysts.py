#!/usr/bin/env python3
"""Refresh config/analyst_targets.json from Nasdaq's public analyst price-target API.

Each symbol carries its own `as_of` date. A symbol Nasdaq does not return keeps its
previous entry and date, so it ages out (marked stale) instead of silently vanishing.
The file is only rewritten when at least half of the previously covered symbols refresh,
so a blocked or broken endpoint never wipes the snapshot.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "config" / "analyst_targets.json"
UNIVERSE = ROOT / "config" / "universe.json"
URL = "https://api.nasdaq.com/api/analyst/{}/targetprice"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def fetch(symbol: str) -> dict | None:
    req = urllib.request.Request(URL.format(symbol.replace("-", ".")), headers=HEADERS)
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = (json.load(resp) or {}).get("data") or {}
            c = data.get("consensusOverview") or {}
            mean, lo, hi = c.get("priceTarget"), c.get("lowPriceTarget"), c.get("highPriceTarget")
            buy, hold, sell = (int(c.get(k) or 0) for k in ("buy", "hold", "sell"))
            n = buy + hold + sell
            if not mean or not lo or not hi or n == 0 or not (lo <= mean <= hi):
                return None
            return {"mean": round(float(mean), 2), "low": float(lo), "high": float(hi), "analysts": n, "buy_pct": round(100 * buy / n)}
        except Exception:
            time.sleep(2 * (attempt + 1))
    return None


def main() -> int:
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    old = json.loads(PATH.read_text(encoding="utf-8"))
    prev = old.get("targets", {})
    symbols = [row["symbol"] for row in json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]]
    # Covered names stalest first, so a run cut short by the time budget still rotates through all of them over successive
    # runs; names Nasdaq has never covered (crypto, some funds) go last, since they rarely return anything.
    symbols.sort(key=lambda s: (s not in prev, (prev.get(s) or {}).get("as_of") or "0000", s))
    targets, fresh, tried, tried_cov, start = {}, 0, 0, 0, time.monotonic()
    BUDGET = 540  # seconds: a slow or blocking Nasdaq must never hold up the rest of the refresh
    for symbol in symbols:
        out_of_time = time.monotonic() - start > BUDGET
        dead = tried_cov >= 12 and fresh == 0  # a dozen previously covered names all failed: the endpoint is down or blocking us
        new = None if out_of_time or dead else fetch(symbol)
        tried += 0 if out_of_time or dead else 1
        tried_cov += 0 if out_of_time or dead or symbol not in prev else 1
        before = prev.get(symbol)
        if new:
            fresh += 1
            moved = before is not None and before.get("source") == "nasdaq" and abs(new["mean"] / before["mean"] - 1) > 0.005
            new["last_changed"] = today if moved else (before or {}).get("last_changed")
            new["as_of"] = today
            new["source"] = "nasdaq"
            targets[symbol] = new
        elif before is not None:
            before.setdefault("as_of", old.get("synced_at"))
            targets[symbol] = before
        if not (out_of_time or dead):
            time.sleep(0.3)
    if tried < len(symbols):
        print(f"analyst refresh: stopped after {tried} of {len(symbols)} symbols ({'time budget' if fresh else 'no responses'}); the rest keep their earlier entries", file=sys.stderr)
    covered_before = sum(1 for s in prev if s in symbols)
    if fresh < max(1, min(covered_before, tried) // 2):
        print(f"analyst refresh: only {fresh} of {covered_before} symbols returned; keeping the previous file", file=sys.stderr)
        return 1
    payload = {"source": "Analyst consensus price targets (Nasdaq public API; earlier entries from a Robinhood snapshot)",
               "synced_at": today, "targets": dict(sorted(targets.items()))}
    PATH.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"analyst targets: {fresh} refreshed, {len(targets) - fresh} kept from earlier, {len(symbols) - len(targets)} without coverage")
    return 0


if __name__ == "__main__":
    sys.exit(main())
