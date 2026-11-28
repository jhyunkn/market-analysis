#!/usr/bin/env python3
"""Stock ratings for two kinds of investor, from stated rules. Writes data/ratings.json and data/ratings_log.json.

Run after build_data.py (reads data/outlook_summary.json, data/news.json and the daily price cache).
Standard library only.

Long-term (holding for years), 8 checks on the business, its price for that growth, and analyst support
(7 for banks, insurers, payment networks and property trusts, where gross profit is not meaningful):
  growth      sales up 8%+ a year over the last 3 fiscal years, and up in at least 2 of those 3 years
  quality     gross profit / total assets in the top 40% of these stocks (Novy-Marx 2013: the strongest single quality
              measure in his tests); not applied to Financials and Real Estate
  profits     net income positive in each of the last 3 fiscal years
  margins     latest operating margin no more than 2 points below the average of the two years before
  debt        long-term debt no more than 3x the latest year's net income
  value       forward PEG ratio (price/earnings over the next 12 months divided by forecast growth) between 0 and 2
  estimates   next fiscal year's consensus EPS above this year's, and more analysts raised than cut it in the last 4 weeks
  analysts    60%+ of analysts rate it Buy
Short-term (weeks to about 3 months), 6 checks on price behaviour and fresh news on earnings:
  trend       price above its 50- and 200-day averages, 50-day above 200-day
  momentum    12-month return (skipping the latest month) in the top 40% of these stocks
  strength    beat the S&P 500 (SPY) over the past month
  estimates   this year's consensus EPS higher than a month ago, more raised than cut
  beat        the last report beat the EPS estimate and came within the last ~10 weeks (the post-earnings drift window)
  calm        no earnings report due in the next 10 days (a report can move the price either way)
A stock is flagged "cyclical" when its operating margin swung more than 25 points across the 4 years or it lost money in
one of them: its growth may be a rebound from a bad year rather than steady growth.
Ratings: Strong = every check passes, Good = all but one, Mixed = at least half, Weak = fewer than half.
Research behind the checks: earnings-estimate revisions and post-earnings drift (Chan, Jegadeesh and Lakonishok 1996;
Bernard and Thomas 1989), momentum (Jegadeesh and Titman 1993), quality and profitability (Novy-Marx 2013). This
combination has not been tested here; the live log below grades every Strong list against SPY from 2026-11 on.
"""
from __future__ import annotations

import concurrent.futures as cf
import datetime as dt
import json
import os
import re
import statistics
import time
import urllib.parse
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import sec_facts  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "market-data"
NOW = dt.datetime.now(dt.timezone.utc)
TODAY = NOW.date()
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)", "Accept": "application/json", "Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
SKIP_GROUPS = {"Market & sector ETFs", "Leveraged ETFs", "Crypto"}
REFETCH_DAYS = 3


def get(url: str) -> dict | None:
    for a in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=25) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (a + 1))
    return None


def num(v) -> float | None:
    if v in (None, "", "--", "N/A", "NA"):
        return None
    try:
        s = str(v).replace("$", "").replace(",", "").replace("%", "").strip()
        neg = s.startswith("(") and s.endswith(")")
        x = float(s.strip("()"))
        return -x if neg else x
    except ValueError:
        return None


def fetch(sym: str) -> dict:
    """Fundamentals from Nasdaq's public pages; any piece that fails is left out."""
    s = urllib.parse.quote(sym.replace("-", "."))  # Nasdaq writes class shares with a dot (BRK.B)
    f: dict = {"fetched": TODAY.isoformat()}
    fi = get(f"https://api.nasdaq.com/api/company/{s}/financials?frequency=1")
    try:
        d = fi["data"]
        inc = {r["value1"]: r for r in d["incomeStatementTable"]["rows"]}
        bal = {r["value1"]: r for r in d["balanceSheetTable"]["rows"]}
        rat = {r["value1"]: r for r in d["financialRatiosTable"]["rows"]}
        cols = ["value2", "value3", "value4", "value5"]
        rev = [num(inc.get("Total Revenue", {}).get(c)) for c in cols]
        ni = [num(inc.get("Net Income", {}).get(c)) for c in cols]
        om = [num(rat.get("Operating Margin", {}).get(c)) for c in cols]
        f["fy_end"] = d["incomeStatementTable"]["headers"]["value2"]
        f["revenue"] = rev; f["net_income"] = ni; f["op_margin"] = om
        f["lt_debt"] = num(bal.get("Long-Term Debt", {}).get("value2"))
        f["gross_profit"] = num(inc.get("Gross Profit", {}).get("value2"))
        f["assets"] = num(bal.get("Total Assets", {}).get("value2"))
    except Exception:  # noqa: BLE001
        pass
    es = get(f"https://api.nasdaq.com/api/company/{s}/earnings-surprise")
    try:
        rows = es["data"]["earningsSurpriseTable"]["rows"][:4]
        f["surprises"] = [{"date": r.get("dateReported"), "pct": num(r.get("percentageSurprise"))} for r in rows]
    except Exception:  # noqa: BLE001
        pass
    ef = get(f"https://api.nasdaq.com/api/analyst/{s}/earnings-forecast")
    try:
        yr = ef["data"]["yearlyForecast"]["rows"][:2]
        f["eps_fy"] = [num(r.get("consensusEPSForecast")) for r in yr]
        f["fy_up"] = [int(num(r.get("up")) or 0) for r in yr]
        f["fy_down"] = [int(num(r.get("down")) or 0) for r in yr]
    except Exception:  # noqa: BLE001
        pass
    em = get(f"https://api.nasdaq.com/api/analyst/{s}/estimate-momentum")
    try:
        c = em["data"]["changeInConsensus"]
        cur, mon = num(c["currentData"]["yrMean"]), num(c["monthData"]["yrMean"])
        if cur is not None and mon:
            f["est_1m_pct"] = round((cur / mon - 1) * 100 * (1 if mon > 0 else -1), 2)
    except Exception:  # noqa: BLE001
        pass
    pg = get(f"https://api.nasdaq.com/api/analyst/{s}/peg-ratio")
    try:
        f["peg"] = num(pg["data"]["pegr"]["pegValue"])
    except Exception:  # noqa: BLE001
        pass
    return f


def chk(v) -> bool | None:
    return None if v is None else bool(v)


def rate(passed: int, total: int, known: int) -> str:
    if known < total - 2:
        return "Not enough data"
    miss = total - passed
    return "Strong" if miss == 0 else "Good" if miss == 1 else "Mixed" if passed >= total / 2 else "Weak"


def daily(sym: str) -> dict[str, float]:
    p = CACHE / f"{sym}.json"
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


def main() -> int:
    summ = {r["symbol"]: r for r in json.loads((DATA / "outlook_summary.json").read_text())["rows"]}
    news = {}
    try:
        news = {r["symbol"]: r for r in json.loads((DATA / "news.json").read_text())["rows"]}
    except Exception:  # noqa: BLE001
        pass
    uni = json.loads((ROOT / "config/universe.json").read_text())["symbols"]
    stocks = [u for u in uni if u["group"] not in SKIP_GROUPS and u["symbol"] in summ]
    price_asof = json.loads((DATA / "outlook_summary.json").read_text())["meta"].get("price_asof")
    # Fundamentals: reuse anything fetched within REFETCH_DAYS; refetch the oldest third or so each run.
    prev = {}
    try:
        prev = {r["symbol"]: r.get("fund", {}) for r in json.loads((DATA / "ratings.json").read_text())["rows"]}
    except Exception:  # noqa: BLE001
        pass
    stale = [u["symbol"] for u in stocks if (TODAY - dt.date.fromisoformat(prev.get(u["symbol"], {}).get("fetched", "2000-01-01"))).days >= REFETCH_DAYS]
    have = sum(1 for u in stocks if prev.get(u["symbol"], {}).get("revenue"))
    if have >= 0.7 * len(stocks):  # steady state: spread the refresh over several days
        stale = sorted(stale, key=lambda s: prev.get(s, {}).get("fetched", ""))[: max(50, len(stocks) // 3 + 1)]
    # Stocks whose financial statements failed to load are retried on every run (Nasdaq drops requests at random).
    stale += [u["symbol"] for u in stocks if (not prev.get(u["symbol"], {}).get("revenue") or "assets" not in prev.get(u["symbol"], {})) and u["symbol"] not in stale]
    fund = dict(prev)
    with cf.ThreadPoolExecutor(4) as ex:
        for s, f in zip(stale, ex.map(fetch, stale)):
            if len(f) > 1:
                old = prev.get(s, {})
                # a partial failure never erases good data: keep the old statements and their date, so it is retried next run
                fund[s] = {**old, **f, "fetched": old["fetched"]} if old.get("revenue") and not f.get("revenue") else f
    got = sum(1 for s in stale if fund.get(s, {}).get("fetched") == TODAY.isoformat())
    # Backup: statements from SEC filings for stocks whose Nasdaq statements still failed (needs SEC_CONTACT).
    sec_used = []
    gaps = [u["symbol"] for u in stocks if not fund.get(u["symbol"], {}).get("revenue") or fund[u["symbol"]].get("assets") is None]
    if gaps and sec_facts.enabled():
        cik = sec_facts.ciks()
        for s in gaps:
            if s not in cik:
                continue
            a = sec_facts.annual(cik[s])
            k = {item: sec_facts.known_at(a.get(item, []), TODAY) for item in ("revenue", "net_income", "op_income", "gross_profit", "cost_of_revenue")}
            rev = k["revenue"]
            if len(rev) < 4 or (TODAY - rev[0][0]).days > 550:
                continue
            by = {item: dict(k[item]) for item in k}
            ends = [e for e, _ in rev]
            one = lambda item: next((v for e, v in sec_facts.known_at(a.get(item, []), TODAY, 1) if abs((e - ends[0]).days) < 40), None)  # noqa: E731
            gp = by["gross_profit"].get(ends[0])
            if gp is None and ends[0] in by["cost_of_revenue"]:
                gp = rev[0][1] - by["cost_of_revenue"][ends[0]]
            f = dict(fund.get(s, {}))
            f.update({"revenue": [v / 1000 for _, v in rev], "net_income": [by["net_income"].get(e, 0) / 1000 if e in by["net_income"] else None for e in ends],
                      "op_margin": [by["op_income"][e] / v * 100 if e in by["op_income"] and v else None for e, v in rev],
                      "lt_debt": (one("lt_debt") or 0) / 1000, "assets": (one("assets") or 0) / 1000 or None,
                      "gross_profit": gp / 1000 if gp is not None else None, "fy_end": ends[0].strftime("%-m/%-d/%Y"), "statements_from": "SEC filings"})
            f.setdefault("fetched", TODAY.isoformat())
            fund[s] = f
            sec_used.append(s)
    spy1m = summ.get("SPY", {}).get("ret_1m")
    moms = sorted(summ[u["symbol"]]["mom12_1"] for u in stocks if summ[u["symbol"]].get("mom12_1") is not None)
    cut = moms[int(len(moms) * 0.6)] if moms else 0
    NO_GP = {"Financials", "Real Estate"}
    gpa = {u["symbol"]: fund[u["symbol"]]["gross_profit"] / fund[u["symbol"]]["assets"] for u in stocks
           if u["group"] not in NO_GP and fund.get(u["symbol"], {}).get("gross_profit") is not None and (fund[u["symbol"]].get("assets") or 0) > 0}
    gcut = sorted(gpa.values())[int(len(gpa) * 0.6)] if gpa else None
    rows = []
    for u in stocks:
        s = u["symbol"]; sm = summ[s]; f = fund.get(s, {}); nw = (news.get(s) or {}).get("earnings") or {}
        rev, ni, om = f.get("revenue") or [], f.get("net_income") or [], f.get("op_margin") or []
        margin_kind = "operating"
        if om and all(v in (0, None) for v in om[:3]) and any(v for v in ni[:3]):
            # Banks and card lenders often report no operating income line (Nasdaq shows 0%): use net margin instead.
            om = [ni[i] / rev[i] * 100 if i < len(ni) and ni[i] is not None and rev[i] else None for i in range(len(rev))]
            margin_kind = "net"
        cagr = ((rev[0] / rev[3]) ** (1 / 3) - 1) * 100 if len(rev) == 4 and all(v and v > 0 for v in rev) else None
        lt = {
            "growth": chk(cagr >= 8 and sum(rev[i] > rev[i + 1] for i in range(3)) >= 2) if cagr is not None else None,
            "profits": chk(all(v is not None and v > 0 for v in ni[:3])) if len(ni) >= 3 and all(v is not None for v in ni[:3]) else None,
            "margins": chk(om[0] >= statistics.mean(om[1:3]) - 2) if len(om) >= 3 and all(v is not None for v in om[:3]) else None,
            "debt": chk((f.get("lt_debt") or 0) <= 3 * ni[0]) if ni and ni[0] is not None and ni[0] > 0 else (False if ni and ni[0] is not None else None),
            "value": chk(f["peg"] is not None and 0 < f["peg"] <= 2) if "peg" in f and f["peg"] is not None else None,
            "estimates": chk(f["eps_fy"][1] > f["eps_fy"][0] and f["fy_up"][1] >= f["fy_down"][1] and f["fy_up"][0] >= f["fy_down"][0]) if len(f.get("eps_fy") or []) == 2 and None not in f["eps_fy"] else None,
            "analysts": chk((sm.get("buy_pct") or 0) >= 60) if sm.get("buy_pct") is not None and not sm.get("analyst_stale") else None,
        }
        if u["group"] not in NO_GP:
            lt["quality"] = chk(gpa[s] >= gcut) if s in gpa and gcut is not None else None
        oms = [v for v in om if v is not None]
        cyclical = (len(oms) >= 3 and max(oms) - min(oms) > 25) or any(v is not None and v < 0 for v in ni)
        sur = f.get("surprises") or []
        last = sur[0] if sur else {}
        last_date = None
        try:
            last_date = dt.datetime.strptime(last.get("date") or "", "%m/%d/%Y").date()
        except ValueError:
            pass
        nxt = nw.get("date")
        days_to = (dt.date.fromisoformat(nxt) - TODAY).days if nxt else None
        st = {
            "trend": chk(sm["price"] > sm["ma50"] > sm["ma200"]) if sm.get("ma50") and sm.get("ma200") else None,
            "momentum": chk(sm["mom12_1"] >= cut and sm["mom12_1"] > 0) if sm.get("mom12_1") is not None else None,
            "strength": chk(sm["ret_1m"] > spy1m) if sm.get("ret_1m") is not None and spy1m is not None else None,
            "estimates": chk(f["est_1m_pct"] > 0 and f["fy_up"][0] > f["fy_down"][0]) if f.get("est_1m_pct") is not None and f.get("fy_up") else None,
            "beat": chk(last.get("pct") is not None and last["pct"] > 0 and last_date is not None and (TODAY - last_date).days <= 70) if last else None,
            "calm": chk(days_to is None or days_to < 0 or days_to > 10) if nxt or nw else None,
        }
        lp, sp = sum(v is True for v in lt.values()), sum(v is True for v in st.values())
        rows.append({"symbol": s, "name": u["name"], "group": u["group"], "price": sm.get("price"),
                     "long": {"rating": rate(lp, len(lt), sum(v is not None for v in lt.values())), "passed": lp, "of": len(lt), "checks": lt},
                     "short": {"rating": rate(sp, 6, sum(v is not None for v in st.values())), "passed": sp, "of": 6, "checks": st},
                     "facts": {"sales_cagr_3y": round(cagr, 1) if cagr is not None else None, "gp_assets": round(gpa[s] * 100, 1) if s in gpa else None, "cyclical": cyclical, "op_margin": round(om[0], 1) if om and om[0] is not None else None, "margin_kind": margin_kind, "peg": f.get("peg"),
                               "eps_growth_next_fy": round((f["eps_fy"][1] / f["eps_fy"][0] - 1) * 100, 1) if len(f.get("eps_fy") or []) == 2 and f["eps_fy"][0] and f["eps_fy"][0] > 0 and f["eps_fy"][1] is not None else None,
                               "est_1m_pct": f.get("est_1m_pct"), "fy_up": (f.get("fy_up") or [None])[0], "fy_down": (f.get("fy_down") or [None])[0],
                               "last_surprise_pct": last.get("pct"), "last_report": last_date.isoformat() if last_date else None, "next_report": nxt,
                               "next_estimated": bool(nw.get("estimated")), "mom12_1": sm.get("mom12_1"), "ret_1m": sm.get("ret_1m"), "dd_52w_pct": sm.get("dd_52w_pct"),
                               "buy_pct": sm.get("buy_pct"), "upside_pct": sm.get("upside_to_mean_pct"), "fy_end": f.get("fy_end")},
                     "fund": f})
    # Live log: once a week, record each Strong list with prices; grade 1 and 3 months later against SPY.
    lp = DATA / "ratings_log.json"
    log = {"entries": []}
    if lp.exists():
        try:
            log = json.loads(lp.read_text())
        except Exception:  # noqa: BLE001
            pass
    ents = log["entries"]
    if price_asof and (not ents or (dt.date.fromisoformat(price_asof) - dt.date.fromisoformat(ents[-1]["d"])).days >= 7):
        ents.append({"d": price_asof, "spy": summ.get("SPY", {}).get("price"),
                     "long": {r["symbol"]: r["price"] for r in rows if r["long"]["rating"] == "Strong"},
                     "short": {r["symbol"]: r["price"] for r in rows if r["short"]["rating"] == "Strong"}})
    # Daily snapshots (last 15) of each Strong list and which stocks were rated, for "what changed this week".
    snaps = [x for x in log.get("daily", []) if x.get("d") != price_asof]
    if price_asof:
        snaps.append({"d": price_asof, "rated": sorted(r["symbol"] for r in rows if r["long"]["rating"] != "Not enough data"),
                      "long": sorted(r["symbol"] for r in rows if r["long"]["rating"] == "Strong"),
                      "short": sorted(r["symbol"] for r in rows if r["short"]["rating"] == "Strong")})
    log["daily"] = snaps[-15:]
    changes = None
    if price_asof and len(snaps) >= 2:
        target = dt.date.fromisoformat(price_asof) - dt.timedelta(days=7)
        base = min(snaps[:-1], key=lambda x: abs((dt.date.fromisoformat(x["d"]) - target).days))
        both = set(base["rated"]) & set(snaps[-1]["rated"])
        changes = {"since": base["d"]}
        for side in ("long", "short"):
            was, now = set(base[side]) & both, set(snaps[-1][side]) & both
            changes[side] = {"joined": sorted(now - was), "left": sorted(was - now)}
    spyd = daily("SPY")
    dates = sorted(spyd)

    def after(d0: str, n: int) -> str | None:
        later = [d for d in dates if d > d0]
        return later[n - 1] if len(later) >= n else None
    for e in ents:
        for key, n in (("g1", 21), ("g3", 63)):
            if key in e:
                continue
            d1 = after(e["d"], n)
            if not d1 or not e.get("spy"):
                continue
            g = {"date": d1, "spy": round((spyd[d1] / e["spy"] - 1) * 100, 2)}
            for side in ("long", "short"):
                rs = []
                for s, p0 in e[side].items():
                    sd = daily(s)
                    if d1 in sd and p0:
                        rs.append(sd[d1] / p0 - 1)
                g[side] = round(statistics.mean(rs) * 100, 2) if rs else None
                g[side + "_n"] = len(rs)
            e[key] = g
    lp.write_text(json.dumps(log, separators=(",", ":")))
    graded = {k: [e[k] for e in ents if k in e] for k in ("g1", "g3")}
    summary = {k: {"n": len(v), "long_vs_spy": round(statistics.mean(x["long"] - x["spy"] for x in v if x["long"] is not None), 2) if any(x["long"] is not None for x in v) else None,
                   "short_vs_spy": round(statistics.mean(x["short"] - x["spy"] for x in v if x["short"] is not None), 2) if any(x["short"] is not None for x in v) else None}
               for k, v in graded.items()}
    payload = {"rows": rows, "meta": {"generated_at": NOW.replace(microsecond=0).isoformat().replace("+00:00", "Z"), "price_asof": price_asof,
                                       "generator": "scripts/build_ratings.py", "momentum_cut": cut, "gp_assets_cut": round(gcut * 100, 1) if gcut is not None else None, "spy_ret_1m": spy1m,
                                       "fundamentals": {"with_data": sum(1 for r in rows if r["fund"].get("revenue")), "refetched": got, "of": len(rows), "from_sec": sec_used,
                                                        "median_age_days": statistics.median([(TODAY - dt.date.fromisoformat(r["fund"]["fetched"])).days for r in rows if r["fund"].get("revenue")] or [0])},
                                       "log": {"started": ents[0]["d"] if ents else None, "entries": len(ents), "graded": summary}, "changes": changes,
                                       "method": __doc__.split("\n", 2)[2].strip(),
                                       "sources": ["Nasdaq public company and analyst data (financials, earnings surprises, estimates, PEG)", "this dashboard's prices, trend and analyst data"]}}
    tmp = DATA / "ratings.json.tmp"
    tmp.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False))
    os.replace(tmp, DATA / "ratings.json")
    cnt = lambda side, r: sum(1 for x in rows if x[side]["rating"] == r)  # noqa: E731
    print(f"ratings: {len(rows)} stocks, fundamentals for {payload['meta']['fundamentals']['with_data']} ({got} refreshed now); "
          f"long-term Strong {cnt('long', 'Strong')}, Good {cnt('long', 'Good')}; short-term Strong {cnt('short', 'Strong')}, Good {cnt('short', 'Good')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
