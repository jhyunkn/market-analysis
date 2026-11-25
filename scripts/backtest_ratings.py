#!/usr/bin/env python3
"""Point-in-time test of the long-term business checks, from SEC filings. Writes data/ratings_backtest.json.

At every month end since 2017, each stock is scored on the five business checks that SEC filings allow us to rebuild
as they stood on that date (only 10-K figures already filed by then): growth, profits, margins, debt and gross
profitability. Stocks passing every check form one equal-weight portfolio, those missing one another; each is held
for the next month and compared with all the rated stocks held equally, and with SPY.

Not tested, because their history is not freely available: the PEG, estimate and analyst checks, and the whole
short-term rating. Survivorship: the stock list is today's large companies, which flatters every group alike, so the
useful number is the gap between groups, not their raw returns.

Runs weekly (skips when the last result is under 6 days old; --force to rerun). Needs SEC_CONTACT (see sec_facts.py).
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_facts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "market-data"
OUT = DATA / "ratings_backtest.json"
SKIP_GROUPS = {"Market & sector ETFs", "Leveraged ETFs", "Crypto"}
NO_GP = {"Financials", "Real Estate"}
START = dt.date(2017, 1, 31)


def month_ends(prices: dict[dt.date, float]) -> list[dt.date]:
    days = sorted(prices)
    return [d for i, d in enumerate(days) if i + 1 == len(days) or days[i + 1].month != d.month]


def checks(f: dict, when: dt.date) -> dict | None:
    k = {item: sec_facts.known_at(f.get(item, []), when) for item in ("revenue", "net_income", "op_income", "gross_profit", "cost_of_revenue")}
    k1 = {item: sec_facts.known_at(f.get(item, []), when, 1) for item in ("assets", "lt_debt")}
    rev = k["revenue"]
    if len(rev) < 4 or (when - rev[0][0]).days > 550:  # need 4 fiscal years, the latest no older than ~18 months
        return None
    by = lambda item: {e: v for e, v in k[item]}  # noqa: E731
    ends = [e for e, _ in rev]
    r = [v for _, v in rev]
    ni, oi = by("net_income"), by("op_income")
    out: dict[str, bool | None] = {}
    out["growth"] = (all(v > 0 for v in r) and ((r[0] / r[3]) ** (1 / 3) - 1) * 100 >= 8 and sum(r[i] > r[i + 1] for i in range(3)) >= 2)
    nis = [ni.get(e) for e in ends[:3]]
    out["profits"] = all(v is not None and v > 0 for v in nis) if None not in nis else None
    om = [oi[e] / v * 100 if e in oi and v else None for e, v in rev[:3]]
    out["margins"] = (om[0] >= statistics.mean(om[1:3]) - 2) if None not in om else None
    ltd = k1["lt_debt"][0][1] if k1["lt_debt"] and abs((k1["lt_debt"][0][0] - ends[0]).days) < 40 else 0.0
    out["debt"] = (ltd <= 3 * nis[0]) if nis[0] is not None and nis[0] > 0 else (False if nis[0] is not None else None)
    gp = by("gross_profit").get(ends[0])
    if gp is None and ends[0] in by("cost_of_revenue"):
        gp = r[0] - by("cost_of_revenue")[ends[0]]
    assets = k1["assets"][0][1] if k1["assets"] and abs((k1["assets"][0][0] - ends[0]).days) < 40 else None
    out["_gpa"] = gp / assets if gp is not None and assets else None
    return out


def stats(rets: list[float]) -> dict:
    if not rets:
        return {"months": 0}
    grow = math.prod(1 + x for x in rets)
    peak, dd, v = 1.0, 0.0, 1.0
    for x in rets:
        v *= 1 + x
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    return {"months": len(rets), "ann_pct": round((grow ** (12 / len(rets)) - 1) * 100, 2), "worst_drawdown_pct": round(dd * 100, 1)}


def main() -> int:
    if OUT.exists() and "--force" not in sys.argv:
        try:
            age = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(json.loads(OUT.read_text())["meta"]["generated_at"].replace("Z", "+00:00"))).days
            if age < 6:
                print(f"ratings backtest: built {age} days ago; skipping")
                return 0
        except Exception:  # noqa: BLE001
            pass
    if not sec_facts.enabled():
        print("ratings backtest: SEC_CONTACT not set; skipping")
        return 0
    uni = [u for u in json.loads((ROOT / "config/universe.json").read_text())["symbols"] if u["group"] not in SKIP_GROUPS]
    cik = sec_facts.ciks()
    if not cik:
        print("ratings backtest: SEC ticker list unavailable; skipping")
        return 0
    facts, prices, group = {}, {}, {}
    for u in uni:
        s = u["symbol"]
        try:
            px = {dt.date.fromisoformat(k): v for k, v in json.loads((CACHE / f"{s}.json").read_text()).items()}
        except Exception:  # noqa: BLE001
            continue
        if s not in cik:
            continue
        f = sec_facts.annual(cik[s])
        if f.get("revenue"):
            facts[s], prices[s], group[s] = f, px, u["group"]
    spy = {dt.date.fromisoformat(k): v for k, v in json.loads((CACHE / "SPY.json").read_text()).items()}
    me = [d for d in month_ends(spy) if d >= START]
    groups = {"strong": [], "good": [], "rest": [], "all": [], "spy": []}
    counts = {"strong": [], "good": []}
    yearly: dict[int, dict[str, list[float]]] = {}

    def px_on(p: dict[dt.date, float], d: dt.date) -> float | None:
        for k in range(6):  # nearest earlier trading day
            v = p.get(d - dt.timedelta(days=k))
            if v:
                return v
        return None

    for d0, d1 in zip(me, me[1:]):
        scored = {}
        for s, f in facts.items():
            c = checks(f, d0)
            if c is not None:
                scored[s] = c
        gp = sorted(c["_gpa"] for s, c in scored.items() if c["_gpa"] is not None and group[s] not in NO_GP)
        cut = gp[int(len(gp) * 0.6)] if gp else None
        bucket: dict[str, list[float]] = {"strong": [], "good": [], "rest": [], "all": []}
        n_s = n_g = 0
        for s, c in scored.items():
            ch = {k: v for k, v in c.items() if not k.startswith("_")}
            if group[s] not in NO_GP:
                ch["quality"] = (c["_gpa"] >= cut) if c["_gpa"] is not None and cut is not None else None
            p0, p1 = px_on(prices[s], d0), px_on(prices[s], d1)
            if not p0 or not p1:
                continue
            r = p1 / p0 - 1
            miss = sum(v is not True for v in ch.values())
            key = "strong" if miss == 0 else "good" if miss == 1 else "rest"
            bucket[key].append(r)
            bucket["all"].append(r)
            n_s += key == "strong"; n_g += key == "good"
        if len(bucket["all"]) < 40:
            continue
        sp0, sp1 = px_on(spy, d0), px_on(spy, d1)
        row = {k: statistics.mean(v) if v else None for k, v in bucket.items()}
        row["spy"] = sp1 / sp0 - 1 if sp0 and sp1 else None
        for k, v in row.items():
            if v is not None:
                groups[k].append(v)
        counts["strong"].append(n_s); counts["good"].append(n_g)
        y = yearly.setdefault(d1.year, {k: [] for k in row})
        for k, v in row.items():
            if v is not None:
                y[k].append(v)
    if not groups["all"]:
        print("ratings backtest: no months could be scored")
        return 1
    res = {k: stats(v) for k, v in groups.items()}
    paired = [(a, b) for a, b in zip(groups["strong"], groups["all"])]
    res["strong"]["beat_all_months_pct"] = round(sum(a > b for a, b in paired) / len(paired) * 100) if paired else None
    for k in ("strong", "good"):
        ex = [x - b for x, b in zip(groups[k], groups["all"])]
        # t-statistic of the monthly gap to all rated stocks: roughly, above 2 is unlikely to be luck alone
        res[k]["t_stat"] = round(statistics.mean(ex) / statistics.stdev(ex) * math.sqrt(len(ex)), 2) if len(ex) > 2 and statistics.stdev(ex) > 0 else None
    res["strong"]["avg_stocks"] = round(statistics.mean(counts["strong"]), 1) if counts["strong"] else None
    res["good"]["avg_stocks"] = round(statistics.mean(counts["good"]), 1) if counts["good"] else None
    for k in ("strong", "good", "rest", "spy"):
        if res[k].get("ann_pct") is not None:
            res[k]["vs_all_pct"] = round(res[k]["ann_pct"] - res["all"]["ann_pct"], 2)
    years = [{"year": y, **{k: round((math.prod(1 + x for x in v) - 1) * 100, 1) if v else None for k, v in g.items()}} for y, g in sorted(yearly.items())]
    payload = {"rows": [{"summary": res, "years": years}],
               "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "generator": "scripts/backtest_ratings.py", "stocks": len(facts), "from": me[0].isoformat(), "to": me[-1].isoformat(),
                        "checks": ["growth", "profits", "margins", "debt", "quality"],
                        "not_tested": ["value (PEG)", "estimates", "analysts", "all short-term checks"],
                        "method": __doc__.split("\n", 2)[2].strip(), "sources": ["SEC EDGAR XBRL company facts (10-K, as filed)", "daily prices from this dashboard"]}}
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
    tmp.replace(OUT)
    s, a = res["strong"], res["all"]
    print(f"ratings backtest: {len(facts)} stocks, {s['months']} months; all-checks-pass {s.get('ann_pct')}% a year vs {a['ann_pct']}% for all rated "
          f"({s.get('vs_all_pct'):+}, t {s.get('t_stat')}), beat in {s.get('beat_all_months_pct')}% of months, ~{s.get('avg_stocks')} stocks; SPY {res['spy'].get('ann_pct')}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
