#!/usr/bin/env python3
"""Macro regime read: is the economy helping or hurting stocks, and is money getting cheaper or dearer?

Pure Python (no numpy). Uses FRED through build_data.fetch_fred (cached) and the S&P 500 from Yahoo.
Writes data/macro_regime.json with:
  scorecard   tailwind / neutral / headwind counts per area and the overall split (economy vs price of money)
  themes      plain-language reads per area, written from the numbers
  series      ten years of monthly values for the rates and inflation charts
  regimes     S&P 500 returns after past months with the same macro conditions as today (one-month publication lag)
Indicator rules are fixed thresholds written in advance, not fitted.
"""
from __future__ import annotations

import datetime as dt
import json
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as B  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "macro_regime.json"


def mkey(d: dt.date) -> int:
    return d.year * 12 + d.month - 1


def month_date(k: int) -> dt.date:
    return dt.date(k // 12, k % 12 + 1, 1)


def monthly(src: dict, how: str = "last") -> dict[int, float]:
    out: dict[int, list] = {}
    for d in sorted(src):
        out.setdefault(mkey(d), []).append(src[d])
    return {k: (v[-1] if how == "last" else sum(v) / len(v)) for k, v in out.items()}


def yoy(m: dict[int, float]) -> dict[int, float]:
    return {k: (v / m[k - 12] - 1) * 100 for k, v in m.items() if k - 12 in m and m[k - 12]}


def diff(m: dict[int, float], n: int) -> dict[int, float]:
    return {k: v - m[k - n] for k, v in m.items() if k - n in m}


def pct_rank(values, v) -> int:
    vals = list(values)
    return round(sum(x < v for x in vals) / len(vals) * 100)


AVG = {"DFF", "DGS2", "DGS10", "DFII10", "T10Y3M", "T5YIE", "BAA10Y", "NFCI", "ICSA", "DTWEXBGS", "DCOILWTICO"}
IDS = ["DFF", "DGS2", "DGS10", "DFII10", "T10Y3M", "T5YIE", "CPIAUCSL", "CPILFESL", "PCEPILFE", "UNRATE", "PAYEMS", "ICSA",
       "A191RL1Q225SBEA", "INDPRO", "RSAFS", "UMCSENT", "BAA10Y", "NFCI", "WALCL", "M2SL", "DTWEXBGS", "DCOILWTICO", "SAHMREALTIME"]


def sig(cond_head, cond_tail, why_head, why_mid, why_tail):
    return ("headwind", why_head) if cond_head else ("tailwind", why_tail) if cond_tail else ("neutral", why_mid)


def main() -> None:
    raw = {s: B.fetch_fred(s) for s in IDS}
    if sum(bool(v) for v in raw.values()) < len(IDS) - 3:
        print("macro_regime: FRED mostly unavailable; keeping the previous file")
        return
    M = {s: monthly(v, "mean" if s in AVG else "last") for s, v in raw.items()}
    T = {"fedfunds": M["DFF"], "y2": M["DGS2"], "y10": M["DGS10"], "real10": M["DFII10"], "curve": M["T10Y3M"], "be5": M["T5YIE"],
         "cpi": yoy(M["CPIAUCSL"]), "core_cpi": yoy(M["CPILFESL"]), "core_pce": yoy(M["PCEPILFE"]), "unrate": M["UNRATE"],
         "payrolls": {k: v / 3 for k, v in diff(M["PAYEMS"], 3).items()}, "claims": {k: v / 1000 for k, v in M["ICSA"].items()},
         "gdp": M["A191RL1Q225SBEA"], "indpro": yoy(M["INDPRO"]), "retail": yoy(M["RSAFS"]), "sentiment": M["UMCSENT"],
         "baa": M["BAA10Y"], "nfci": M["NFCI"], "fedbs": yoy(M["WALCL"]), "m2": yoy(M["M2SL"]), "dollar": yoy(M["DTWEXBGS"]),
         "oil": M["DCOILWTICO"], "sahm": M["SAHMREALTIME"]}
    # id, area, label, unit, rule(v, change over 3 months, change over 12 months)
    SPEC = [
        ("fedfunds", "Fed & rates", "Fed funds rate", "%", lambda v, c3, c12: sig(c12 > 0.25 or c3 >= 0.2, c12 < -0.25, "the Fed has been raising rates (borrowing gets more expensive)", "rates on hold", "the Fed is cutting (borrowing gets cheaper)")),
        ("y2", "Fed & rates", "2-year Treasury", "%", lambda v, c3, c12: sig(c3 > 0.25, c3 < -0.25, "markets price more hikes", "little change in rate expectations", "markets price cuts")),
        ("y10", "Fed & rates", "10-year Treasury", "%", lambda v, c3, c12: sig(v >= 4.5, v < 3, "above 4.5%, the level where long rates start to weigh on stock valuations", "between 3% and 4.5%: moderate", "below 3%: cheap long-term money")),
        ("real10", "Fed & rates", "10-year real yield", "%", lambda v, c3, c12: sig(v >= 2, v < 0.5, "above 2% after inflation: bonds now compete hard with stocks", "between 0.5% and 2%: moderate", "below 0.5%: little competition from bonds")),
        ("curve", "Fed & rates", "Yield curve (10y − 3m)", "pts", lambda v, c3, c12: sig(v < 0, v >= 1, "inverted, a classic recession warning", "flat curve: no clear signal", "normal upward slope")),
        ("core_pce", "Inflation", "Core PCE, y/y", "%", lambda v, c3, c12: sig(v >= 3, v < 2.2, "3% or more, well above the Fed's 2% target: keeps rates high", "above target but under 3%", "near the 2% target")),
        ("core_cpi", "Inflation", "Core CPI, y/y", "%", lambda v, c3, c12: sig(v >= 3, v < 2.2, "well above target", "above target but contained", "near target")),
        ("cpi", "Inflation", "Headline CPI, y/y", "%", lambda v, c3, c12: sig(v >= 3, v < 2.2, "well above target", "above target but contained", "near target")),
        ("be5", "Inflation", "5-year inflation expectation", "%", lambda v, c3, c12: sig(v >= 2.5 or v < 1.8, False, "inflation expectations drifting", "inflation expectations steady", "")),
        ("unrate", "Jobs", "Unemployment rate", "%", lambda v, c3, c12: sig(c12 >= 0.5, c12 <= 0.1, "joblessness is rising", "drifting higher", "labor market steady")),
        ("sahm", "Jobs", "Sahm recession indicator", "pts", lambda v, c3, c12: sig(v >= 0.5, v < 0.3, "recession signal triggered", "approaching the trigger", "no recession signal")),
        ("payrolls", "Jobs", "Jobs added a month (3-mo avg)", "k", lambda v, c3, c12: sig(v < 50, v >= 150, "hiring has stalled", "hiring is modest", "hiring is solid")),
        ("claims", "Jobs", "Weekly jobless claims", "k", lambda v, c3, c12: sig(v >= 300, v < 250, "layoffs rising", "layoffs edging up", "layoffs low")),
        ("gdp", "Growth", "Real GDP growth, annualized", "%", lambda v, c3, c12: sig(v < 1, v >= 2.5, "growth is weak", "growth near trend", "growth is strong")),
        ("indpro", "Growth", "Industrial production, y/y", "%", lambda v, c3, c12: sig(v < 0, v >= 2, "factory output shrinking", "output growing slowly", "output growing")),
        ("retail", "Growth", "Retail sales, y/y", "%", lambda v, c3, c12: sig(v < 3, v >= 6, "spending barely keeps up with prices", "spending growing", "spending is strong")),
        ("sentiment", "Growth", "Consumer sentiment", "idx", lambda v, c3, c12: sig(v < 60, v >= 80, "consumers are gloomy", "consumers are cautious", "consumers are confident")),
        ("baa", "Credit & liquidity", "Corporate credit spread (Baa − 10y)", "pts", lambda v, c3, c12: sig(v >= 2.5, v < 1.8, "borrowing costs stressed", "spreads near normal", "corporate credit is calm")),
        ("nfci", "Credit & liquidity", "Financial conditions (Chicago Fed)", "idx", lambda v, c3, c12: sig(v > 0, v <= -0.3, "conditions tighter than average", "conditions near average", "borrowing is easy")),
        ("fedbs", "Credit & liquidity", "Fed balance sheet, y/y", "%", lambda v, c3, c12: sig(v < -2, v >= 2, "the Fed is draining liquidity", "balance sheet flat", "the Fed is adding liquidity")),
        ("m2", "Credit & liquidity", "Money supply (M2), y/y", "%", lambda v, c3, c12: sig(v < 0, v >= 5, "money supply contracting", "money growing slowly", "money supply growing")),
        ("dollar", "Dollar & oil", "Broad dollar, y/y", "%", lambda v, c3, c12: sig(v >= 5, v <= -5, "a strong dollar tightens conditions", "dollar stable", "a weaker dollar eases conditions")),
        ("oil", "Dollar & oil", "WTI crude, monthly avg", "$", lambda v, c3, c12: sig(v >= 90, v < 55, "oil high enough to squeeze consumers", "oil moderate", "cheap oil helps consumers")),
    ]
    rows = []
    for iid, area, label, unit, rule in SPEC:
        m = T[iid]
        if not m:
            continue
        k = max(m); v = m[k]
        c3 = v - m.get(k - 3, v); c12 = v - m.get(k - 12, v)
        s, why = rule(v, c3, c12)
        rows.append({"id": iid, "area": area, "label": label, "unit": unit, "value": round(v, 3), "chg_3m": round(c3, 3), "chg_12m": round(c12, 3),
                     "percentile": pct_rank(m.values(), v), "since": month_date(min(m)).year, "asof": month_date(k).isoformat(), "signal": s, "why": why})
    R = {r["id"]: r for r in rows}
    areas = ["Growth", "Jobs", "Credit & liquidity", "Fed & rates", "Inflation", "Dollar & oil"]
    score = [{"area": a, **{t: sum(r["signal"] == t for r in rows if r["area"] == a) for t in ("tailwind", "neutral", "headwind")}} for a in areas]
    econ = [r for r in rows if r["area"] in areas[:3]]; money = [r for r in rows if r["area"] in areas[3:]]
    def lean(rs):
        h = sum(r["signal"] == "headwind" for r in rs); t = sum(r["signal"] == "tailwind" for r in rs)
        return ("supportive" if t > h else "restrictive" if h > t else "mixed"), h, t
    eb, mb = lean(econ), lean(money)
    f = lambda i, d=1: (f"{R[i]['value']:.{d}f}" + ("%" if R[i]["unit"] == "%" else "")) if i in R else "n/a"
    p = lambda x, d=1: f"{'+' if x > 0 else '−' if x < 0 else ''}{abs(x):.{d}f}"

    # S&P after macro conditions (monthly, one-month publication lag).
    spx = monthly(B.fetch_symbol("^GSPC", dt.date(1990, 1, 1)))
    try:
        long_spx = B.fetch_symbol("^GSPC", dt.date(1950, 1, 1), "_long1950")
        spx = monthly(long_spx)
    except RuntimeError:
        pass
    keys = sorted(spx)
    f12 = {k: spx[k + 12] / spx[k] - 1 for k in keys if k + 12 in spx}
    f3 = {k: spx[k + 3] / spx[k] - 1 for k in keys if k + 3 in spx}
    infl, grow = yoy(M["CPIAUCSL"]), yoy(M["INDPRO"])
    dinf, dgro, core, ff6 = diff(infl, 6), diff(grow, 6), yoy(M["CPILFESL"]), diff(M["DFF"], 6)
    def quad(k):
        if k not in dinf or k not in dgro:
            return None
        return {(True, False): "Goldilocks: growth up, inflation down", (True, True): "Overheating: growth up, inflation up",
                (False, True): "Stagflation: growth down, inflation up", (False, False): "Slowdown: growth down, inflation down"}[(dgro[k] > 0, dinf[k] > 0)]
    conds = [("All months (normal)", lambda k: True),
             ("Fed raising rates", lambda k: ff6[k] >= 0.2 if k in ff6 else None),
             ("Fed cutting rates", lambda k: ff6[k] < -0.25 if k in ff6 else None),
             ("Core inflation above 3%", lambda k: core[k] > 3 if k in core else None),
             ("10-year yield above 4.5%", lambda k: M["DGS10"][k] > 4.5 if k in M["DGS10"] else None),
             ("Corporate credit calm (Baa spread < 2)", lambda k: M["BAA10Y"][k] < 2 if k in M["BAA10Y"] else None),
             ("Sahm recession signal on", lambda k: M["SAHMREALTIME"][k] >= 0.5 if k in M["SAHMREALTIME"] else None)]
    for q in ["Goldilocks: growth up, inflation down", "Overheating: growth up, inflation up", "Stagflation: growth down, inflation up", "Slowdown: growth down, inflation down"]:
        conds.append((q, (lambda qq: lambda k: (quad(k) == qq) if quad(k) else None)(q)))
    regimes = []
    newest = max(keys)
    for name, fn in conds:
        hits = [k + 1 for k in keys if fn(k) and (k + 1) in f12]
        if len(hits) < 12:
            continue
        a12 = [f12[k] * 100 for k in hits]; a3 = [f3[k] * 100 for k in hits if k in f3]
        now = next((fn(k) for k in range(newest, newest - 7, -1) if fn(k) is not None), False)
        regimes.append({"condition": name, "today": bool(now), "months": len(hits), "episodes": 1 + sum(b - a > 1 for a, b in zip(hits, hits[1:])),
                        "since": month_date(hits[0]).year, "fwd3m_median": round(statistics.median(a3), 1), "fwd12m_median": round(statistics.median(a12), 1),
                        "fwd12m_up_pct": round(sum(x > 0 for x in a12) / len(a12) * 100), "fwd12m_p10": round(B.percentile(a12, 10), 1)})
    latest_q = next((quad(k) for k in range(newest, newest - 7, -1) if quad(k)), None)
    meds = [r["fwd12m_median"] for r in regimes if not r["condition"].startswith("All")]
    qrow = next((r for r in regimes if r["condition"] == latest_q), None)
    sr = R.get("sentiment")
    sent_rank = "n/a" if not sr else (f"the lowest reading since {sr['since']}" if sr["percentile"] < 1 else f"higher than only {sr['percentile']}% of readings since {sr['since']}")
    themes = [
        {"area": "Overall", "lean": "mixed" if eb[0] != mb[0] else eb[0],
         "text": f"The economy is {eb[0]} ({eb[2]} of {len(econ)} growth, jobs and credit gauges help stocks); the price of money is {mb[0]} "
                 f"({mb[1]} of {len(money)} rate, inflation and dollar/oil gauges hurt). Regime: {latest_q.split(':')[0].lower() if latest_q else 'unclear'}"
                 + (f" ({latest_q.split(': ')[1]}); after such months the S&P's median next year was {p(qrow['fwd12m_median'])}%, up {qrow['fwd12m_up_pct']}% of the time." if qrow else ".")
                 + (f" Across every macro condition tested, median next-year returns only ranged {p(min(meds))}% to {p(max(meds))}%, so macro sets the backdrop but has been a weak timing signal." if meds else "")},
        {"area": "Growth", "text": f"Real GDP {f('gdp')} annualized last quarter, retail sales {f('retail')} y/y, factory output {f('indpro')} y/y; consumer sentiment {f('sentiment')} is {sent_rank}: people are spending while saying they feel worse."},
        {"area": "Inflation", "text": f"Core PCE {f('core_pce')} and core CPI {f('core_cpi')} against the Fed's 2% goal; headline CPI {f('cpi')}; markets expect {f('be5', 2)} a year over five years."},
        {"area": "Fed & rates", "text": f"Fed funds {f('fedfunds', 2)} ({p(R['fedfunds']['chg_3m'], 2) if 'fedfunds' in R else ''} pts in 3 months); 2-year {f('y2', 2)}, 10-year {f('y10', 2)}, real 10-year {f('real10', 2)} (higher than {R['real10']['percentile'] if 'real10' in R else '–'}% of months since {R['real10']['since'] if 'real10' in R else '–'}). Curve {f('curve', 2)} pts."},
        {"area": "Jobs", "text": f"Unemployment {f('unrate')} ({p(R['unrate']['chg_12m']) if 'unrate' in R else ''} pts in a year), payrolls {f('payrolls', 0)}k a month, weekly claims {f('claims', 0)}k; Sahm indicator {f('sahm', 2)} against a 0.5 trigger."},
        {"area": "Credit & liquidity", "text": f"Baa corporate spread {f('baa', 2)} pts (lower than {100 - R['baa']['percentile'] if 'baa' in R else '–'}% of months since {R['baa']['since'] if 'baa' in R else '–'}); financial conditions {f('nfci', 2)} (below 0 is looser than average); Fed balance sheet {f('fedbs')} y/y; M2 {f('m2')} y/y."},
        {"area": "Dollar & oil", "text": f"Broad dollar {f('dollar')} y/y; WTI averaged ${R['oil']['value']:.0f} this month." if "oil" in R else ""},
    ]
    for t in themes[1:]:
        rs = [r for r in rows if r["area"] == t["area"]]
        h = sum(r["signal"] == "headwind" for r in rs); tw = sum(r["signal"] == "tailwind" for r in rs)
        t["lean"] = "tailwind" if tw > h else "headwind" if h > tw else "mixed"
    series = [{"id": i, "date": month_date(k).isoformat(), "value": round(v, 3)} for i in ("fedfunds", "y2", "y10", "real10", "core_pce", "core_cpi", "cpi")
              for k, v in sorted(T[i].items()) if k > max(T[i]) - 120]
    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    OUT.write_text(json.dumps({"rows": rows, "scorecard": score, "themes": themes, "series": series, "regimes": regimes, "regime_now": latest_q,
                               "meta": {"generated_at": generated, "source": "FRED (St. Louis Fed); S&P 500 from Yahoo Finance",
                                        "method": "Fixed thresholds per indicator, written in advance. Regime months use data with a one-month publication lag."}},
                              separators=(",", ":"), allow_nan=False), encoding="utf-8")
    print(f"macro_regime: {len(rows)} indicators, {len(regimes)} conditions; economy {eb[0]}, money {mb[0]}; regime {latest_q}")


if __name__ == "__main__":
    main()
