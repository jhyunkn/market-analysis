#!/usr/bin/env python3
"""Record a small daily snapshot of the page's key states, so the page can say what changed since the previous close.

Each row (one per market close, New York date) holds: the S&P 500's state (near its high, pullback, correction, bear market),
the VIX band, whether most S&P 500 stocks are above their 200-day average, what each tested daily trend rule holds, each
fund's month-end trend state, and the Strong rating lists. Rows are keyed by the close they describe; a later run for the
same close replaces the row. `--backfill` rebuilds rows for past closes from the data files in git history.
Writes data/states_log.json (last 60 closes).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "states_log.json"
KEEP = 60
TREND_FUNDS = ["SPY", "QQQ", "IWM", "DIA", "SMH", "XLK", "TQQQ", "UPRO", "SOXL", "TECL"]


def _rows0(d: dict | None) -> dict | None:
    rows = (d or {}).get("rows") or []
    return rows[0] if rows else None


def states(load) -> dict | None:
    """load(name) returns the parsed data/<name>.json (or None). Returns one snapshot row, or None if the market file is missing."""
    m = _rows0(load("market"))
    if not m or m.get("asof") is None or m.get("spx_off_high_pct") is None:
        return None
    off, vix = m["spx_off_high_pct"], (m.get("vol") or {}).get("vix")
    row = {"date": m["asof"],
           "market": "bear market" if off <= -20 else "correction" if off <= -10 else "pullback" if off <= -5 else "near its high",
           "vix": None if vix is None else "high" if vix >= 25 else "elevated" if vix >= 18 else "low"}
    b = _rows0(load("breadth"))
    if b and (b.get("now") or {}).get("above200_pct") is not None:
        row["breadth_up"] = b["now"]["above200_pct"] >= 50
    s = _rows0(load("strategies"))
    if s:
        names = {x["key"]: x["name"] for x in s.get("strategies", [])}
        row["daily"] = {k: {"in": bool(v[0].get("in_fund")), "name": names.get(k, k)} for k, v in (s.get("checks") or {}).items()
                        if v and v[0].get("kind") == "daily"}
    f = _rows0(load("forward"))
    if f:
        tr = f.get("trend") or {}
        row["trend"] = {k: tr[k]["state"] for k in TREND_FUNDS if k in tr and tr[k].get("state") in ("in", "out")}
    r = load("ratings")
    if r and r.get("rows"):
        row["strong_long"] = sorted(x["symbol"] for x in r["rows"] if (x.get("long") or {}).get("rating") == "Strong")
        row["strong_short"] = sorted(x["symbol"] for x in r["rows"] if (x.get("short") or {}).get("rating") == "Strong")
    return row


def _file_loader(name: str) -> dict | None:
    p = ROOT / "data" / f"{name}.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _git_loader(sha: str):
    def load(name: str) -> dict | None:
        try:
            out = subprocess.run(["git", "show", f"{sha}:data/{name}.json"], cwd=ROOT, capture_output=True, check=True).stdout
            return json.loads(out)
        except (subprocess.CalledProcessError, ValueError):
            return None
    return load


def merge(rows: list[dict], row: dict) -> list[dict]:
    rows = [r for r in rows if r["date"] != row["date"]] + [row]
    return sorted(rows, key=lambda r: r["date"])[-KEEP:]


def main() -> int:
    try:
        rows = json.loads(OUT.read_text(encoding="utf-8")).get("rows", [])
    except (OSError, ValueError):
        rows = []
    if "--backfill" in sys.argv:
        shas = subprocess.run(["git", "log", "--since=21 days ago", "--format=%H", "--reverse", "--", "data/market.json"], cwd=ROOT,
                              capture_output=True, text=True, check=True).stdout.split()
        for sha in shas:  # oldest first, so the last commit for each close wins
            row = states(_git_loader(sha))
            if row:
                rows = merge(rows, row)
    row = states(_file_loader)
    if row is None:
        print("states: market data missing; log unchanged", file=sys.stderr)
        return 0
    rows = merge(rows, row)
    OUT.write_text(json.dumps({"rows": rows, "meta": {"generator": "scripts/record_states.py",
                                                     "note": "One snapshot per market close; the page compares the latest with the one before."}},
                              separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"states: {len(rows)} closes logged, latest {rows[-1]['date']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
