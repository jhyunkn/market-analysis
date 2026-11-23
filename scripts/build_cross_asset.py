#!/usr/bin/env python3
"""Cross-asset context for the "What is moving the market" section.

Writes data/cross_asset.json:
- yield_curve: Treasury yields by maturity today, a month ago and a year ago (FRED DGS series).
- fear: implied volatility for stocks (VIX), tech (VXN), Treasuries (MOVE), oil (OVX) and gold (GVZ),
  ranked against the past year and five years (Yahoo Finance).
- fomc: the next Federal Reserve policy meetings (federalreserve.gov calendar).
- documents: the latest Fed policy releases and speeches, and BEA data releases (official RSS feeds).
Each part is best effort; a part that fails keeps its previous value.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_data as B  # noqa: E402

OUT = B.DATA / "cross_asset.json"
UA = {"User-Agent": "Mozilla/5.0"}
CURVE = [("1M", "DGS1MO", 1 / 12), ("3M", "DGS3MO", 0.25), ("6M", "DGS6MO", 0.5), ("1Y", "DGS1", 1), ("2Y", "DGS2", 2), ("3Y", "DGS3", 3),
         ("5Y", "DGS5", 5), ("7Y", "DGS7", 7), ("10Y", "DGS10", 10), ("20Y", "DGS20", 20), ("30Y", "DGS30", 30)]
FEAR = [("^VIX", "Stocks (VIX)", "S&P 500 options"), ("^VXN", "Tech (VXN)", "Nasdaq-100 options"), ("^MOVE", "Treasuries (MOVE)", "Treasury options"),
        ("^OVX", "Oil (OVX)", "crude oil options"), ("^GVZ", "Gold (GVZ)", "gold options")]
FOMC_PAST: list[dict] = []
MONTHS = {m: i for i, m in enumerate(["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"], 1)}


def get(url: str) -> bytes | None:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30) as resp:
            return resp.read()
    except Exception:
        return None


def on_or_before(series: dict, day: dt.date):
    keys = [d for d in series if d <= day]
    return series[max(keys)] if keys else None


def yield_curve() -> dict | None:
    data = {label: B.fetch_fred(sid) for label, sid, _ in CURVE}
    if sum(bool(v) for v in data.values()) < 8:
        return None
    last = max(max(v) for v in data.values() if v)
    points = []
    for label, sid, years in CURVE:
        s = data[label]
        if not s:
            continue
        points.append({"maturity": label, "years": years, "now": on_or_before(s, last), "m1": on_or_before(s, last - dt.timedelta(days=30)),
                       "y1": on_or_before(s, last - dt.timedelta(days=365))})
    def spread(a, b, k):
        pa = next((p for p in points if p["maturity"] == a), None); pb = next((p for p in points if p["maturity"] == b), None)
        return round(pb[k] - pa[k], 2) if pa and pb and pa[k] is not None and pb[k] is not None else None
    mort = B.fetch_fred("MORTGAGE30US")
    mortgage = None
    if mort:
        ml = max(mort)
        mortgage = {"asof": ml.isoformat(), "now": mort[ml], "y1": on_or_before(mort, ml - dt.timedelta(days=365))}
    return {"asof": last.isoformat(), "points": points, "mortgage30": mortgage, "s2_10": spread("2Y", "10Y", "now"), "s3m_10": spread("3M", "10Y", "now"),
            "s2_10_y1": spread("2Y", "10Y", "y1"), "s3m_10_y1": spread("3M", "10Y", "y1")}


def fear() -> list | None:
    out = []
    for sym, label, what in FEAR:
        try:
            p = B.fetch_symbol(sym, dt.date(2015, 1, 1), "_hist")
        except RuntimeError:
            continue
        days = sorted(p)
        cur = p[days[-1]]
        y1 = [p[d] for d in days[-252:]]; y5 = [p[d] for d in days[-1260:]]
        m1 = p[days[-22]] if len(days) > 22 else None
        out.append({"symbol": sym, "label": label, "what": what, "value": round(cur, 2), "asof": days[-1].isoformat(),
                    "pct_1y": round(100 * sum(v < cur for v in y1) / len(y1)), "pct_5y": round(100 * sum(v < cur for v in y5) / len(y5)),
                    "chg_1m_pct": round((cur / m1 - 1) * 100, 1) if m1 else None, "median_5y": round(sorted(y5)[len(y5) // 2], 2),
                    "spark": [[d.isoformat(), round(p[d], 2)] for d in days[-130:]]})
    return out if len(out) >= 3 else None


def fomc(today: dt.date) -> list | None:
    raw = get("https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm")
    if not raw:
        return None
    s = raw.decode("utf-8", "replace")
    marks = sorted((m.start(), int(m.group(1))) for m in re.finditer(r"(\d{4}) FOMC Meetings", s))
    meetings = []
    for m in re.finditer(r"fomc-meeting__month[^>]*>\s*<strong>([^<]+)</strong>.*?fomc-meeting__date[^>]*>([^<]+)<", s, re.S):
        year = next((y for pos, y in reversed(marks) if pos < m.start()), None)
        month_txt, date_txt = m.group(1).strip(), m.group(2).strip()
        if not year or "notation" in date_txt.lower():
            continue
        last_month = month_txt.split("/")[-1]
        mon = next((v for k, v in MONTHS.items() if k.startswith(last_month[:3])), None)
        days = re.findall(r"\d+", date_txt)
        if not mon or not days:
            continue
        try:
            day = dt.date(year, mon, int(days[-1]))
        except ValueError:
            continue
        meetings.append({"date": day.isoformat(), "label": f"{month_txt} {date_txt.rstrip('*')}, {year}", "projections": date_txt.endswith("*")})
    upcoming = sorted((x for x in meetings if x["date"] >= today.isoformat()), key=lambda x: x["date"])
    past = sorted((x for x in meetings if (today - dt.timedelta(days=400)).isoformat() <= x["date"] < today.isoformat()), key=lambda x: x["date"])
    FOMC_PAST[:] = past
    return upcoming[:4] or None


def feed(url: str, source: str, keep, n: int) -> list:
    raw = get(url)
    if not raw:
        return []
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip(); link = (item.findtext("link") or "").strip()
        try:
            when = parsedate_to_datetime(item.findtext("pubDate")).astimezone(dt.timezone.utc)
        except Exception:
            continue
        if title and link.startswith("https://") and keep(title):
            out.append({"source": source, "title": title, "link": link, "date": when.date().isoformat()})
        if len(out) >= n:
            break
    return out


def main() -> int:
    prev = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    prev_row = (prev.get("rows") or [{}])[0]
    today = dt.datetime.now(dt.timezone.utc).date()
    policy = re.compile(r"FOMC|Federal Open Market|monetary|discount rate|economic projections|policy", re.I)
    bea = re.compile(r"GDP|Personal Income|Outlays|International Trade|Corporate Profits|PCE|Inflation", re.I)
    docs = (feed("https://www.federalreserve.gov/feeds/press_monetary.xml", "Federal Reserve", lambda t: bool(policy.search(t)) and "discount rate" not in t.lower(), 5)
            + feed("https://www.federalreserve.gov/feeds/speeches.xml", "Fed speech", lambda t: True, 4)
            + feed("https://apps.bea.gov/rss/rss.xml", "BEA", lambda t: bool(bea.search(t)), 4))
    row = {
        "yield_curve": yield_curve() or prev_row.get("yield_curve"),
        "fear": fear() or prev_row.get("fear"),
        "fomc": fomc(today) or prev_row.get("fomc"),
        "fomc_past": FOMC_PAST or prev_row.get("fomc_past"),
        "documents": sorted(docs, key=lambda d: d["date"], reverse=True) or prev_row.get("documents"),
    }
    if not any(row.values()):
        print("cross_asset: every source failed; keeping the previous file", file=sys.stderr)
        return 1
    payload = {"rows": [row], "meta": {"generated_at": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
               "sources": ["FRED Treasury yields (DGS series)", "Yahoo Finance volatility indexes", "federalreserve.gov FOMC calendar and RSS", "BEA release RSS"]}}
    OUT.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")
    yc = row["yield_curve"] or {}
    print(f"cross_asset: curve {len(yc.get('points', []))} maturities (2s10s {yc.get('s2_10')}), fear {len(row['fear'] or [])}, "
          f"next FOMC {(row['fomc'] or [{}])[0].get('label')}, documents {len(row['documents'] or [])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
