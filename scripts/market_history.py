#!/usr/bin/env python3
"""Where today's market sits against history, and what the S&P 500 did next.

Writes data/market_history.json with two tables:
- gauges: each stress gauge's reading, its percentile over its full history, and the S&P 500's
  forward 1-, 3- and 12-month price change on past days whose reading sat within +/-5 percentile
  points of today's.
- setups: the same forward statistics for named market setups (VIX 30+, S&P 8-20% off its high,
  split by credit stress, and so on), flagging which ones fit today.

"Episodes" count separate stretches (a gap of more than a month starts a new one), because
overlapping days from one sell-off are not independent evidence.
Standard library only; reuses build_data's cached Yahoo and FRED fetchers.
"""
from __future__ import annotations

import bisect
import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as B  # noqa: E402

OUT = B.DATA / "market_history.json"
NAN = float("nan")


def isnum(x) -> bool:
    return x is not None and not (isinstance(x, float) and math.isnan(x))


def on_cal(src: dict, cal: list[dt.date]) -> list[float]:
    """Forward-fill a series onto the S&P calendar; NaN before it starts."""
    out, prev, keys, j = [], None, sorted(src), 0
    for d in cal:
        while j < len(keys) and keys[j] <= d:
            prev = src[keys[j]]
            j += 1
        out.append(prev if prev is not None else NAN)
    return out


def drawdown(a: list[float]) -> list[float]:
    out, peak = [], None
    for v in a:
        if not isnum(v):
            out.append(NAN)
            continue
        peak = v if peak is None else max(peak, v)
        out.append((v / peak - 1) * 100)
    return out


def r(x, n=1):
    return round(float(x), n) if isnum(x) else None


def main() -> int:
    try:
        spx = B.fetch_symbol("^GSPC", dt.date(1950, 1, 1), "_long1950")
        series = {
            "vix": B.fetch_symbol("^VIX", dt.date(1990, 1, 1), "_hist"),
            "vix3m": B.fetch_symbol("^VIX3M", dt.date(2006, 1, 1), "_hist"),
            "ndx": B.fetch_symbol("^NDX", dt.date(1985, 1, 1), "_hist"),
            "btc": B.fetch_symbol("BTC-USD", dt.date(2014, 9, 1), "_hist"),
        }
    except RuntimeError as exc:
        print(f"market_history: price fetch failed ({exc}); keeping the previous file", file=sys.stderr)
        return 1
    fred = {s: B.fetch_fred(s) for s in ("BAA10Y", "T10Y2Y", "DGS10")}

    cal = sorted(spx)
    T = len(cal)
    px = [spx[d] for d in cal]
    fwd = {n: [px[i + n] / px[i] - 1 if i + n < T else NAN for i in range(T)] for n in (21, 63, 252)}
    dd = drawdown(px)
    ma200 = B.moving_average(px, 200)
    ret1m = [(px[i] / px[i - 21] - 1) * 100 if i >= 21 else NAN for i in range(T)]
    vix, vix3m = on_cal(series["vix"], cal), on_cal(series["vix3m"], cal)
    baa = on_cal(fred["BAA10Y"], cal) if fred["BAA10Y"] else [NAN] * T

    gauges = [  # id, label, values, unit, higher_is_stress, meaning
        ("vix", "VIX (expected S&P volatility)", vix, "pts", True, "How much turbulence options traders are paying to protect against."),
        ("vix_ratio", "VIX vs 3-month VIX", [a / b if isnum(a) and isnum(b) and b else NAN for a, b in zip(vix, vix3m)], "ratio", True,
         "Above 1.0 means traders fear the next month more than the next three: acute stress."),
        ("spx_dd", "S&P 500 below its all-time high", dd, "%", False, "How far the index has fallen from its peak."),
        ("spx_1m", "S&P 500 past month", ret1m, "%", False, "Speed of the recent move."),
        ("spx_ma200", "S&P 500 vs its 200-day average", [(p / m - 1) * 100 if m else NAN for p, m in zip(px, ma200)], "%", False,
         "Below zero is the classic line for a downtrend."),
        ("ndx_dd", "Nasdaq 100 below its high", drawdown(on_cal(series["ndx"], cal)), "%", False, "The tech-heavy index that leads most risk-on and risk-off swings."),
        ("btc_dd", "Bitcoin below its high", drawdown(on_cal(series["btc"], cal)), "%", False, "A gauge of speculative risk appetite."),
    ]
    if fred["BAA10Y"]:
        gauges.append(("baa", "Corporate credit spread (Baa minus 10-yr)", baa, "pts", True,
                       "Extra yield lenders demand from companies. Rising fast is credit stress, which separates corrections from crises."))
    if fred["T10Y2Y"]:
        gauges.append(("slope", "Yield curve (10-yr minus 2-yr)", on_cal(fred["T10Y2Y"], cal), "pts", False, "Negative (inverted) has preceded most US recessions."))
    if fred["DGS10"]:
        gauges.append(("y10", "10-year Treasury yield", on_cal(fred["DGS10"], cal), "%", True, "The cost of money for the whole economy."))

    def stats(mask: list[bool]) -> dict:
        idx = [i for i, m in enumerate(mask) if m]
        starts = [idx[0]] + [b for a, b in zip(idx, idx[1:]) if b - a > 21] if idx else []
        f1 = [fwd[21][i] for i in idx if isnum(fwd[21][i])]
        f3 = [fwd[63][i] for i in idx if isnum(fwd[63][i])]
        f12 = [fwd[252][i] for i in idx if isnum(fwd[252][i])]
        return {"days": len(idx), "episodes": len(starts),
                "fwd1m_median": r(statistics.median(f1) * 100) if f1 else None,
                "fwd3m_median": r(statistics.median(f3) * 100) if f3 else None,
                "fwd3m_p10": r(B.percentile(f3, 10) * 100) if f3 else None,
                "fwd12m_median": r(statistics.median(f12) * 100) if f12 else None,
                "fwd12m_pos_pct": r(sum(x > 0 for x in f12) / len(f12) * 100, 0) if f12 else None,
                "fwd12m_worst": r(min(f12) * 100) if f12 else None,
                "recent_starts": ", ".join(cal[s].strftime("%b %Y") for s in starts[-4:])}

    base = stats([True] * T)
    rows = []
    for gid, label, a, unit, stress_up, meaning in gauges:
        ok = [isnum(v) for v in a]
        hist = [v for v in a if isnum(v)]
        if len(hist) < 500:
            continue
        cur = hist[-1]
        pct = sum(v < cur for v in hist) / len(hist) * 100
        stress = pct if stress_up else 100 - pct
        qlo, qhi = B.percentile(hist, max(0, pct - 5)), B.percentile(hist, min(100, pct + 5))
        st = stats([o and qlo <= v <= qhi for o, v in zip(ok, a)])
        zone = "Extreme stress" if stress >= 95 else "High stress" if stress >= 80 else "Elevated" if stress >= 60 else "Normal" if stress >= 20 else "Calm"
        # stress percentile over the last three years, weekly, ranked against the full history
        ranked = sorted(hist)

        recent = []
        for i in range(max(0, T - 756), T, 5):
            v = a[i]
            if isnum(v):
                pc = bisect.bisect_left(ranked, v) / len(ranked) * 100
                recent.append([cal[i].isoformat(), r(v, 2), r(pc if stress_up else 100 - pc, 0)])
        if recent and recent[-1][0] != cal[-1].isoformat():
            recent.append([cal[-1].isoformat(), r(cur, 2), r(stress, 0)])
        rows.append({"gauge": gid, "recent": recent, "label": label, "meaning": meaning, "current": r(cur, 2), "unit": unit,
                     "since": cal[ok.index(True)].year, "percentile": r(pct, 0), "stress_pct": r(stress, 0), "zone": zone,
                     "median_hist": r(statistics.median(hist), 2), "p10_hist": r(B.percentile(hist, 10), 2), "p90_hist": r(B.percentile(hist, 90), 2),
                     **st, "vs_normal_3m": r(st["fwd3m_median"] - base["fwd3m_median"]) if st["fwd3m_median"] is not None else None})

    has_vix = [isnum(v) for v in vix]
    off = [isnum(d) and -20 <= d <= -8 for d in dd]
    v30 = [h and v >= 30 for h, v in zip(has_vix, vix)]
    setups = [
        ("All days since 1950 (normal)", [True] * T),
        ("VIX 30 or higher", v30),
        ("S&P 8–20% below its high", off),
        ("Today's setup: VIX 30+ and S&P 8–20% off its high", [a and b for a, b in zip(v30, off)]),
    ]
    if fred["BAA10Y"]:
        setups += [
            ("Same, but credit spreads still calm (Baa < 2.5)", [a and b and isnum(c) and c < 2.5 for a, b, c in zip(v30, off, baa)]),
            ("Same, with credit stress (Baa ≥ 2.5)", [a and b and isnum(c) and c >= 2.5 for a, b, c in zip(v30, off, baa)]),
        ]
    setups.append(("S&P down 10%+ in one month", [isnum(x) and x <= -10 for x in ret1m]))
    PATH_DAYS = list(range(0, 253, 7))

    def path(mask: list[bool]) -> dict:
        idx = [i for i, m in enumerate(mask) if m and i + 252 < T]
        if not idx:
            return {}
        cols = [[px[i + k] / px[i] - 1 for i in idx] for k in PATH_DAYS]
        return {"days": PATH_DAYS, "median": [r(statistics.median(c) * 100) for c in cols],
                "p10": [r(B.percentile(c, 10) * 100) for c in cols], "p90": [r(B.percentile(c, 90) * 100) for c in cols]}

    setup_rows = [{"setup": name, "today_fits": bool(mask[-1]), **stats(mask), "path": path(mask)} for name, mask in setups]

    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {"rows": [{"asof": cal[-1].isoformat(), "gauges": rows, "setups": setup_rows}],
               "meta": {"generated_at": generated, "price_asof": cal[-1].isoformat(),
                        "sources": ["Yahoo Finance ^GSPC since 1950, ^VIX since 1990, ^VIX3M since 2006, ^NDX since 1985, BTC-USD since 2014",
                                    "FRED BAA10Y, T10Y2Y, DGS10"],
                        "method": "Percentile = share of past days with a lower reading. Forward returns are S&P 500 price changes after past days "
                                  "whose reading fell within ±5 percentile points of today's. Episodes start after a gap of more than 21 trading days."}}
    OUT.write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"market history: {len(rows)} gauges, {len(setup_rows)} setups through {cal[-1]}")
    for x in rows:
        print(f"  {x['label']}: {x['current']} pct {x['percentile']} {x['zone']} | {x['episodes']} ep, 3m {x['fwd3m_median']}, 12m {x['fwd12m_median']}")
    for x in setup_rows:
        print(f"  {'*' if x['today_fits'] else ' '} {x['setup']}: {x['episodes']} ep, 3m {x['fwd3m_median']} (p10 {x['fwd3m_p10']}), 12m {x['fwd12m_median']} up {x['fwd12m_pos_pct']}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
