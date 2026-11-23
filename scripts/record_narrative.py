#!/usr/bin/env python3
"""Record how loud each news theme is, once a day, so a narrative timeline can be drawn later.

Reads the theme counts in data/drivers.json (stories in the last 48 hours per theme) and keeps one entry per
calendar day in data/narrative.json (the latest run of the day wins), up to 400 days. Nothing on the page uses
this yet; it needs a few weeks of history first.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "data" / "drivers.json"
OUT = ROOT / "data" / "narrative.json"


def main() -> int:
    try:
        d = json.loads(SRC.read_text(encoding="utf-8"))["rows"][0]
    except Exception as exc:  # noqa: BLE001
        print(f"narrative: no drivers data ({exc})", file=sys.stderr)
        return 1
    themes = {t["id"]: t["count"] for t in d.get("themes", []) if "id" in t}
    names = {t["id"]: t["name"] for t in d.get("themes", []) if "id" in t}
    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {"rows": []}
    rows = [r for r in old.get("rows", []) if r["date"] != today] + [{"date": today, "counts": themes, "stories": len(d.get("news", []))}]
    rows = sorted(rows, key=lambda r: r["date"])[-400:]
    OUT.write_text(json.dumps({"rows": rows, "meta": {"names": {**old.get("meta", {}).get("names", {}), **names},
                                                      "note": "Stories per theme in the 48 hours before each day's last refresh (data/drivers.json)."}}, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"narrative: {len(rows)} days recorded; today {themes}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
