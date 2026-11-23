#!/usr/bin/env python3
"""How well the price bands on the page would have done in the past. Reproducible, standard library only.

Method
- Bands are rebuilt at each window start exactly as scripts/build_data.py builds them: EWMA volatility (lambda 0.94)
  seeded with the variance of returns 504 to 252 sessions back, decaying toward the past year's volatility with a
  63-session half-life; the 2-in-3 band is +/-1 and the 9-in-10 band +/-1.645 standard deviations of log price,
  centered on the start price (no drift).
- Windows do not overlap and share one calendar (SPY's sessions since the start date), so every fund is tested on the
  same dates. A window needs 504 prior sessions for the volatility model.
- Terminal coverage: did the close at the end of the window land inside the band.
- Intraperiod touches: did any adjusted daily low inside the window reach a threshold fixed at the window's start
  (start price x exp(-k x the horizon's standard deviation)).
- 90% intervals come from a bootstrap that resamples whole windows (all funds in a window move together), 4,000 draws,
  fixed seed.
- High-volatility windows (volatility now / normal >= 1.4 at the start) are reported separately.
- Leveraged and ordinary ETFs are reported separately, and each fund on its own.
- Caveat: the band design was chosen with this history visible, so this is not a fully out-of-sample test.

Usage: python scripts/calibrate_bands.py [--start 2013-01-01] [--out data/calibration.json]
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import random
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "market-data"
LEVERAGED = ["TQQQ", "UPRO", "TECL", "SOXL"]
ORDINARY = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "XLV", "XLY", "XLP", "XLI", "XLU", "SMH"]
HORIZONS = {"1 month": 21, "3 months": 63, "12 months": 252}
TOUCH_K = (0.731, 0.988)


def fetch(sym: str) -> dict[dt.date, tuple[float, float]]:
    """Adjusted close and adjusted low per day since 2008 (cached for 12 hours)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"calib_{sym.replace('^', '_')}.json"
    if path.exists() and time.time() - path.stat().st_mtime < 12 * 3600:
        return {dt.date.fromisoformat(k): tuple(v) for k, v in json.loads(path.read_text()).items()}
    p1 = int(dt.datetime(2008, 1, 1, tzinfo=dt.timezone.utc).timestamp()); p2 = int(time.time()) + 86400
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?period1={p1}&period2={p2}&interval=1d&events=div%2Csplits"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "market-analysis/1.0"}), timeout=40) as resp:
                r = json.load(resp)["chart"]["result"][0]
            break
        except Exception:
            time.sleep(2 * (attempt + 1))
    else:
        raise SystemExit(f"could not fetch {sym}")
    q = r["indicators"]["quote"][0]; adj = r["indicators"].get("adjclose", [{}])[0].get("adjclose") or q["close"]
    out = {}
    for t, c, lo, a in zip(r["timestamp"], q["close"], q["low"], adj):
        if None in (c, lo, a) or not c:
            continue
        out[dt.datetime.fromtimestamp(t, dt.timezone.utc).date()] = (a, lo * a / c)
    path.write_text(json.dumps({k.isoformat(): v for k, v in out.items()}))
    return out


def variance(xs: list[float]) -> float:
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)


def band_sd(closes: list[float], horizon: int) -> tuple[float, float, float]:
    """Standard deviation of log price at the horizon, plus volatility now and normal (annualized)."""
    rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    ewma = variance(rets[-504:-252])
    for r in rets[-252:]:
        ewma = 0.94 * ewma + 0.06 * r * r
    s_now2, s_long2 = ewma, variance(rets[-252:])
    total = sum(s_long2 + (s_now2 - s_long2) * math.exp(-k * math.log(2) / 63) for k in range(horizon))
    return math.sqrt(total), math.sqrt(s_now2 * 252), math.sqrt(s_long2 * 252)


def bootstrap(windows: list[list[int]], draws: int = 4000, seed: int = 11) -> tuple[float, float, float] | None:
    """Pooled rate with a 90% interval, resampling whole windows. Each window is a list of 0/1 outcomes."""
    flat = [x for w in windows for x in w]
    if not flat:
        return None
    rng = random.Random(seed); rates = []
    for _ in range(draws):
        pick = [windows[rng.randrange(len(windows))] for _ in windows]
        n = sum(len(w) for w in pick)
        if n:
            rates.append(sum(sum(w) for w in pick) / n)
    rates.sort()
    return round(sum(flat) / len(flat) * 100), round(rates[int(0.05 * len(rates))] * 100), round(rates[int(0.95 * len(rates)) - 1] * 100)


def run(symbols: list[str], data: dict, calendar: list[dt.date], start: dt.date) -> dict:
    out = {}
    first = next(i for i, d in enumerate(calendar) if d >= start)
    for label, h in HORIZONS.items():
        per = {"b67": [], "b90": [], "t1": [], "t2": [], "hv67": [], "hv90": [], "hvt1": [], "hvt2": []}
        n_windows = 0
        for i in range(first, len(calendar) - h, h):
            d0, d1 = calendar[i], calendar[i + h]
            row = {k: [] for k in per}
            for sym in symbols:
                s = data[sym]
                hist = [s[d][0] for d in calendar[:i + 1] if d in s]
                if len(hist) < 505 or d0 not in s or d1 not in s:
                    continue
                sd, vnow, vlong = band_sd(hist, h)
                p0, p1 = s[d0][0], s[d1][0]
                z = math.log(p1 / p0) / sd
                row["b67"].append(int(abs(z) <= 1.0)); row["b90"].append(int(abs(z) <= 1.645))
                lows = [s[d][1] for d in calendar[i + 1:i + h + 1] if d in s]
                row["t1"].append(int(min(lows) <= p0 * math.exp(-TOUCH_K[0] * sd)))
                row["t2"].append(int(min(lows) <= p0 * math.exp(-TOUCH_K[1] * sd)))
                if vlong and vnow / vlong >= 1.4:
                    row["hv67"].append(int(abs(z) <= 1.0)); row["hv90"].append(int(abs(z) <= 1.645))
                    row["hvt1"].append(row["t1"][-1]); row["hvt2"].append(row["t2"][-1])
            if row["b67"]:
                n_windows += 1
                for k in per:
                    if row[k]:
                        per[k].append(row[k])
        res = {"windows": n_windows, "fund_windows": sum(len(w) for w in per["b67"]),
               "terminal_2in3": bootstrap(per["b67"]), "terminal_9in10": bootstrap(per["b90"]),
               "high_vol_windows": len(per["hv67"]), "high_vol_2in3": bootstrap(per["hv67"]), "high_vol_9in10": bootstrap(per["hv90"])}
        if h == 21:
            res[f"touch_{TOUCH_K[0]}"] = bootstrap(per["t1"]); res[f"touch_{TOUCH_K[1]}"] = bootstrap(per["t2"])
            res["high_vol_touch"] = [bootstrap(per["hvt1"]), bootstrap(per["hvt2"])]
        if n_windows < 20:
            res["note"] = "unproven: fewer than 20 non-overlapping windows"
        out[label] = res
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2013-01-01")
    ap.add_argument("--out", default=str(ROOT / "data" / "calibration.json"))
    a = ap.parse_args()
    start = dt.date.fromisoformat(a.start)
    data = {s: fetch(s) for s in ["SPY"] + LEVERAGED + ORDINARY}
    calendar = sorted(data["SPY"])
    report = {"start": a.start, "method": __doc__.split("Usage:")[0].strip(), "groups": {}, "funds": {}}
    report["groups"]["leveraged"] = run(LEVERAGED, data, calendar, start)
    report["groups"]["ordinary"] = run(ORDINARY, data, calendar, start)
    for s in LEVERAGED + ["QQQ", "SPY"]:
        report["funds"][s] = run([s], data, calendar, start)
    report["generated_at"] = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    Path(a.out).write_text(json.dumps(report, indent=1) + "\n")
    fmt = lambda t: f"{t[0]}% [{t[1]}-{t[2]}]" if t else "n/a"  # noqa: E731
    for name, block in [("LEVERAGED (pooled)", report["groups"]["leveraged"]), ("ORDINARY ETFs (pooled)", report["groups"]["ordinary"])] + [(s, report["funds"][s]) for s in report["funds"]]:
        print(f"== {name}")
        for label, r in block.items():
            line = f"  {label:9s} {r['windows']:3d} windows ({r['fund_windows']} fund-windows): 2-in-3 {fmt(r['terminal_2in3'])} / 9-in-10 {fmt(r['terminal_9in10'])}"
            if f"touch_{TOUCH_K[0]}" in r:
                line += f" | touch {TOUCH_K[0]}: {fmt(r[f'touch_{TOUCH_K[0]}'])}, {TOUCH_K[1]}: {fmt(r[f'touch_{TOUCH_K[1]}'])}"
            if r.get("high_vol_touch"):
                line += f" | high-vol touches {fmt(r['high_vol_touch'][0])}, {fmt(r['high_vol_touch'][1])}"
            line += f" | high-vol {r['high_vol_windows']}: {fmt(r['high_vol_2in3'])} / {fmt(r['high_vol_9in10'])}" + (f"  [{r['note']}]" if r.get("note") else "")
            print(line)
    print("Note: the band design was chosen with this history visible, so this is not a fully out-of-sample test.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
