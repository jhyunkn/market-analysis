#!/usr/bin/env python3
"""Checks for the optional datasets (macro regime, market history, insider/fund flows).

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


def main() -> int:
    failed = 0
    for name, fn in (("macro_regime", check_macro), ("market_history", check_history), ("flows", check_flows)):
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
