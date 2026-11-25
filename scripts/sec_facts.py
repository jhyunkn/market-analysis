#!/usr/bin/env python3
"""Company financials from SEC EDGAR's free XBRL API (data.sec.gov), with the date each number was filed.

SEC asks every client to identify itself with a contact email in the User-Agent. The address is read from the
SEC_CONTACT environment variable (a GitHub Actions secret in the cloud build) and never written to any file; without
it every function returns nothing and callers carry on without SEC data.

annual(cik) -> {item: [(fiscal_year_end, value, filed_date), ...]} for 10-K annual figures, every filing kept, so a
caller can ask what was known on any past date (point in time) with known_at().
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
import urllib.request

CONTACT = os.environ.get("SEC_CONTACT", "").strip()
_last = [0.0]

# XBRL tags tried in order for each item (companies use different ones, and switched to the ASC 606 revenue tag in 2018).
FLOW = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "op_income": ["OperatingIncomeLoss"],
    "gross_profit": ["GrossProfit"],
    "cost_of_revenue": ["CostOfRevenue", "CostOfGoodsAndServicesSold", "CostOfGoodsSold"],
}
INSTANT = {"assets": ["Assets"], "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"]}


def enabled() -> bool:
    return bool(CONTACT)


def _get(url: str):
    if not CONTACT:
        return None
    for attempt in range(3):
        wait = 0.15 - (time.time() - _last[0])  # SEC allows 10 requests a second; stay well under
        if wait > 0:
            time.sleep(wait)
        _last[0] = time.time()
        try:
            req = urllib.request.Request(url, headers={"User-Agent": f"market-analysis dashboard {CONTACT}", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=40) as r:
                return json.load(r)
        except Exception:  # noqa: BLE001
            time.sleep(1.5 * (attempt + 1))
    return None


def ciks() -> dict[str, int]:
    d = _get("https://www.sec.gov/files/company_tickers.json") or {}
    return {v["ticker"].upper().replace(".", "-"): int(v["cik_str"]) for v in d.values()}


def _d(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def annual(cik: int) -> dict[str, list[tuple[dt.date, float, dt.date]]]:
    facts = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
    if not facts:
        return {}
    gaap = (facts.get("facts") or {}).get("us-gaap") or {}
    out: dict[str, list[tuple[dt.date, float, dt.date]]] = {}

    def pick(tags: list[str], flow: bool) -> list[tuple[dt.date, float, dt.date]]:
        rows: dict[tuple[dt.date, dt.date], float] = {}
        for tag in tags:  # earlier tags win for the same period and filing
            for x in ((gaap.get(tag) or {}).get("units") or {}).get("USD", []):
                if not str(x.get("form", "")).startswith("10-K") or "end" not in x or "filed" not in x:
                    continue
                if flow:
                    if "start" not in x or not 340 <= (_d(x["end"]) - _d(x["start"])).days <= 380:
                        continue
                key = (_d(x["end"]), _d(x["filed"]))
                rows.setdefault(key, float(x["val"]))
        return sorted((e, v, f) for (e, f), v in rows.items())

    for item, tags in FLOW.items():
        out[item] = pick(tags, True)
    for item, tags in INSTANT.items():
        out[item] = pick(tags, False)
    return out


def known_at(series: list[tuple[dt.date, float, dt.date]], when: dt.date, years: int = 4) -> list[tuple[dt.date, float]]:
    """The last `years` fiscal-year values as first reported on or before `when`, newest first (one per year end)."""
    first: dict[dt.date, tuple[dt.date, float]] = {}
    for end, val, filed in series:
        if filed <= when and (end not in first or filed < first[end][0]):
            first[end] = (filed, val)
    ends = sorted(first, reverse=True)
    picked: list[dt.date] = []
    for e in ends:  # one value per fiscal year: skip year ends closer than ~10 months to the previous pick
        if not picked or (picked[-1] - e).days >= 300:
            picked.append(e)
        if len(picked) == years:
            break
    return [(e, first[e][1]) for e in picked]
