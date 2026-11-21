#!/usr/bin/env python3
"""Check a rebuilt outlook dataset before it replaces data/.
Usage: validate_data.py DIR          validate DIR
       validate_data.py --same A B   exit 0 if the rows in A and B are identical
"""
import datetime as dt
import json
import math
import os
import sys

NAMES = ("outlook_summary", "outlook_cone", "outlook_history")


def rows(folder, name):
    with open(os.path.join(folder, name + ".json"), encoding="utf-8") as f:
        return json.load(f)["rows"]


def validate(folder):
    summary, cone, history = (rows(folder, n) for n in NAMES)
    errors = []
    symbols = [r["symbol"] for r in summary]
    if len(symbols) < 140:
        errors.append(f"only {len(symbols)} symbols")
    if len(set(symbols)) != len(symbols):
        errors.append("duplicate symbols")
    dates = {r["asof"] for r in summary}
    if len(dates) != 1:
        errors.append(f"mixed as-of dates: {sorted(dates)}")
    else:
        age = (dt.date.today() - dt.date.fromisoformat(dates.pop())).days
        if age < 0 or age > 5:
            errors.append(f"as-of date is {age} days old")
    last = {}
    for r in sorted(history, key=lambda x: x["date"]):
        last[r["symbol"]] = r["close"]
    cone_symbols = {r["symbol"] for r in cone}
    for r in summary:
        s, p = r["symbol"], r["price"]
        if not (isinstance(p, (int, float)) and math.isfinite(p) and p > 0):
            errors.append(f"{s}: bad price")
            continue
        if s not in cone_symbols or s not in last:
            errors.append(f"{s}: missing chart data")
        elif abs(last[s] / p - 1) > 0.001:
            errors.append(f"{s}: price {p} differs from last close {last[s]}")
        for h in ("m1", "m3", "m12"):
            if not r[h + "_lo"] <= p <= r[h + "_hi"]:
                errors.append(f"{s}: price outside {h} range")
        if not r["invalid_level"] < p < r["promote_level"]:
            errors.append(f"{s}: confirm/invalidate levels do not bracket price")
    return errors


if __name__ == "__main__":
    if sys.argv[1] == "--same":
        sys.exit(0 if all(rows(sys.argv[2], n) == rows(sys.argv[3], n) for n in NAMES) else 1)
    problems = validate(sys.argv[1])
    if problems:
        print("validation failed:", *problems[:25], sep="\n  ")
        sys.exit(1)
    print("validation passed")
