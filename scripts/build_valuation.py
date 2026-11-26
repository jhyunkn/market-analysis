#!/usr/bin/env python3
"""Ten-year view from valuation: what the S&P 500's price relative to ten years of earnings (Shiller's CAPE) has implied
for the following ten years' returns after inflation, tested walk-forward so the forecast never sees its own future.

Writes data/valuation.json. Standard library only. Source: Robert Shiller's monthly data (shillerdata.com, ie_data.xls),
read with scripts/xls_lite.py.

Method
  - y = the next 10 years' real total return, a year (Shiller's own column, dividends reinvested, CPI-deflated).
  - x = 1 / CAPE (the cyclically adjusted earnings yield). The model is y = a + b * x, fit by least squares.
  - Walk-forward: the forecast for month t uses only start months whose 10-year outcome was known by t (start <= t - 120
    months), from 1881. Every month from 1921 is forecast that way and graded once its outcome is in. The error band is
    the 10th to 90th percentile of those out-of-sample misses, added to today's forecast.
  - The same test for a "know nothing" forecast (the average 10-year return to date) shows whether valuation added
    anything. Overlapping 10-year windows mean far fewer independent observations than months (about 14 since 1881).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import statistics
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import xls_lite  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "valuation.json"
CACHE = ROOT / ".cache" / "market-data" / "shiller_ie_data.xls"
PAGE = "https://shillerdata.com/"
UA = "market-analysis-build/1.0 (+https://github.com/jhyunkn/market-analysis)"


def fetch() -> Path | None:
    try:
        html = urllib.request.urlopen(urllib.request.Request(PAGE, headers={"User-Agent": UA}), timeout=40).read().decode("utf-8", "ignore")
        m = re.search(r'href="((?:https:)?//[^"]+/ie_data\.xls[^"]*)"', html)
        if not m:
            raise ValueError("ie_data.xls link not found")
        url = m.group(1) if m.group(1).startswith("http") else "https:" + m.group(1)
        data = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=90).read()
        if data[:8] != bytes.fromhex("d0cf11e0a1b11ae1"):
            raise ValueError("not an xls file")
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_bytes(data)
    except Exception as e:  # keep the last good copy
        print(f"::warning::Shiller data fetch failed ({e}); using cache" if CACHE.exists() else f"::warning::Shiller data fetch failed ({e})")
    return CACHE if CACHE.exists() else None


def ym(v: float) -> str:
    y = int(v); m = round((v - y) * 100)
    return f"{y}-{m:02d}"


def load(path: Path) -> list[dict]:
    c = xls_lite.read_sheet(str(path), "Data")
    hdr = {c.get((7, k)): k for k in range(30) if isinstance(c.get((7, k)), str)}
    need = {"date": hdr["Date"], "cape": hdr["CAPE"], "tr": next(k for k in range(30) if c.get((9 - 2, k)) == "Price" and c.get((6, k)) == "Return"), "p": hdr["P"]}
    rows = []
    for r in range(8, max(r for r, _ in c) + 1):
        d, cape, tr, p = (c.get((r, need[k])) for k in ("date", "cape", "tr", "p"))
        if not isinstance(d, float) or not isinstance(tr, float):
            continue
        rows.append({"m": ym(d), "cape": cape if isinstance(cape, float) and cape > 0 else None, "tr": tr, "p": p})
    for i, r in enumerate(rows):  # next 10 years' real total return, a year
        r["fwd"] = (rows[i + 120]["tr"] / r["tr"]) ** 0.1 - 1 if i + 120 < len(rows) else None
    return rows


def fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    return my - b * mx, b


def pctl(v: list[float], q: float) -> float:
    s = sorted(v); k = (len(s) - 1) * q; f = int(k)
    return s[f] + (s[min(f + 1, len(s) - 1)] - s[f]) * (k - f)


def main() -> int:
    path = fetch()
    if not path:
        return 0 if OUT.exists() else 1
    rows = load(path)
    usable = [r for r in rows if r["cape"]]
    graded, series = [], []
    for i, r in enumerate(rows):
        if not r["cape"] or r["m"] < "1921-01":
            continue
        known = [q for q in rows[:max(i - 119, 0)] if q["cape"] and q["fwd"] is not None]
        if len(known) < 240:
            continue
        a, b = fit([1 / q["cape"] for q in known], [q["fwd"] for q in known])
        pred, naive = a + b / r["cape"], statistics.fmean(q["fwd"] for q in known)
        series.append({"m": r["m"], "pred": round(pred * 100, 2), "actual": round(r["fwd"] * 100, 2) if r["fwd"] is not None else None})
        if r["fwd"] is not None:
            graded.append((pred, naive, r["fwd"], r["m"]))
    miss = [g[2] - g[0] for g in graded]
    sse = sum((g[2] - g[0]) ** 2 for g in graded); sse_n = sum((g[2] - g[1]) ** 2 for g in graded)
    now = usable[-1]; nowi = rows.index(now)
    known = [q for q in rows[:nowi - 119] if q["cape"] and q["fwd"] is not None]
    a, b = fit([1 / q["cape"] for q in known], [q["fwd"] for q in known])
    pred = a + b / now["cape"]
    xs, ys = [1 / q["cape"] for q in known], [q["fwd"] for q in known]
    fa, fb = fit(xs, ys); my = statistics.fmean(ys)
    r2_in = 1 - sum((y - fa - fb * x) ** 2 for x, y in zip(xs, ys)) / sum((y - my) ** 2 for y in ys)
    # Same walk-forward score per era, to show whether it held up
    eras = {}
    for lo_, hi_ in (("1921-01", "1959-12"), ("1960-01", "1989-12"), ("1990-01", "2099-12")):
        g = [x for x in graded if lo_ <= x[3] <= hi_]
        if g:
            eras[f"{g[0][3][:4]}-{g[-1][3][:4]}"] = round(1 - sum((x[2] - x[0]) ** 2 for x in g) / sum((x[2] - x[1]) ** 2 for x in g), 2)
    lo, hi = pred + pctl(miss, 0.10), pred + pctl(miss, 0.90)
    # What actually followed when CAPE was at least as high as now (few independent episodes)
    hiv = [q for q in rows if q["cape"] and q["fwd"] is not None and q["cape"] >= now["cape"] * 0.9]
    episodes = sorted({q["m"][:4] for q in hiv})
    out = {
        "asof": now["m"], "cape": round(now["cape"], 1),
        "cape_median": round(statistics.median(q["cape"] for q in usable), 1),
        "cape_pctile": round(100 * sum(q["cape"] < now["cape"] for q in usable) / len(usable)),
        "cape_max": max(({"m": q["m"], "cape": round(q["cape"], 1)} for q in usable), key=lambda z: z["cape"]),
        "forecast_real_pct": round(pred * 100, 1), "band_real_pct": [round(lo * 100, 1), round(hi * 100, 1)],
        "test": {"from": graded[0][3], "to": graded[-1][3], "months": len(graded), "independent_decades": round(len(graded) / 120, 1),
                 "r2_oos": round(1 - sse / sse_n, 2), "r2_in_sample": round(r2_in, 2), "r2_oos_by_era": eras, "median_abs_miss_pct": round(statistics.median(abs(m) for m in miss) * 100, 1),
                 "in_band_pct": round(100 * sum(pctl(miss, 0.10) <= m <= pctl(miss, 0.90) for m in miss) / len(miss)),
},
        "similar": {"threshold": round(now["cape"] * 0.9, 1), "months": len(hiv), "years": episodes,
                    "median_pct": round(statistics.median(q["fwd"] for q in hiv) * 100, 1) if hiv else None,
                    "worst_pct": round(min(q["fwd"] for q in hiv) * 100, 1) if hiv else None,
                    "best_pct": round(max(q["fwd"] for q in hiv) * 100, 1) if hiv else None},
        "series": series[::3],
    }
    OUT.write_text(json.dumps({"rows": [out], "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                                       "generator": "scripts/build_valuation.py", "sources": ["Robert Shiller, shillerdata.com (ie_data.xls)"],
                                                       "method": __doc__.split("Method", 1)[1].strip()}}, separators=(",", ":")))
    t = out["test"]
    print(f"CAPE {out['cape']} ({out['asof']}, {out['cape_pctile']}th pct) -> {out['forecast_real_pct']}% real/yr, band {out['band_real_pct']}; "
          f"OOS R2 {t['r2_oos']} (in-sample {t['r2_in_sample']}, eras {t['r2_oos_by_era']}) over {t['from']}..{t['to']}, median miss {t['median_abs_miss_pct']}; similar {out['similar']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
