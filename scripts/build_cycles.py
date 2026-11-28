#!/usr/bin/env python3
"""Century view: 150 years of S&P 500 cycles, every bear market since 1871, and the past months that looked most like
today, with what followed them, graded walk-forward.

Writes data/cycles.json. Standard library only. Sources: Robert Shiller's monthly data (price, dividends, earnings,
CPI, 10-year rate, CAPE; read from the cache build_valuation.py keeps, or fetched), FRED BAA (Moody's Baa corporate
yield, from 1919), FEDFUNDS (from 1954), USREC (NBER recessions).

Method
  - Monthly data. Real total return = Shiller's CPI-deflated price with dividends reinvested.
  - Bear markets: every fall of 20% or more in the monthly S&P price from a prior high, until it made a new high.
    Shiller's prices are monthly averages, so intramonth lows (October 1987, March 2009) were deeper than shown.
  - Today's configuration: 7 readings fixed in advance (not tuned): CAPE (log), 10-year rate, its 12-month change,
    inflation over 12 months, Baa minus 10-year (credit stress), 12-month price change, distance below the prior high.
    Each is turned into a z-score with the mean and spread known at the time.
  - Analogs: the 7 nearest past months by distance, at least 24 months apart (separate episodes), whose outcome is known.
    What followed = their next 1, 3 and 5 years' real total return.
  - Walk-forward test: from 1950, each month is forecast from analogs using only data known then; the forecast is the
    median of the analogs. It is graded against the plain average of all past outcomes (score above 0 = analogs helped).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_valuation as bv  # noqa: E402
from build_strategies import fred  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "cycles.json"
FEATS = [("cape", "CAPE (price / 10-year real earnings)"), ("y10", "10-year Treasury rate"), ("dy10", "Change in the 10-year rate over 12 months"),
         ("infl", "Inflation over 12 months"), ("credit", "Baa corporate minus 10-year rate"), ("mom", "S&P price change over 12 months"),
         ("dd", "Distance below the prior high")]
HZ = {"1y": 12, "3y": 36, "5y": 60}
K, GAP = 7, 24


def load_shiller() -> list[dict]:
    path = bv.CACHE if bv.CACHE.exists() else bv.fetch()
    if not path:
        return []
    c = bv.xls_lite.read_sheet(str(path), "Data")
    hdr = {c.get((7, k)): k for k in range(30) if isinstance(c.get((7, k)), str)}
    trk = next(k for k in range(30) if c.get((7, k)) == "Price" and c.get((6, k)) == "Return")
    rows = []
    for r in range(8, max(r for r, _ in c) + 1):
        d, p, cpi, tr = c.get((r, hdr["Date"])), c.get((r, hdr["P"])), c.get((r, hdr["CPI"])), c.get((r, trk))
        if not all(isinstance(x, float) for x in (d, p, cpi, tr)):
            continue
        cape, gs10 = c.get((r, hdr["CAPE"])), c.get((r, hdr["Rate GS10"]))
        rows.append({"m": bv.ym(d), "p": p, "cpi": cpi, "tr": tr, "cape": cape if isinstance(cape, float) and cape > 0 else None,
                     "y10": gs10 if isinstance(gs10, float) and gs10 > 0 else None})
    return rows


def build(rows: list[dict], baa: dict, ff: dict, rec: dict) -> dict:
    hi = 0.0
    for i, r in enumerate(rows):
        hi = max(hi, r["p"])
        r["dd"] = (r["p"] / hi - 1) * 100
        r["mom"] = (r["p"] / rows[i - 12]["p"] - 1) * 100 if i >= 12 else None
        r["infl"] = (r["cpi"] / rows[i - 12]["cpi"] - 1) * 100 if i >= 12 else None
        r["dy10"] = r["y10"] - rows[i - 12]["y10"] if i >= 12 and r["y10"] is not None and rows[i - 12]["y10"] is not None else None
        r["credit"] = baa[r["m"]] - r["y10"] if r["m"] in baa and r["y10"] is not None else None
        r["ff"], r["rec"] = ff.get(r["m"]), rec.get(r["m"])
        for h, n in HZ.items():
            r["f" + h] = ((rows[i + n]["tr"] / r["tr"]) ** (12 / n) - 1) * 100 if i + n < len(rows) else None
    # Bear markets: monthly prices before 1950, daily closes from 1950
    bears, current = all_bears(rows)
    return finish(rows, bears, current)


def find_bears(v: list[float]) -> list[tuple[int, int, int | None, bool]]:
    """(peak, trough, recovered, ongoing) indexes. A bear market is a fall of 20% or more from the highest close since the
    last bear market; it ends at its low once prices win back half of the loss (so a sharp bear-market rally, like late
    2008 or early 2002, does not split one bear in two). Recovered = the first close back at the old peak."""
    out, pk, i = [], 0, 1
    while i < len(v):
        if v[i] >= v[pk]:
            pk = i
        elif v[i] <= 0.8 * v[pk]:
            lo, j = i, i
            while j < len(v) and v[j] < (v[pk] + v[lo]) / 2:
                if v[j] < v[lo]:
                    lo = j
                j += 1
            rec = next((k for k in range(lo, len(v)) if v[k] >= v[pk]), None)
            out.append((pk, lo, rec, j >= len(v)))
            if j >= len(v):
                break
            pk, i = j, j
        i += 1
    return out


def _bear_row(rows_by_m: dict, peak, trough, rec, ongoing, pv: float, tv: float, basis: str) -> dict:
    mon = lambda a, b: (int(b[:4]) - int(a[:4])) * 12 + int(b[5:7]) - int(a[5:7])
    a, t = rows_by_m.get(peak[:7], {}), rows_by_m.get(trough[:7], {})
    months = [f"{y}-{m:02d}" for y in range(int(peak[:4]), int(trough[:4]) + 1) for m in range(1, 13)]
    return {"peak": peak, "trough": trough, "depth_pct": round((tv / pv - 1) * 100, 1),
            "real_tr_pct": round((t["tr"] / a["tr"] - 1) * 100, 1) if a and t else None,
            "months_down": mon(peak, trough), "months_to_recover": mon(peak, rec) if rec else None, "ongoing": ongoing,
            "recession": any((rows_by_m.get(m) or {}).get("rec") for m in months if peak[:7] <= m <= trough[:7]),
            "at_peak": {"cape": round(a["cape"], 1) if a.get("cape") else None, "y10": round(a["y10"], 2) if a.get("y10") is not None else None,
                        "infl": round(a["infl"], 1) if a.get("infl") is not None else None}, "basis": basis}


def all_bears(rows: list[dict]) -> tuple[list[dict], dict | None]:
    """Monthly averages before 1950 (recovery searched on the full monthly record); daily closes from 1950."""
    from build_strategies import daily
    by_m = {r["m"]: r for r in rows}
    early = [r for r in rows if r["m"] < "1950-01"]
    out = []
    for pk, lo, _, _ in find_bears([r["p"] for r in early]):
        rec = next((r["m"] for r in rows[lo:] if r["p"] >= early[pk]["p"]), None)
        out.append(_bear_row(by_m, early[pk]["m"], early[lo]["m"], rec, False, early[pk]["p"], early[lo]["p"], "monthly average"))
    px = dict(daily("_GSPC_long1950")); px.update(daily("_GSPC"))
    ds = sorted(d for d in px if d >= dt.date(1950, 1, 1))
    if len(ds) < 5000:
        return out, None
    v = [px[d] for d in ds]
    for pk, lo, rec, ongoing in find_bears(v):
        out.append(_bear_row(by_m, ds[pk].isoformat(), ds[lo].isoformat(), ds[rec].isoformat() if rec is not None else None, ongoing, v[pk], v[lo], "daily close"))
    hi = max(v); hd = ds[max(i for i, x in enumerate(v) if x == hi)]
    return out, {"asof": ds[-1].isoformat(), "close": round(v[-1], 2), "high": round(hi, 2), "high_date": hd.isoformat(), "dd_pct": round((v[-1] / hi - 1) * 100, 1)}


def finish(rows: list[dict], bears: list[dict], current: dict | None) -> dict:
    feat = lambda r: None if any(r.get(k) is None for k, _ in FEATS) else [math.log(r["cape"]) if k == "cape" else r[k] for k, _ in FEATS]

    def analogs(t: int, h: str | None, k: int = K) -> list[tuple[float, int]]:
        x0 = feat(rows[t])
        if x0 is None:
            return []
        n = HZ[h] if h else 0
        past = [(i, feat(rows[i])) for i in range(t - n) if feat(rows[i]) is not None]  # outcome known by t
        hist = [f for _, f in [(i, feat(rows[i])) for i in range(t + 1)] if f is not None]
        if len(past) < 120:
            return []
        mu = [statistics.fmean(c) for c in zip(*hist)]; sd = [statistics.pstdev(c) or 1 for c in zip(*hist)]
        dist = sorted((math.sqrt(sum(((a - m) / s) ** 2 for a, m, s in zip(f, mu, sd))), i) for i, f in past)
        pick = []
        for dd_, i in dist:
            if all(abs(i - j) >= GAP for _, j in pick):
                pick.append((dd_, i))
            if len(pick) == k:
                break
        return pick

    # Walk-forward grading from 1950
    test = {}
    for h, n in HZ.items():
        g = []
        for t in range(len(rows)):
            if rows[t]["m"] < "1950-01" or rows[t]["f" + h] is None or rows[t]["m"][5:] not in ("01", "04", "07", "10"):
                continue
            pk_ = analogs(t, h)
            if len(pk_) < K:
                continue
            fc = statistics.median(rows[i]["f" + h] for _, i in pk_)
            naive = statistics.fmean(rows[i]["f" + h] for i in range(t - n) if rows[i]["f" + h] is not None)
            g.append((fc, naive, rows[t]["f" + h], rows[t]["m"]))
        sse = sum((a - f) ** 2 for f, _, a, _ in g); ssn = sum((a - nv) ** 2 for _, nv, a, _ in g)
        lo_ = [x for x in g if x[0] < x[1]]; hi_ = [x for x in g if x[0] >= x[1]]
        test[h] = {"from": g[0][3], "to": g[-1][3], "n": len(g), "score": round(1 - sse / ssn, 2),
                   "median_abs_miss": round(statistics.median(abs(a - f) for f, _, a, _ in g), 1),
                   "when_below_avg": round(statistics.fmean(a for *_, a, _ in [(x[0], x[1], x[2], x[3]) for x in lo_]), 1) if lo_ else None,
                   "when_above_avg": round(statistics.fmean(x[2] for x in hi_), 1) if hi_ else None}
    # Today
    now = max(i for i, r in enumerate(rows) if feat(r) is not None)
    hist = [feat(r) for r in rows[:now + 1] if feat(r) is not None]
    today = []
    for j, (k, name) in enumerate(FEATS):
        col = sorted(f[j] for f in hist); v = feat(rows[now])[j]
        today.append({"key": k, "name": name, "value": round(rows[now][k], 2), "pctile": round(100 * sum(x < v for x in col) / len(col))})
    an = {}
    for h in HZ:
        pk_ = analogs(now, h)
        an[h] = [{"m": rows[i]["m"], "dist": round(d_, 2), "ret": round(rows[i]["f" + h], 1)} for d_, i in pk_]
    near = [{"m": rows[i]["m"], "dist": round(d_, 2), **{k: (round(rows[i][k], 1) if rows[i][k] is not None else None) for k, _ in FEATS},
             **{"f" + h: (round(rows[i]["f" + h], 1) if rows[i]["f" + h] is not None else None) for h in HZ}} for d_, i in analogs(now, None)]
    # Chart series: quarterly, real total return index rebased to 1 in 1871
    base = rows[0]["tr"]
    series = [{"m": r["m"], "tr": round(r["tr"] / base, 3), "p": round(r["p"], 2), "cape": round(r["cape"], 1) if r["cape"] else None,
               "y10": round(r["y10"], 2) if r["y10"] is not None else None, "infl": round(r["infl"], 1) if r["infl"] is not None else None,
               "credit": round(r["credit"], 2) if r["credit"] is not None else None, "ff": r["ff"], "rec": 1 if r["rec"] else 0}
              for r in rows[::3]]
    cur = rows[-1]
    return {"asof": rows[now]["m"], "price_asof": cur["m"], "now": current, "today": today, "near": near, "analogs": an,
            "test": test, "bears": bears, "series": series,
            "decades": decade_table(rows)}


def decade_table(rows: list[dict]) -> list[dict]:
    out = []
    for dec in range(1870, 2030, 10):
        rs = [r for r in rows if f"{dec}-01" <= r["m"] <= f"{dec + 9}-12"]
        if len(rs) < 24:
            continue
        a, b = rs[0], rs[-1]; yrs = len(rs) / 12
        out.append({"decade": f"{dec}s", "real_tr_pct": round(((b["tr"] / a["tr"]) ** (1 / yrs) - 1) * 100, 1),
                    "infl_pct": round(((b["cpi"] / a["cpi"]) ** (1 / yrs) - 1) * 100, 1),
                    "cape_start": round(a["cape"], 1) if a["cape"] else None, "partial": len(rs) < 120})
    return out


def main() -> int:
    rows = load_shiller()
    if len(rows) < 1500:
        print("::warning::Shiller data unavailable; keeping the last cycles.json")
        return 0 if OUT.exists() else 1
    baa, ff, rec = fred("BAA"), fred("FEDFUNDS"), fred("USREC")
    if not baa:
        print("::warning::FRED BAA unavailable; keeping the last cycles.json")
        return 0 if OUT.exists() else 1
    out = build(rows, baa, ff, rec)
    OUT.write_text(json.dumps({"rows": [out], "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                                                       "generator": "scripts/build_cycles.py",
                                                       "sources": ["Robert Shiller, shillerdata.com (ie_data.xls)", "FRED BAA, FEDFUNDS, USREC"],
                                                       "method": __doc__.split("Method", 1)[1].strip()}}, separators=(",", ":")))
    print("as of", out["asof"], "today:", [(x["key"], x["value"], x["pctile"]) for x in out["today"]])
    print("nearest:", [(x["m"], x["dist"], x["f1y"], x["f5y"]) for x in out["near"]])
    print("test:", out["test"])
    print("bears:", len(out["bears"]), [(b["peak"], b["depth_pct"], b["months_to_recover"]) for b in out["bears"]]); print("now", out["now"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
