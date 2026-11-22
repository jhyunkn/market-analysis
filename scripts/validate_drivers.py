#!/usr/bin/env python3
"""Check data/drivers.json before it is published. Usage: validate_drivers.py [path]"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from build_drivers import settled_day  # noqa: E402

CORE = {"spx", "vix", "y10", "wti", "gold", "dxy", "semis"}


def check(path: Path) -> tuple[list[str], list[str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    p, meta = payload["rows"][0], payload["meta"]
    symbols = {r["symbol"] for r in json.loads((ROOT / "data" / "outlook_summary.json").read_text(encoding="utf-8"))["rows"]}
    errors: list[str] = []
    warnings: list[str] = []
    now = dt.datetime.now(dt.timezone.utc)
    built = dt.datetime.fromisoformat(meta["generated_at"].replace("Z", "+00:00"))
    if not dt.timedelta(minutes=-5) <= now - built <= dt.timedelta(hours=2):
        errors.append(f"build timestamp is stale: {meta['generated_at']}")
    behind = (settled_day() - dt.date.fromisoformat(p["asof"])).days
    if behind < 0 or behind > 4:
        errors.append(f"latest close {p['asof']} is {behind} days from the last settled session")
    missing = CORE - {d["id"] for d in p["drivers"]}
    if missing:
        errors.append(f"core drivers missing: {sorted(missing)}")
    if sum(not m.get("stale") for m in p["macro"]) < 12:
        errors.append("fewer than 12 fresh macro readings")
    covered = symbols & set(p["stocks"])
    if len(covered) < 0.9 * len(symbols):
        errors.append(f"stock coverage {len(covered)} of {len(symbols)}")
    for s, x in p["stocks"].items():
        if "chips" not in {e["id"] for e in x["exposures"]}:
            errors.append(f"{s}: no chip exposure")
        for span in ("day", "week"):
            w = x[span]
            if abs(sum(w["parts"].values()) + w["other"] - w["total"]) > 0.08:
                errors.append(f"{s}: {span} attribution does not add up")
    if not p["news"]:
        errors.append("no news stories")
    if any(not n["link"].startswith("https://") for n in p["news"]):
        errors.append("news link is not https")
    if meta.get("news_stale"):
        warnings.append("news feeds were unavailable; the previous stories are marked out of date")
    else:
        if meta["feeds_ok"] < 4:
            errors.append(f"only {meta['feeds_ok']} news feeds worked")
        newest = max(dt.datetime.fromisoformat(n["published"].replace("Z", "+00:00")) for n in p["news"]) if p["news"] else None
        if newest and now - newest > dt.timedelta(hours=48):
            errors.append(f"newest story is from {newest.isoformat()}")
    return errors, warnings


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "drivers.json"
    problems, notes = check(target)
    for note in notes:
        print(f"::warning::{note}")
    if problems:
        print("drivers validation failed:", *problems[:20], sep="\n  ")
        sys.exit(1)
    print("drivers validated")
