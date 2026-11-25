#!/usr/bin/env python3
"""Is the published data current? Compares each dataset with the latest completed NYSE session.

Writes data/health.json (the page shows a warning from it) and prints a report. Exit code 1 when something is
stale, so the workflow can open or update a single GitHub issue; 0 when everything is current.
Standard library only; the trading calendar comes from scripts/build_strategies.py.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_strategies import is_trading_day  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
ET = ZoneInfo("America/New_York")


def expected_session(now: dt.datetime) -> dt.date:
    """The last session whose close should be published by now (the evening build finishes by about 5 PM ET)."""
    d = now.date()
    if not (is_trading_day(d) and now.time() >= dt.time(17, 15)):
        d -= dt.timedelta(days=1)
        while not is_trading_day(d):
            d -= dt.timedelta(days=1)
    return d


def load(name: str) -> dict | None:
    try:
        return json.loads((DATA / f"{name}.json").read_text())
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    now = dt.datetime.now(ET)
    want = expected_session(now)
    problems, checked = [], {}

    def price_date(label: str, value: str | None) -> None:
        checked[label] = value
        if not value:
            problems.append(f"{label}: no date found")
        elif dt.date.fromisoformat(value[:10]) < want:
            problems.append(f"{label}: on the {value[:10]} close, expected {want}")

    for name in ("outlook_summary", "market", "market_history"):
        j = load(name)
        price_date(name, j and j.get("meta", {}).get("price_asof"))
    f = load("forward")
    price_date("forward", f and f["rows"][0].get("asof"))
    s = load("strategies")
    if s:
        r = s["rows"][0]
        pv = r.get("provisional")
        if pv:
            price_date("strategies", pv.get("asof"))
        else:  # the month has just ended: the decision month should be the latest completed one
            checked["strategies"] = r.get("decided_at")
            if r.get("decided_at") != f"{want.year}-{want.month:02d}":
                problems.append(f"strategies: decided at {r.get('decided_at')}, expected {want.year}-{want.month:02d}")
    else:
        problems.append("strategies: missing")
    lv = load("live")
    mc = lv and lv["rows"][0].get("market_close")
    price_date("live", mc and dt.datetime.fromisoformat(mc.replace("Z", "+00:00")).astimezone(ET).date().isoformat())
    for name, hours in (("breadth", 30), ("macro_regime", 30), ("cross_asset", 30), ("flows", 54), ("ratings", 30)):
        j = load(name)
        g = j and j.get("meta", {}).get("generated_at")
        checked[name] = g
        if not g:
            problems.append(f"{name}: missing")
            continue
        age = (now - dt.datetime.fromisoformat(g.replace("Z", "+00:00"))).total_seconds() / 3600
        # weekends and holidays: allow the gap since the last expected build
        gap = (now.date() - want).days * 24
        if age > hours + gap:
            problems.append(f"{name}: last built {age:.0f} hours ago")
    out = {"rows": [{"checked_at": now.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                     "expected_session": want.isoformat(), "ok": not problems, "problems": problems, "datasets": checked}],
           "meta": {"generator": "scripts/health_check.py"}}
    (DATA / "health.json").write_text(json.dumps(out, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"expected session {want}; " + ("all current" if not problems else f"{len(problems)} problem(s):"))
    for p in problems:
        print("  - " + p)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
