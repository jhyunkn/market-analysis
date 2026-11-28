#!/usr/bin/env python3
"""Insider trades, institutional (13F) ownership changes, notable hedge-fund moves and short interest.

Source: Nasdaq's public company pages (insider trades from SEC Form 4, holdings from SEC 13F,
short interest from FINRA's public API), one request per stock per dataset. Writes data/flows.json.
Runs at most once every 20 hours (these datasets change daily at most; 13F only quarterly),
unless --force. Keeps the previous file if most requests fail.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
from zoneinfo import ZoneInfo
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "flows.json"
UNIVERSE = ROOT / "config" / "universe.json"
SUMMARY = ROOT / "data" / "outlook_summary.json"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
MIN_BUY_USD = 10_000
SKIP_GROUPS = {"Market & sector ETFs", "Leveraged ETFs"}

# Display name, pattern on the 13F filer name, kind. Trading firms' 13F changes mostly reflect hedging and market making.
NOTABLE = [
    ("Berkshire Hathaway", r"berkshire hathaway", "fund"), ("Bridgewater", r"bridgewater", "fund"),
    ("Renaissance Technologies", r"renaissance tech", "fund"), ("Citadel", r"citadel advisors", "fund"),
    ("Millennium", r"^millennium management", "fund"), ("Point72", r"point72", "fund"), ("Two Sigma", r"two sigma", "fund"),
    ("D. E. Shaw", r"shaw & co", "fund"), ("Tiger Global", r"tiger global", "fund"), ("Coatue", r"coatue", "fund"),
    ("Pershing Square (Ackman)", r"pershing square", "fund"), ("Viking Global", r"viking global", "fund"),
    ("Lone Pine", r"lone pine", "fund"), ("Duquesne (Druckenmiller)", r"duquesne family", "fund"),
    ("Appaloosa (Tepper)", r"appaloosa", "fund"), ("Third Point (Loeb)", r"third point", "fund"),
    ("Elliott", r"elliott (investment|management)", "fund"), ("Soros Fund", r"soros fund", "fund"),
    ("Scion (Burry)", r"scion asset", "fund"), ("Baupost", r"baupost", "fund"), ("Balyasny", r"balyasny", "fund"),
    ("Marshall Wace", r"marshall wace", "fund"), ("Altimeter", r"altimeter", "fund"), ("Maverick", r"maverick capital", "fund"),
    ("ARK Invest", r"ark investment", "fund"), ("Tudor", r"tudor investment", "fund"), ("Dragoneer", r"dragoneer", "fund"),
    ("D1 Capital", r"d1 capital", "fund"), ("Whale Rock", r"whale rock", "fund"), ("Greenlight (Einhorn)", r"greenlight capital", "fund"),
    ("Icahn", r"icahn", "fund"), ("Jane Street", r"jane street", "trader"), ("Susquehanna", r"susquehanna", "trader"),
]
NOTABLE_RE = [(name, re.compile(pat, re.I), kind) for name, pat, kind in NOTABLE]


def get(url: str) -> dict | None:
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as resp:
                return (json.load(resp) or {}).get("data")
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return None


def num(s) -> float | None:
    if s is None:
        return None
    if isinstance(s, (int, float)):
        return float(s)
    t = str(s).replace(",", "").replace("$", "").replace("%", "").strip()
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return None
    return -v if neg else v


def mdy(s: str) -> dt.date | None:
    try:
        return dt.datetime.strptime(s, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None


def insiders(sym: str, price: float | None, today: dt.date) -> dict | None:
    d = get(f"https://api.nasdaq.com/api/company/{sym}/insider-trades?limit=300&type=all&sortColumn=lastDate&sortOrder=DESC")
    rows = (((d or {}).get("transactionTable") or {}).get("table") or {}).get("rows")
    if rows is None:
        return None
    out = {"m3": {"buys": 0, "buyers": [], "buy_shares": 0, "sells": 0, "sell_shares": 0, "planned": 0, "planned_shares": 0},
           "m12": {"buys": 0, "buyers": [], "buy_shares": 0, "sells": 0, "sell_shares": 0, "planned": 0, "planned_shares": 0}, "recent": []}
    oldest = None
    for r in rows:
        day = mdy(r.get("lastDate"))
        if not day:
            continue
        oldest = day
        kind = {"Buy": "buy", "Sell": "sell", "Automatic Sell": "planned"}.get(r.get("transactionType"))
        if not kind:
            continue
        shares = num(r.get("sharesTraded")) or 0
        px = num(r.get("lastPrice")) or 0
        if kind == "buy" and shares * (px or price or 0) < MIN_BUY_USD:
            continue  # tiny purchases are usually employee plans or dividend reinvestment, not a decision to buy
        for key, days in (("m3", 92), ("m12", 366)):
            if (today - day).days > days:
                continue
            o = out[key]
            if kind == "buy":
                o["buys"] += 1; o["buy_shares"] += shares
                if r.get("insider") not in o["buyers"]:
                    o["buyers"].append(r.get("insider"))
            elif kind == "sell":
                o["sells"] += 1; o["sell_shares"] += shares
            else:
                o["planned"] += 1; o["planned_shares"] += shares
        if len(out["recent"]) < 8 and (today - day).days <= 366:
            out["recent"].append({"date": day.isoformat(), "who": (r.get("insider") or "").title(), "role": r.get("relation") or "",
                                  "type": {"buy": "Open-market buy", "sell": "Sale", "planned": "Pre-planned sale"}[kind],
                                  "shares": int(shares), "price": round(px, 2) if px else None})
    for key in ("m3", "m12"):
        o = out[key]
        o["buyers"] = len(o["buyers"])
        if price:
            o["buy_usd"] = round(o["buy_shares"] * price)
            o["sell_usd"] = round(o["sell_shares"] * price)
            o["planned_usd"] = round(o["planned_shares"] * price)
    out["covers_12m"] = bool(oldest and (today - oldest).days >= 360) or len(rows) < 300
    return out


def institutions(sym: str) -> dict | None:
    d = get(f"https://api.nasdaq.com/api/company/{sym}/institutional-holdings?limit=400&type=TOTAL&sortColumn=marketValue&sortOrder=DESC")
    if not d or not d.get("activePositions"):
        return None
    summ = d.get("ownershipSummary") or {}
    act = {r["positions"]: (num(r["holders"]), num(r["shares"])) for r in d["activePositions"]["rows"]}
    ns = {r["positions"]: (num(r["holders"]), num(r["shares"])) for r in (d.get("newSoldOutPositions") or {}).get("rows", [])}
    total = act.get("Total Institutional Shares", (None, None))[1] or 0
    inc_h, inc_s = act.get("Increased Positions", (0, 0)); dec_h, dec_s = act.get("Decreased Positions", (0, 0))
    new_h, new_s = ns.get("New Positions", (0, 0)); out_h, out_s = ns.get("Sold Out Positions", (0, 0))
    rows = ((d.get("holdingsTransactions") or {}).get("table") or {}).get("rows") or []
    dates = [mdy(r.get("date")) for r in rows]
    latest = max((x for x in dates if x), default=None)
    funds = {}
    for r, day in zip(rows, dates):
        if day != latest:
            continue
        for name, rx, kind in NOTABLE_RE:
            if rx.search(r.get("ownerName") or ""):
                chg = num(r.get("sharesChangePCT"))
                val = num(r.get("marketValue"))
                prev = funds.get(name)
                if not prev or (val or 0) > (prev["value_k"] or 0):
                    funds[name] = {"fund": name, "kind": kind, "change_pct": round(chg, 1) if chg is not None else None,
                                   "value_k": val, "shares_change": num(r.get("sharesChange"))}
                break
    net = (inc_s or 0) + (new_s or 0) - (dec_s or 0) - (out_s or 0)
    return {"own_pct": num((summ.get("SharesOutstandingPCT") or {}).get("value")), "shares_out_m": num((summ.get("ShareoutstandingTotal") or {}).get("value")),
            "holders": int(act.get("Total Institutional Shares", (0, 0))[0] or 0), "increased": int(inc_h or 0), "decreased": int(dec_h or 0),
            "new": int(new_h or 0), "sold_out": int(out_h or 0), "net_shares": net, "net_pct_of_inst": round(net / total * 100, 2) if total else None,
            "quarter": latest.isoformat() if latest else None, "funds": sorted(funds.values(), key=lambda f: -(f["value_k"] or 0))}


def shorts(sym: str, shares_out_m: float | None, today: dt.date) -> dict | None:
    """Consolidated short interest for any US listing, from FINRA's public API (twice-monthly settlement dates)."""
    start = (today - dt.timedelta(days=200)).isoformat()
    for code in dict.fromkeys([sym.replace("-", "."), sym.replace("-", "/"), sym.replace("-", "")]):
        body = json.dumps({"limit": 40, "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": code}],
                           "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": start, "endDate": today.isoformat()}]}).encode()
        req = urllib.request.Request("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", data=body,
                                     headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
        rows = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    rows = json.load(resp)
                break
            except Exception:
                time.sleep(1.5 * (attempt + 1))
        if rows:
            break
    if not rows:
        return None
    pts = sorted({r["settlementDate"]: [r["settlementDate"], float(r["currentShortPositionQuantity"]), round(float(r.get("daysToCoverQuantity") or 0), 2)]
                  for r in rows if r.get("currentShortPositionQuantity") is not None}.values())
    if not pts:
        return None
    last = pts[-1]
    month_ago = next((p for p in reversed(pts[:-1]) if (dt.date.fromisoformat(last[0]) - dt.date.fromisoformat(p[0])).days >= 28), None)
    out = {"date": last[0], "shares": last[1], "days_to_cover": last[2], "series": pts[-12:],
           "chg_1m_pct": round((last[1] / month_ago[1] - 1) * 100, 1) if month_ago and month_ago[1] else None}
    if shares_out_m:
        out["pct_out"] = round(last[1] / (shares_out_m * 1e6) * 100, 2)
    return out


def earnings_history(sym: str) -> list[dict]:
    """Last four reported quarters: date, EPS, consensus forecast and surprise (Nasdaq)."""
    d = get(f"https://api.nasdaq.com/api/company/{sym}/earnings-surprise")
    rows = ((d or {}).get("earningsSurpriseTable") or {}).get("rows") or []
    out = []
    for r in rows:
        day = mdy(r.get("dateReported"))
        if day:
            out.append({"date": day.isoformat(), "quarter": r.get("fiscalQtrEnd"), "eps": num(r.get("eps")), "forecast": num(r.get("consensusForecast")), "surprise_pct": num(r.get("percentageSurprise"))})
    return out


def main() -> int:
    force = "--force" in sys.argv
    if OUT.exists() and not force:
        try:
            gen = dt.datetime.fromisoformat(json.loads(OUT.read_text())["meta"]["generated_at"].replace("Z", "+00:00"))
            if dt.datetime.now(dt.timezone.utc) - gen < dt.timedelta(hours=20):
                print("flows: built less than 20 hours ago; skipping")
                return 0
        except Exception:
            pass
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))["symbols"]
    prices = {r["symbol"]: r["price"] for r in json.loads(SUMMARY.read_text(encoding="utf-8"))["rows"]}
    today = dt.datetime.now(ZoneInfo("America/New_York")).date()
    def one(u: dict) -> dict:
        sym = u["symbol"]; nsym = sym.replace("-", ".")
        ins = insiders(nsym, prices.get(sym), today)
        inst = institutions(nsym)
        sh = shorts(sym, (inst or {}).get("shares_out_m"), today)
        eh = earnings_history(nsym)
        return {"symbol": sym, "group": u.get("group"), "price": prices.get(sym), "insiders": ins, "institutions": inst, "short": sh, "earnings_hist": eh}

    todo = [u for u in universe if u.get("group") not in SKIP_GROUPS and not u["symbol"].endswith("-USD")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(one, todo))
    ok = sum(1 for r in rows if r["insiders"] or r["institutions"] or r["short"])
    if ok < len(rows) * 0.5:
        print(f"flows: only {ok} of {len(rows)} stocks returned data; keeping the previous file", file=sys.stderr)
        return 1
    quarters = sorted({r["institutions"]["quarter"] for r in rows if r.get("institutions") and r["institutions"].get("quarter")})
    payload = {"rows": rows, "meta": {
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "stocks": len(rows), "with_data": ok, "latest_13f_quarter": quarters[-1] if quarters else None,
        "notable": [{"fund": n, "kind": k} for n, _, k in NOTABLE],
        "sources": ["Nasdaq company pages: insider trades (SEC Form 4), institutional holdings (SEC 13F), short interest (FINRA)"],
        "notes": "Insider counts use open-market buys and sales; pre-planned (10b5-1) sales are counted separately; option exercises and grants are excluded. "
                 "13F holdings are as of quarter end and filed up to 45 days later, long positions only. Notable funds are found among each stock's 400 largest holders."}}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"flows: {ok} of {len(rows)} stocks, 13F quarter {payload['meta']['latest_13f_quarter']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
