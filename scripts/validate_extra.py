#!/usr/bin/env python3
"""Checks for the optional datasets (macro regime, market history, insider/fund flows, cross-asset).

A dataset that fails is restored to its last committed version, so a bad build is never published,
and the failure is reported as a GitHub Actions warning. Exit code 1 if anything failed.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NOW = dt.datetime.now(dt.timezone.utc)


def age_days(iso: str) -> float:
    return (NOW - dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))).total_seconds() / 86400


def check_macro(d: dict) -> None:
    assert len(d["rows"]) >= 20, "fewer than 20 macro indicators"
    assert all(r["signal"] in ("tailwind", "headwind", "neutral") and 0 <= r["percentile"] <= 100 for r in d["rows"]), "bad signal or percentile"
    assert any(r["condition"].startswith("All") for r in d["regimes"]), "missing baseline regime"
    assert d.get("regime_now"), "missing current regime"
    assert age_days(d["meta"]["generated_at"]) < 3, "macro regime older than 3 days"


def check_history(d: dict) -> None:
    h = d["rows"][0]
    assert len(h["gauges"]) >= 8 and len(h["setups"]) >= 5, "too few gauges or setups"
    assert all(g["recent"] and 0 <= g["stress_pct"] <= 100 for g in h["gauges"]), "bad gauge series"
    assert all(s.get("path", {}).get("median") for s in h["setups"]), "missing setup paths"
    assert age_days(d["meta"]["generated_at"]) < 3, "market history older than 3 days"


def check_flows(d: dict) -> None:
    rows = d["rows"]
    assert len(rows) >= 100, "fewer than 100 stocks"
    assert d["meta"]["with_data"] >= 0.8 * len(rows), "under 80% of stocks have data"
    assert sum(1 for r in rows if r.get("short")) >= 0.8 * len(rows), "short interest missing for over 20% of stocks"
    assert sum(1 for r in rows if r.get("institutions")) >= 0.8 * len(rows), "13F data missing for over 20% of stocks"
    assert age_days(d["meta"]["generated_at"]) < 3, "flows older than 3 days"


def check_cross(d: dict) -> None:
    r = d["rows"][0]
    yc = r.get("yield_curve") or {}
    assert len(yc.get("points", [])) >= 8, "yield curve has fewer than 8 maturities"
    assert len(r.get("fear") or []) >= 3, "fewer than 3 fear gauges"
    assert age_days(d["meta"]["generated_at"]) < 3, "cross-asset data older than 3 days"


def check_breadth(d: dict) -> None:
    r = d["rows"][0]
    assert r["now"]["n"] >= 450, "fewer than 450 S&P 500 stocks with prices"
    assert 0 <= r["now"]["above200_pct"] <= 100 and len(r["sectors"]) >= 10, "bad breadth or sector rows"
    assert len((r.get("factor_monitor") or {}).get("factors", [])) >= 4, "factor test missing"
    assert age_days(d["meta"]["generated_at"]) < 3, "breadth older than 3 days"


def check_live(d: dict) -> None:
    r = d["rows"][0]
    assert len(r.get("futures") or []) >= 2 or len(r.get("stocks") or []) >= 90, "too few futures and quotes"
    assert all(-30 < f["chg_pct"] < 30 for f in r.get("futures") or []), "implausible futures move"
    assert age_days(d["meta"]["generated_at"]) < 2, "live prices older than 2 days"


def check_forward(d: dict) -> None:
    r = d["rows"][0]
    opts = r.get("options") or []
    assert len(opts) >= 80, "option ranges for fewer than 80 symbols"
    for o in opts:
        m1 = o.get("m1")
        if m1:
            assert 0 < m1["move_pct"] < 150 and m1["lo"] < m1["spot"] < m1["hi"], f"implausible option range for {o['symbol']}"
    assert len(r.get("trend") or {}) >= 100, "trend states for fewer than 100 symbols"
    assert all(t["state"] in ("in", "out") and t["switch_line"] > 0 for t in r["trend"].values()), "bad trend state"
    rec = r.get("recession")
    assert rec is None or 0 <= rec["prob_pct"] <= 100, "bad recession probability"
    assert "scorecard" in r, "missing scorecard"
    assert age_days(d["meta"]["generated_at"]) < 3, "forward data older than 3 days"


def check_strategies(d: dict) -> None:
    r = d["rows"][0]
    ss = r["strategies"]
    assert len(ss) >= 6 and any(x["key"] == "hold_spy" for x in ss), "missing strategies or benchmark"
    for x in ss:
        c = x["common"]
        assert -60 < c["cagr_pct"] < 60 and -100 < c["max_dd_pct"] <= 0 and c["years"] >= 10, f"implausible record for {x['key']}"
        assert x["holdings"] and abs(sum(x["holdings"].values()) - 1) < 0.01, f"holdings do not add to 100% for {x['key']}"
        if x.get("tax"):
            assert -60 < x["tax"]["after_tax_cagr_pct"] <= c["cagr_pct"] + 0.05, f"after-tax return above pre-tax for {x['key']}"
    nm = r.get("new_money")
    if nm:
        assert nm["starts"] > 100 and {a["key"] for a in nm["approaches"]} >= {"lump", "dca6", "dca12", "trend", "dip"}, "new-money test incomplete"
    for k, v in (r.get("new_money_by_rule") or {}).items():
        assert v["starts"] > 100 and "lump" in v["approaches"], f"new-money test for {k} incomplete"
    assert r.get("checks") is None or "trend_spy" in r["checks"], "plan checks missing the trend rule"
    ics = ROOT / "data" / "decisions.ics"
    assert ics.exists() and ics.read_text().count("BEGIN:VEVENT") >= 12, "decision calendar missing"
    assert age_days(d["meta"]["generated_at"]) < 3, "strategies older than 3 days"


def check_ratings(d: dict) -> None:
    rows = d["rows"]
    assert len(rows) >= 100, "too few rated stocks"
    with_data = sum(1 for r in rows if r["long"]["rating"] != "Not enough data")
    assert with_data >= 0.6 * len(rows), f"fundamentals missing for too many stocks ({with_data} of {len(rows)})"
    for r in rows:
        assert r["long"]["rating"] in {"Strong", "Good", "Mixed", "Weak", "Not enough data"} and 0 <= r["long"]["passed"] <= 7, f"bad long rating {r['symbol']}"
        assert r["short"]["rating"] in {"Strong", "Good", "Mixed", "Weak", "Not enough data"} and 0 <= r["short"]["passed"] <= 6, f"bad short rating {r['symbol']}"
    assert age_days(d["meta"]["generated_at"]) < 3, "ratings older than 3 days"
    # the page rebuilds daily from cached fundamentals; this catches Nasdaq silently refusing every refetch
    assert d["meta"]["fundamentals"].get("median_age_days", 0) <= 8, "fundamentals have not refreshed in over a week (Nasdaq fetch failing?)"


def check_page() -> list[str]:
    """Catch stylesheet mistakes that silently disable later rules (an unclosed brace), and data files the page cannot find."""
    import re
    html = (ROOT / "index.html").read_text(encoding="utf-8")
    problems = []
    for i, block in enumerate(re.findall(r"<style[^>]*>(.*?)</style>", html, re.S)):
        css = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        css = re.sub(r'"[^"]*"' + "|'[^']*'", '""', css)
        depth = 0
        for ch in css:
            depth += ch == "{"
            depth -= ch == "}"
            if depth < 0:
                break
        if depth != 0:
            problems.append(f"style block {i + 1} has unbalanced braces")
    for path in re.findall(r"'\./(data/[a-z_]+\.json)'", html):
        if not (ROOT / path).exists():
            problems.append(f"page loads {path}, which does not exist")
    return problems


def main() -> int:
    failed = 0
    for problem in check_page():
        failed += 1
        print(f"::warning::page check: {problem}")
    for name, fn in (("macro_regime", check_macro), ("market_history", check_history), ("flows", check_flows), ("cross_asset", check_cross), ("live", check_live), ("breadth", check_breadth), ("forward", check_forward), ("strategies", check_strategies), ("ratings", check_ratings)):
        path = ROOT / "data" / f"{name}.json"
        try:
            fn(json.loads(path.read_text(encoding="utf-8")))
            print(f"{name}: ok")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"::warning::{name} failed validation ({exc}); keeping the last published version")
            subprocess.run(["git", "checkout", "--", str(path)], cwd=ROOT, check=False)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
