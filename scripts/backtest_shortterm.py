#!/usr/bin/env python3
"""History test of the short-term rating's price checks. Writes data/shortterm_backtest.json.

The short-term rating has six checks; three come from prices alone and can be rebuilt at any past date:
  trend     price above its 50-day average, and the 50-day above the 200-day
  momentum  12-month return skipping the last month in the top 40% of the rated stocks, and positive
  strength  last month's return above SPY's
At every month end since early 2018 (a year of history is needed first), each rated stock is scored with the prices known
that day, then held for the next month. Stocks passing a check are compared with all scored stocks, alone and all three
together. The rule for "works" is fixed in advance, as in the long-term test: ahead in both halves (before and from 2022)
and a t-statistic of at least 2 over the whole period.

Not tested: earnings estimates, the last earnings beat and the no-report-soon check (no free history).
Survivorship: the list is today's large companies. Stocks that rose strongly became large and are in the list, which
tends to flatter trend and momentum checks; treat a pass here as weaker evidence than it looks.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / ".cache" / "market-data"
OUT = ROOT / "data" / "shortterm_backtest.json"
SKIP_GROUPS = {"Market & sector ETFs", "Leveraged ETFs", "Crypto"}
SPLIT = dt.date(2022, 1, 1)
CHECKS = ("trend", "momentum", "strength")


def load(sym: str) -> tuple[list[dt.date], list[float]] | None:
    try:
        d = json.loads((CACHE / f"{sym}.json").read_text())
    except (OSError, ValueError):
        return None
    days = sorted(dt.date.fromisoformat(k) for k in d)
    return days, [float(d[k.isoformat()]) for k in days]


def idx_on(days: list[dt.date], d: dt.date) -> int | None:
    """Index of the last trading day on or before d (binary search)."""
    lo, hi = 0, len(days) - 1
    if not days or days[0] > d:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if days[mid] <= d:
            lo = mid
        else:
            hi = mid - 1
    return lo if (d - days[lo]).days <= 6 else None


def t_of(x: list[float]) -> float | None:
    return round(statistics.mean(x) / statistics.stdev(x) * math.sqrt(len(x)), 2) if len(x) > 2 and statistics.stdev(x) > 0 else None


def main() -> int:
    uni = [u["symbol"] for u in json.loads((ROOT / "config/universe.json").read_text())["symbols"] if u["group"] not in SKIP_GROUPS]
    px = {s: v for s in uni if (v := load(s)) and len(v[0]) > 300}
    spy = load("SPY")
    if not spy or len(px) < 50:
        print("short-term backtest: not enough price history; skipping")
        return 0
    sdays, sp = spy
    ends = [d for i, d in enumerate(sdays) if i + 1 == len(sdays) or sdays[i + 1].month != d.month]
    gaps: dict[str, list[tuple[dt.date, float]]] = {k: [] for k in CHECKS + ("all_three",)}
    counts: dict[str, list[int]] = {k: [] for k in gaps}
    for d0, d1 in zip(ends, ends[1:]):
        i0s, i1s = idx_on(sdays, d0), idx_on(sdays, d1)
        if i0s is None or i0s < 21 or i1s is None:
            continue
        spy1m = sp[i0s] / sp[i0s - 21] - 1
        feats = {}
        for s, (days, p) in px.items():
            i0, i1 = idx_on(days, d0), idx_on(days, d1)
            if i0 is None or i1 is None or i0 < 252:
                continue
            ma50, ma200 = statistics.mean(p[i0 - 49:i0 + 1]), statistics.mean(p[i0 - 199:i0 + 1])
            feats[s] = {"trend": p[i0] > ma50 > ma200, "mom": p[i0 - 21] / p[i0 - 252] - 1, "r1m": p[i0] / p[i0 - 21] - 1, "fwd": p[i1] / p[i0] - 1}
        if len(feats) < 50:
            continue
        moms = sorted(f["mom"] for f in feats.values())
        cut = moms[int(len(moms) * 0.6)]
        allr = [f["fwd"] for f in feats.values()]
        base = statistics.mean(allr)
        passed = {"trend": [], "momentum": [], "strength": [], "all_three": []}
        for f in feats.values():
            ok = {"trend": f["trend"], "momentum": f["mom"] >= cut and f["mom"] > 0, "strength": f["r1m"] > spy1m}
            for k in CHECKS:
                if ok[k]:
                    passed[k].append(f["fwd"])
            if all(ok.values()):
                passed["all_three"].append(f["fwd"])
        for k, v in passed.items():
            if len(v) >= 5:
                gaps[k].append((d1, statistics.mean(v) - base))
                counts[k].append(len(v))
    out = {}
    for k, rows in gaps.items():
        g = [x for _, x in rows]
        h1 = [x for d, x in rows if d < SPLIT]
        h2 = [x for d, x in rows if d >= SPLIT]
        ann = lambda x: round(statistics.mean(x) * 12 * 100, 2) if x else None  # noqa: E731  (simple annualized gap, points)
        t = t_of(g)
        out[k] = {"months": len(g), "avg_stocks": round(statistics.mean(counts[k]), 1) if counts[k] else None, "gap_pct": ann(g), "t_stat": t,
                  "first_half_gap_pct": ann(h1), "second_half_gap_pct": ann(h2), "ahead_months_pct": round(sum(x > 0 for x in g) / len(g) * 100) if g else None,
                  "works": bool(h1 and h2 and statistics.mean(h1) > 0 and statistics.mean(h2) > 0 and t is not None and t >= 2)}
    first = min((r[0][0] for r in gaps.values() if r), default=None)
    last = max((r[-1][0] for r in gaps.values() if r), default=None)
    payload = {"rows": [{"checks": out}], "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
               "generator": "scripts/backtest_shortterm.py", "stocks": len(px), "from": first.isoformat() if first else None, "to": last.isoformat() if last else None,
               "split": SPLIT.isoformat(), "rule": "ahead of all scored stocks in both halves and t >= 2 over the whole period",
               "not_tested": ["estimates", "last earnings beat", "no report in the next 10 days"], "method": __doc__.split("\n", 2)[2].strip()}}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
    tmp.replace(OUT)
    print("short-term backtest:", ", ".join(f"{k} {v['gap_pct']:+} pts (t {v['t_stat']}, {'works' if v['works'] else 'no'})" for k, v in out.items() if v["gap_pct"] is not None))
    return 0


if __name__ == "__main__":
    sys.exit(main())
