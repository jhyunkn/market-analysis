#!/usr/bin/env python3
"""Fail closed when generated dashboard datasets are incomplete or inconsistent."""

import datetime as dt
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def load(name):
    payload = json.loads((DATA / f"{name}.json").read_text(encoding="utf-8"))
    assert isinstance(payload.get("rows"), list), f"{name}: missing rows"
    assert isinstance(payload.get("meta"), dict), f"{name}: missing structured metadata"
    return payload


summary = load("outlook_summary")
cone = load("outlook_cone")
history = load("outlook_history")
rows = summary["rows"]
symbols = [row["symbol"] for row in rows]
assert len(rows) >= 140, f"summary: expected at least 140 symbols, found {len(rows)}"
assert len(symbols) == len(set(symbols)), "summary: duplicate symbols"
assert symbols == sorted(symbols), "summary: symbols must be sorted for stable diffs"
assert all(row.get("beta_window") == "5y" or str(row.get("beta_window", "")).startswith("available (") for row in rows), "summary: invalid beta window"
assert sum(row.get("beta_window") == "5y" for row in rows) >= 130, "summary: too few symbols have a five-year beta"
assert all(row.get("promote_basis") in {"50-day average", "200-day average", "1-month high"} for row in rows), "summary: unknown confirmation basis"
assert all(row.get("action") in {"Probe", "Watch", "Watch: odds good, trend not yet", "Avoid for now"} for row in rows), "summary: unknown action"
for name, payload in (("cone", cone), ("history", history)):
    present = {row["symbol"] for row in payload["rows"]}
    assert present == set(symbols), f"{name}: symbol set differs from summary"
    assert payload["meta"]["generated_at"] == summary["meta"]["generated_at"], f"{name}: build timestamp differs from summary"
    assert payload["meta"]["price_asof"] == summary["meta"]["price_asof"], f"{name}: price date differs from summary"
    assert payload["meta"]["sp500_setup"] == summary["meta"]["sp500_setup"], f"{name}: S&P setup metadata differs from summary"
cone_counts = {symbol: 0 for symbol in symbols}
history_counts = {symbol: 0 for symbol in symbols}
for row in cone["rows"]:
    cone_counts[row["symbol"]] += 1
for row in history["rows"]:
    history_counts[row["symbol"]] += 1
assert set(cone_counts.values()) == {31}, f"cone: expected 31 points per symbol, got {sorted(set(cone_counts.values()))}"
assert set(history_counts.values()) == {126}, f"history: expected 126 points per symbol, got {sorted(set(history_counts.values()))}"
for name, payload in (("cone", cone), ("history", history)):
    keys = [row["key"] for row in payload["rows"]]
    assert len(keys) == len(set(keys)), f"{name}: duplicate row keys"
for row in rows:
    assert row["price"] > 0 and row["m1_lo"] < row["m1_hi"] and row["m3_lo"] < row["m3_hi"] and row["m12_lo"] < row["m12_hi"], f"{row['symbol']}: invalid price ranges"
    assert row["invalid_level"] < row["price"], f"{row['symbol']}: invalidation is not below price"
    assert row["promote_level"] >= row["price"], f"{row['symbol']}: confirmation is below price"
    assert isinstance(row.get("spx_setup_active"), bool), f"{row['symbol']}: missing S&P setup state"
    assert isinstance(row.get("bottom_zones"), list), f"{row['symbol']}: missing bottom zones"
    assert not any(k.startswith(("plan_limit", "plan_stop", "size_")) for k in row), f"{row['symbol']}: removed plan/size fields are back"
    assert row.get("ret_1d") is not None and row.get("ret_1m") is not None, f"{row['symbol']}: missing recent returns"
    if row.get("target_mean") is not None:
        assert row.get("analyst_snapshot_date") == summary["meta"]["analyst_snapshot_at"], f"{row['symbol']}: analyst snapshot mismatch"
generated = dt.datetime.fromisoformat(summary["meta"]["generated_at"].replace("Z", "+00:00"))
assert generated.tzinfo is not None, "summary: generated_at must include timezone"
assert dt.datetime.now(dt.timezone.utc) - generated < dt.timedelta(hours=2), "summary: build timestamp is stale"
assert summary["meta"].get("beta_window") == "5 years", "summary: metadata beta window is wrong"
price_asof = dt.date.fromisoformat(summary["meta"]["price_asof"])
assert dt.datetime.now(dt.timezone.utc).date() - price_asof <= dt.timedelta(days=5), "summary: latest market close is stale"
setup = summary["meta"].get("sp500_setup", {})
assert isinstance(setup.get("active"), bool) and setup.get("vix", 0) > 0 and -100 < setup.get("drawdown_pct", 0) <= 0, "summary: invalid S&P setup metadata"
market = load("market")["rows"][0]
assert len(market["sectors"]) >= 10 and market["indices"] and market["breadth"]["n"] > 100, "market: incomplete overview"
assert all(sz["lo"] <= sz["hi"] for sz in market["spx_zones"]), "market: invalid S&P zones"
news_rows = load("news")["rows"]
assert {r["symbol"] for r in news_rows} == set(symbols), "news: symbol set differs from summary"
print(f"validated {len(rows)} symbols, {len(cone['rows'])} cone points, {len(history['rows'])} history points")
