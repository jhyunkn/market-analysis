#!/usr/bin/env python3
"""Build the market-analysis datasets from public daily market data.

The existing summary file is the editable universe and analyst-target snapshot.
Daily prices, volatility, trend state, historical analogs, ranges, and action
states are rebuilt from Yahoo Finance. No Capital Radar checkout is required.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import math
import os
import re
from pathlib import Path
import statistics
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CACHE = ROOT / ".cache" / "market-data"
SUMMARY = DATA / "outlook_summary.json"
UNIVERSE = ROOT / "config" / "universe.json"
ANALYST_TARGETS = ROOT / "config" / "analyst_targets.json"
ANALYST_MAX_AGE_DAYS = 45
TRADING_YEAR = 252
BETA_DAYS = TRADING_YEAR * 5
HORIZON = TRADING_YEAR
STEPS = sorted(set(range(0, 22, 3)) | set(range(21, 64, 6)) | set(range(63, HORIZON + 1, 12)) | {21, 63, HORIZON})
Z = {"p05": -1.645, "p16": -1.0, "p84": 1.0, "p95": 1.645}


def mean(values: list[float]) -> float:
    return sum(values) / len(values)


def variance(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    avg = mean(values)
    return sum((value - avg) ** 2 for value in values) / (len(values) - 1)


def covariance(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or len(left) < 2:
        return 0.0
    left_avg, right_avg = mean(left), mean(right)
    return sum((a - left_avg) * (b - right_avg) for a, b in zip(left, right)) / (len(left) - 1)


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires data")
    rank = (len(ordered) - 1) * pct / 100
    lo, hi = math.floor(rank), math.ceil(rank)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - rank) + ordered[hi] * (rank - lo)


def moving_average(values: list[float], window: int) -> list[float | None]:
    out: list[float | None] = [None] * len(values)
    running = 0.0
    for index, value in enumerate(values):
        running += value
        if index >= window:
            running -= values[index - window]
        if index >= window - 1:
            out[index] = running / window
    return out


def cache_path(symbol: str, tag: str = "") -> Path:
    safe = symbol.replace("^", "_").replace("=", "_").replace("/", "_")
    return CACHE / f"{safe}{tag}.json"


def fetch_symbol(symbol: str, start: dt.date, tag: str = "") -> dict[dt.date, float]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = cache_path(symbol, tag)
    ohlc_file = cache_path(symbol, "_ohlc")
    if cached.exists() and time.time() - cached.stat().st_mtime < 3 * 3600 and (tag or ohlc_file.exists()):
        payload = json.loads(cached.read_text(encoding="utf-8"))
        return {dt.date.fromisoformat(key): value for key, value in payload.items()}

    period1 = int(dt.datetime.combine(start, dt.time.min, tzinfo=dt.timezone.utc).timestamp())
    period2 = int(dt.datetime.now(dt.timezone.utc).timestamp()) + 86400
    encoded = urllib.parse.quote(symbol, safe="")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{encoded}?period1={period1}&period2={period2}&interval=1d&events=div%2Csplits"
    request = urllib.request.Request(url, headers={"User-Agent": "market-analysis/1.0"})
    error: Exception | None = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=35) as response:
                result = json.load(response)["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
            adjusted = result["indicators"].get("adjclose", [{}])[0].get("adjclose") or quote["close"]
            prices: dict[dt.date, float] = {}
            for stamp, close, adj in zip(result["timestamp"], quote["close"], adjusted):
                value = adj if adj is not None else close
                if value is not None:
                    prices[dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date()] = float(value)
            if len(prices) < 260:
                raise RuntimeError(f"{symbol}: only {len(prices)} daily prices returned")
            if not tag:
                # Daily open/high/low/close/volume for the candle chart and volume profile, on the adjusted scale.
                bars = {}
                for stamp, op, hi, lo, close, vol, adj in zip(result["timestamp"], quote.get("open", []), quote.get("high", []), quote.get("low", []),
                                                             quote["close"], quote.get("volume", []), adjusted):
                    if None in (op, hi, lo, close) or not close:
                        continue
                    factor = (adj / close) if adj else 1.0
                    day = dt.datetime.fromtimestamp(stamp, dt.timezone.utc).date().isoformat()
                    bars[day] = [round(op * factor, 4), round(hi * factor, 4), round(lo * factor, 4), round(close * factor, 4), int(vol or 0)]
                ohlc_file.write_text(json.dumps(dict(sorted(bars.items()))), encoding="utf-8")
            cached.write_text(json.dumps({day.isoformat(): value for day, value in prices.items()}), encoding="utf-8")
            return prices
        except (OSError, KeyError, TypeError, urllib.error.URLError, RuntimeError) as exc:
            error = exc
            if attempt < 3:
                time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"Unable to fetch {symbol}: {error}")


def business_day(start: dt.date, count: int) -> dt.date:
    result = start
    remaining = count
    while remaining:
        result += dt.timedelta(days=1)
        if result.weekday() < 5:
            remaining -= 1
    return result


def load_universe() -> tuple[list[dict], dict[str, dict]]:
    payload = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    rows = sorted(payload["symbols"], key=lambda row: row["symbol"])
    prior = {row["symbol"]: row for row in rows}
    if len(rows) < 100 or len(prior) != len(rows):
        raise RuntimeError("config/universe.json is not a valid unique market universe")
    return rows, prior


def sp500_analog_path(spx: dict[dt.date, float], vix: dict[dt.date, float]) -> tuple[list[float], int, dict]:
    days = sorted(spx)
    prices = [spx[day] for day in days]
    running_high = 0.0
    candidates: list[int] = []
    for index, (day, price) in enumerate(zip(days, prices)):
        running_high = max(running_high, price)
        drawdown = price / running_high - 1
        vix_value = vix.get(day)
        if vix_value is not None and vix_value >= 30 and -0.20 <= drawdown <= -0.08:
            candidates.append(index)
    starts = [candidate for pos, candidate in enumerate(candidates) if pos == 0 or candidate - candidates[pos - 1] > 21]
    paths = [[prices[start + step] / prices[start] - 1 for step in range(HORIZON + 1)] for start in starts if start + HORIZON < len(prices)]
    if len(paths) < 5:
        raise RuntimeError(f"Only {len(paths)} complete S&P analog episodes found")
    latest = max(set(spx) & set(vix))
    peak = max(value for day, value in spx.items() if day <= latest)
    current_drawdown = spx[latest] / peak - 1
    current_vix = vix[latest]
    regime = {
        "asof": latest.isoformat(),
        "vix": round(current_vix, 2),
        "drawdown_pct": round(current_drawdown * 100, 1),
        "active": current_vix >= 30 and -0.20 <= current_drawdown <= -0.08,
    }
    return [statistics.median(path[step] for path in paths) for step in range(HORIZON + 1)], len(paths), regime


def load_ohlc(symbol: str) -> dict[dt.date, list[float]]:
    path = cache_path(symbol, "_ohlc")
    if not path.exists():
        return {}
    return {dt.date.fromisoformat(day): bar for day, bar in json.loads(path.read_text(encoding="utf-8")).items()}


def fetch_fred(series: str) -> dict[dt.date, float]:
    """Daily FRED series (no key needed). Cached like prices; empty on failure so the build still runs."""
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = CACHE / f"fred_{series}.json"
    if cached.exists() and time.time() - cached.stat().st_mtime < 12 * 3600:
        return {dt.date.fromisoformat(k): v for k, v in json.loads(cached.read_text(encoding="utf-8")).items()}
    request = urllib.request.Request(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}", headers={"User-Agent": "curl/8.7.1"})
    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            lines = response.read().decode().splitlines()[1:]
    except (OSError, urllib.error.URLError) as exc:
        print(f"FRED {series} unavailable: {exc}")
        return {}
    out = {}
    for line in lines:
        day, _, value = line.partition(",")
        if value not in (".", ""):
            out[dt.date.fromisoformat(day)] = float(value)
    cached.write_text(json.dumps({k.isoformat(): v for k, v in out.items()}), encoding="utf-8")
    return out


MACRO_META: dict = {}
CREDIT_STRESS = 2.0  # Baa corporate yield minus 10-year Treasury, points; above this, corporate credit is stressed


def macro_bottom_stats(spx: dict[dt.date, float], vix: dict[dt.date, float], baa: dict[dt.date, float]) -> dict:
    """On past days in a sell-off like today (VIX 30+, S&P 8-20% off its high): how much further the S&P fell
    to its low within 3 months, and how many trading days that low took, split by corporate-credit state."""
    days = sorted(spx); prices = [spx[d] for d in days]
    running_high, last_baa = 0.0, None
    groups = {"calm": [], "stressed": []}
    baa_days = sorted(baa); j = 0
    for i, day in enumerate(days):
        running_high = max(running_high, prices[i])
        while j < len(baa_days) and baa_days[j] <= day:
            last_baa = baa[baa_days[j]]; j += 1
        v = vix.get(day); dd = prices[i] / running_high - 1
        if v is None or v < 30 or not -0.20 <= dd <= -0.08 or i + 63 >= len(prices) or last_baa is None:
            continue
        window = prices[i:i + 64]; low = min(window)
        groups["stressed" if last_baa >= CREDIT_STRESS else "calm"].append((i, low / prices[i] - 1, window.index(low)))
    def summarise(rows: list) -> dict:
        if len(rows) < 20:
            return {}
        idx = [r[0] for r in rows]
        return {"days": len(rows), "episodes": 1 + sum(b - a > 21 for a, b in zip(idx, idx[1:])),
                "extra_median_pct": round(statistics.median(r[1] for r in rows) * 100, 1),
                "extra_p10_pct": round(percentile([r[1] for r in rows], 10) * 100, 1),
                "days_to_low_median": int(statistics.median(r[2] for r in rows))}
    latest_baa = baa[max(baa)] if baa else None
    state = None if latest_baa is None else ("stressed" if latest_baa >= CREDIT_STRESS else "calm")
    calm, stressed = summarise(groups["calm"]), summarise(groups["stressed"])
    base = (stressed if state == "stressed" else calm) or calm or stressed
    return {"credit_spread": latest_baa, "credit_state": state, "credit_threshold": CREDIT_STRESS,
            "calm": calm, "stressed": stressed,
            "base_extra_pct": base.get("extra_median_pct"), "stress_extra_pct": (stressed or calm).get("extra_p10_pct"),
            "days_to_low_median": base.get("days_to_low_median"), "spx_price": round(prices[-1], 2)}


def bottom_zones(price: float, beta: float, macro: dict, bars: list, closes: list[float], ma200: float, low_52w: float, crash: dict) -> tuple[list[dict], list[dict], list[dict]]:
    """Candidate bottom levels from separate methods, clustered; a zone's score is how many kinds of evidence agree."""
    cands = []
    if macro.get("base_extra_pct") is not None:
        cands.append((price * (1 + beta * macro["base_extra_pct"] / 100), "macro", "Macro base case"))
    if macro.get("stress_extra_pct") is not None:
        cands.append((price * max(0.05, 1 + beta * macro["stress_extra_pct"] / 100), "macro-stress", "Macro stress case"))
    if crash.get("crash_n", 0) >= 3 and crash.get("crash_dd3_median") is not None:
        cands.append((price * (1 + crash["crash_dd3_median"] / 100), "history", "Its typical crash drop"))
    if ma200 < price:
        cands.append((ma200, "trend", "200-day average"))
    if low_52w < price * 0.995:
        cands.append((low_52w, "structure", "52-week low"))
    bars = bars[-504:]
    lows = [b[2] for b in bars]
    pivots = []
    for i in range(10, len(lows) - 10):
        if lows[i] == min(lows[i - 10:i + 11]) and lows[i] < price * 0.995:
            pivots.append(lows[i])
    for level in sorted(set(round(x, 2) for x in pivots), reverse=True)[:4]:
        cands.append((level, "structure", "Prior swing low"))
    profile = []
    recent = bars[-252:]
    if recent:
        lo_all, hi_all = min(b[2] for b in recent), max(b[1] for b in recent)
        width = (hi_all - lo_all) / 24 or 1
        vols = [0.0] * 24
        for b in recent:
            vols[min(23, int((b[3] - lo_all) / width))] += b[4]
        total = sum(vols) or 1
        profile = [{"lo": round(lo_all + k * width, 4), "hi": round(lo_all + (k + 1) * width, 4), "share": round(v / total, 4)} for k, v in enumerate(vols)]
        heavy = sorted((p for p in profile if (p["lo"] + p["hi"]) / 2 < price * 0.995), key=lambda p: -p["share"])[:2]
        for p in heavy:
            cands.append(((p["lo"] + p["hi"]) / 2, "volume", "Heavy-volume price"))
    cands = [c for c in cands if price * 0.30 < c[0] < price * 0.995]
    cands.sort(key=lambda c: -c[0])
    zones, current = [], []
    for c in cands:
        if current and c[0] < current[0][0] * 0.97:
            zones.append(current); current = []
        current.append(c)
    if current:
        zones.append(current)
    out = []
    for z in zones:
        kinds = sorted({c[1].replace("-stress", "") for c in z})
        lo, hi = min(c[0] for c in z), max(c[0] for c in z)
        if hi - lo < price * 0.01:
            mid = (hi + lo) / 2; lo, hi = mid - price * 0.005, mid + price * 0.005
        out.append({"lo": round(lo, 2), "hi": round(hi, 2), "score": len(kinds), "methods": sorted({c[2] for c in z})})
    return out, profile, [{"level": round(x, 2)} for x in pivots]


def pick_zones(zones: list[dict], price: float) -> tuple[dict | None, dict | None]:
    for z in zones:
        z["hi"] = round(min(z["hi"], price * 0.98), 2) if z["lo"] < price * 0.98 else z["hi"]
    near = [z for z in zones if z["lo"] < price * 0.98]
    primary = max(near, key=lambda z: (z["score"], z["hi"])) if near else None
    deeper = [z for z in near if primary and z["hi"] < primary["lo"] * 0.98]
    return primary, (max(deeper, key=lambda z: (z["score"], z["hi"])) if deeper else None)


def zone_backtest(closes: list[float], bars: list, step: int = 15) -> dict:
    """Walk forward: at past dates when the stock was 5%+ below its 52-week high, build the main bottom zone from
    data up to that day only (chart structure, volume and the 200-day; macro and crash-history evidence are not
    replayed), then check the low of the next 63 trading days. 'Held' means price reached the zone and its low
    stayed within 3% below it. A same-width band at a random distance (2-20% below) is the baseline."""
    import random
    rng = random.Random(7)
    n = min(len(closes), len(bars))
    closes, bars = closes[-n:], bars[-n:]
    out = {"samples": 0, "reached": 0, "held": 0, "base_reached": 0, "base_held": 0, "strong_reached": 0, "strong_held": 0, "strong_base_reached": 0, "strong_base_held": 0}
    for i in range(300, n - 64, step):
        price = closes[i]
        high52 = max(closes[i - 251:i + 1])
        if price > high52 * 0.95:
            continue
        ma200 = sum(closes[i - 199:i + 1]) / 200
        zones, _, _ = bottom_zones(price, 1.0, {}, bars[max(0, i - 503):i + 1], closes[:i + 1], ma200, min(closes[i - 251:i + 1]), {})
        primary, _ = pick_zones(zones, price)
        if not primary:
            continue
        low = min(b[2] for b in bars[i + 1:i + 64])
        out["samples"] += 1
        reached = low <= primary["hi"]; held = reached and low >= primary["lo"] * 0.97
        out["reached"] += reached; out["held"] += held
        if primary["score"] >= 3:
            out["strong_reached"] += reached; out["strong_held"] += held
        width = primary["hi"] - primary["lo"]
        top = price * (1 - rng.uniform(0.02, 0.20)); bot = top - width
        b_reached = low <= top
        b_held = b_reached and low >= bot * 0.97
        out["base_reached"] += b_reached; out["base_held"] += b_held
        if primary["score"] >= 3:
            out["strong_base_reached"] += b_reached; out["strong_base_held"] += b_held
    return out


def sell_off_starts(spx: dict[dt.date, float], vix: dict[dt.date, float]) -> list[dt.date]:
    """First day of each past S&P sell-off like the analog setup (VIX 30+, S&P 8-20% off its high), counted once per episode."""
    days = sorted(spx)
    running_high, candidates = 0.0, []
    for index, day in enumerate(days):
        running_high = max(running_high, spx[day])
        vix_value = vix.get(day)
        if vix_value is not None and vix_value >= 30 and -0.20 <= spx[day] / running_high - 1 <= -0.08:
            candidates.append(index)
    return [days[c] for pos, c in enumerate(candidates) if pos == 0 or c - candidates[pos - 1] > 21]


def crash_replay(prices: dict[dt.date, float], starts: list[dt.date]) -> dict:
    """Next-year return and worst drop within 3 months after each sell-off start the symbol traded through."""
    days = sorted(prices)
    if not days:
        return {"crash_n": 0}
    closes = [prices[day] for day in days]
    forward, drops = [], []
    position = 0
    for start in starts:
        while position < len(days) and days[position] < start:
            position += 1
        if position >= len(days) or (days[position] - start).days > 5 or position + TRADING_YEAR >= len(closes):
            continue
        base = closes[position]
        forward.append(closes[position + TRADING_YEAR] / base - 1)
        drops.append(min(closes[position:position + 64]) / base - 1)
    out = {"crash_n": len(forward), "crash_since": days[0].year}
    if forward:
        out.update({
            "crash_f12_median": round(statistics.median(forward) * 100, 1),
            "crash_f12_worst": round(min(forward) * 100, 1),
            "crash_f12_up_pct": round(sum(value > 0 for value in forward) / len(forward) * 100),
            "crash_dd3_median": round(statistics.median(drops) * 100, 1),
            "crash_dd3_worst": round(min(drops) * 100, 1),
        })
    return out


def build_symbol(symbol: str, prior: dict, analyst: dict | None, analyst_snapshot: dt.date, prices: dict[dt.date, float], spy: dict[dt.date, float], spx_path: list[float], regime: dict, crash: dict | None = None, spy_crash: dict | None = None, macro: dict | None = None, ohlc: dict | None = None) -> tuple[dict, list[dict], list[dict]]:
    days = [day for day in sorted(prices) if day in spy]
    closes = [prices[day] for day in days]
    spy_closes = [spy[day] for day in days]
    if len(days) < 260:
        raise RuntimeError(f"{symbol}: need at least one year of aligned prices; found {len(days)}")

    returns = [math.log(closes[index] / closes[index - 1]) for index in range(1, len(closes))]
    spy_returns = [math.log(spy_closes[index] / spy_closes[index - 1]) for index in range(1, len(spy_closes))]
    beta_length = min(BETA_DAYS, len(returns))
    beta_left, beta_right = returns[-beta_length:], spy_returns[-beta_length:]
    beta = covariance(beta_left, beta_right) / variance(beta_right)

    baseline = returns[-504:-252] if len(returns) > 504 else returns
    ewma_variance = variance(baseline)
    for value in returns[-252:]:
        ewma_variance = 0.94 * ewma_variance + 0.06 * value * value
    sigma_now = math.sqrt(ewma_variance)
    sigma_long = math.sqrt(variance(returns[-252:]))
    cumulative_variance: list[float] = []
    total = 0.0
    for step in range(HORIZON + 1):
        total += sigma_long**2 + (sigma_now**2 - sigma_long**2) * math.exp(-step * math.log(2) / 63)
        cumulative_variance.append(total)
    sd = lambda step: math.sqrt(cumulative_variance[step - 1]) if step else 0.0

    ma50 = moving_average(closes, 50)
    ma200 = moving_average(closes, 200)
    price = closes[-1]
    asof = days[-1]
    history = [{
        "symbol": symbol,
        "date": day.isoformat(),
        "close": round(close, 4),
        "ma50": round(float(avg50), 4) if avg50 is not None else None,
        "ma200": round(float(avg200), 4) if avg200 is not None else None,
        **({"open": (ohlc or {})[day][0], "high": (ohlc or {})[day][1], "low": (ohlc or {})[day][2], "volume": (ohlc or {})[day][4]} if day in (ohlc or {}) else {}),
        "key": f"{symbol}|{day.isoformat()}",
    } for day, close, avg50, avg200 in zip(days[-126:], closes[-126:], ma50[-126:], ma200[-126:])]

    cone = []
    for step in STEPS:
        row = {
            "symbol": symbol,
            "step": step,
            "date": business_day(asof, step).isoformat(),
            "key": f"{symbol}|{step}",
            "p50": round(price, 4),
            "history_path": round(price * max(0.01, 1 + beta * spx_path[step]), 4),
        }
        for key, z_score in Z.items():
            row[key] = round(price * math.exp(z_score * sd(step)), 4)
        cone.append(row)

    def price_range(step: int) -> tuple[float, float]:
        return price * math.exp(-sd(step)), price * math.exp(sd(step))

    lo1, hi1 = price_range(21)
    lo3, hi3 = price_range(63)
    lo12, hi12 = price_range(HORIZON)
    row = {
        "symbol": symbol,
        "group": prior.get("group", "Other"),
        "price": round(price, 2),
        "asof": asof.isoformat(),
        "vol_now_pct": round(sigma_now * math.sqrt(TRADING_YEAR) * 100),
        "vol_1y_pct": round(sigma_long * math.sqrt(TRADING_YEAR) * 100),
        "beta": round(beta, 2),
        "beta_window": "5y" if beta_length == BETA_DAYS else f"available ({beta_length}d)",
        "m1_lo": round(lo1, 2), "m1_hi": round(hi1, 2),
        "m3_lo": round(lo3, 2), "m3_hi": round(hi3, 2),
        "m12_lo": round(lo12, 2), "m12_hi": round(hi12, 2),
        "history_3m": round(price * (1 + beta * spx_path[63]), 2),
        "history_12m": round(price * (1 + beta * spx_path[HORIZON]), 2),
        "high_52w": round(max(closes[-252:]), 2),
        "low_52w": round(min(closes[-252:]), 2),
        "spx_setup_active": regime["active"],
        "spx_current_vix": regime["vix"],
        "spx_current_drawdown_pct": regime["drawdown_pct"],
    }
    analyst_stale = (dt.datetime.now(dt.timezone.utc).date() - analyst_snapshot).days > ANALYST_MAX_AGE_DAYS
    if analyst is not None:
        row.update({
            "target_mean": analyst["mean"], "target_low": analyst["low"], "target_high": analyst["high"],
            "analysts": analyst["analysts"], "buy_pct": analyst["buy_pct"], "target_date": analyst.get("last_changed"),
            "analyst_snapshot_date": analyst_snapshot.isoformat(), "analyst_stale": analyst_stale,
        })
        row["upside_to_mean_pct"] = round((row["target_mean"] / price - 1) * 100, 1)
        row["target_sigmas_12m"] = round(math.log(row["target_mean"] / price) / sd(HORIZON), 2)

    high_rolling = [max(closes[max(0, index - 251):index + 1]) for index in range(len(closes))]
    drawdown = [close / high - 1 for close, high in zip(closes, high_rolling)]
    current_ma50 = float(ma50[-1])
    current_ma200 = float(ma200[-1])
    above_200 = price > current_ma200
    slope_up = current_ma50 >= float(ma50[-21])
    trend = "Uptrend" if above_200 and slope_up else "Downtrend" if not above_200 and not slope_up else "Turning" if not above_200 else "Pullback in uptrend"

    matched_12: list[float] = []
    matched_3: list[float] = []
    match_indexes: list[int] = []
    base_12: list[float] = []
    for index in range(252, len(closes) - 252):
        forward_12 = closes[index + 252] / closes[index] - 1
        base_12.append(forward_12)
        same_state = abs(drawdown[index] - drawdown[-1]) <= 0.05 and ((closes[index] > float(ma200[index])) == above_200)
        if same_state:
            match_indexes.append(index)
            matched_12.append(forward_12)
            matched_3.append(closes[index + 63] / closes[index] - 1)
    episodes = 1 + sum(right - left > 21 for left, right in zip(match_indexes, match_indexes[1:])) if match_indexes else 0
    own = {
        "own_days": len(match_indexes),
        "own_episodes": episodes,
        "own_since": days[252].year,
        "base_f12_up_pct": round(sum(value > 0 for value in base_12) / len(base_12) * 100),
    }
    if len(matched_12) >= 40 and episodes >= 4:
        own.update({
            "own_f12_median": round(statistics.median(matched_12) * 100, 1),
            "own_f12_up_pct": round(sum(value > 0 for value in matched_12) / len(matched_12) * 100),
            "own_f3_median": round(statistics.median(matched_3) * 100, 1),
            "own_f3_up_pct": round(sum(value > 0 for value in matched_3) / len(matched_3) * 100),
            "own_f3_p10": round(percentile(matched_3, 10) * 100, 1),
        })

    swing_low = min(closes[-63:])
    one_month_down = price * math.exp(-sd(21))
    invalid = max(swing_low if swing_low < price * 0.985 else 0.0, one_month_down)
    invalid_basis = "3-month low" if invalid == swing_low else "one normal month down"
    averages_above = sorted((value, label) for value, label in ((current_ma50, "50-day average"), (current_ma200, "200-day average")) if value > price)
    promote, promote_basis = averages_above[0] if averages_above else (max(closes[-21:]), "1-month high")

    score, reasons = 0, []
    up_12, base = own.get("own_f12_up_pct"), own["base_f12_up_pct"]
    own["own_edge_pts"] = up_12 - base if up_12 is not None else None
    if up_12 is None:
        reasons.append(f"too few past setups like this since {own['own_since']} to judge")
    elif own["own_edge_pts"] >= 10:
        score += 1; reasons.append(f"in past setups like this it was higher a year later {up_12}% of the time, above its normal {base}%")
    elif own["own_edge_pts"] <= -10:
        score -= 1; reasons.append(f"in past setups like this it was higher a year later only {up_12}% of the time, below its normal {base}%")
    else:
        reasons.append(f"past setups like this ({up_12}% higher a year later) were no better than its normal {base}%")
    if trend == "Uptrend":
        score += 1; reasons.append("the trend is up")
    elif trend == "Downtrend":
        score -= 1; reasons.append("the trend is down (below the 200-day, 50-day falling)")
    elif trend == "Pullback in uptrend":
        reasons.append("it is pulling back inside an uptrend (above the 200-day, 50-day falling)")
    else:
        reasons.append("it is below its 200-day but the 50-day is turning up")
    if regime["active"] and row["history_12m"] > price * 1.05:
        score += 1; reasons.append("the S&P's typical path after setups like today lifts it")
    if analyst is not None and not analyst_stale and 0.2 <= row["target_sigmas_12m"] <= 1.2 and row["buy_pct"] >= 70:
        score += 1; reasons.append(f"{row['buy_pct']}% of analysts rate it a buy, with a reachable average target")
    action = "Probe" if score >= 2 and trend in ("Uptrend", "Pullback in uptrend") else "Watch: odds good, trend not yet" if score >= 2 else "Watch" if score >= 0 else "Avoid for now"
    explanation = "; ".join(reasons[:5])
    row.update({
        "ma50": round(current_ma50, 2), "ma200": round(current_ma200, 2),
        "vs_ma200_pct": round((price / current_ma200 - 1) * 100, 1),
        "dd_52w_pct": round(drawdown[-1] * 100, 1), "trend": trend,
        "swing_low_3m": round(swing_low, 2), "promote_level": round(promote, 2),
        "invalid_level": round(invalid, 2), "promote_basis": promote_basis,
        "invalid_basis": invalid_basis, "evidence_score": score, "action": action,
        "action_why": explanation[:1].upper() + explanation[1:] + ".", **own,
    })

    # How it is doing.
    def back(count: int) -> float | None:
        return round((price / closes[-1 - count] - 1) * 100, 1) if len(closes) > count else None
    year_start = next((index for index, day in enumerate(days) if day.year == asof.year), None)
    row.update({"ret_1d": back(1), "ret_1w": back(5), "ret_1m": back(21), "ret_3m": back(63), "ret_1y": back(TRADING_YEAR),
                "ret_ytd": round((price / closes[year_start - 1] - 1) * 100, 1) if year_start else None})

    # Crash replay and size that matches the drawdown of a full SPY position.
    crash = crash or {"crash_n": 0}
    spy_crash = spy_crash or {}
    row.update(crash)
    row.update({"spy_" + key: value for key, value in spy_crash.items()})
    if crash.get("crash_n", 0) >= 3 and crash.get("crash_dd3_median", 0) < 0 and spy_crash.get("crash_dd3_median"):
        row["size_vs_spy"] = round(max(0.1, min(1.5, spy_crash["crash_dd3_median"] / crash["crash_dd3_median"])), 2)
        row["size_basis"] = "crash"
    else:
        row["size_vs_spy"] = round(max(0.1, min(1.5, 0.16 / max(1e-6, sigma_long * math.sqrt(TRADING_YEAR)))), 2)
        row["size_basis"] = "volatility"

    # Bottom zones and the plan built on them.
    macro = macro or {}
    bars = [(ohlc or {})[d] for d in days if d in (ohlc or {})]
    if not bars:
        bars = [[c, c, c, c, 0] for c in closes[-520:]]
    zones, profile, _ = bottom_zones(price, beta, macro, bars, closes, current_ma200, min(closes[-252:]), crash)
    primary, secondary = pick_zones(zones, price)
    week = math.exp(-sd(5))
    if primary:
        limit, limit_basis = primary["hi"], "top of the main bottom zone (" + ", ".join(primary["methods"]) + ")"
    else:
        limit, limit_basis = price * math.exp(-sd(21)), "one normal month down"
    limit = min(limit, price * 0.98)
    if secondary:
        limit2, limit2_basis = min(secondary["hi"], limit * 0.98), "top of the deeper zone (" + ", ".join(secondary["methods"]) + ")"
        floor = secondary["lo"]
    else:
        limit2, limit2_basis = limit * math.exp(-sd(21) * 0.6), "a deeper fill below the main zone"
        floor = min(primary["lo"] if primary else limit, limit2)
    stop = min(floor * week, limit2 * 0.98)
    for z in zones:
        z["role"] = "main" if z is primary else "deeper" if z is secondary else "other"
    row.update({"plan_limit": round(limit, 2), "plan_limit_basis": limit_basis, "plan_limit2": round(limit2, 2), "plan_limit2_basis": limit2_basis,
                "plan_stop": round(stop, 2), "plan_confirm": round(promote, 2), "invalid_level": round(stop, 2),
                "invalid_basis": "one normal week below the lowest zone it buys",
                "bottom_zones": zones[:6], "volume_profile": profile,
                "zone_backtest": zone_backtest(closes, [(ohlc or {})[d] for d in days if d in (ohlc or {})]),
                "macro_base_level": round(price * (1 + beta * macro["base_extra_pct"] / 100), 2) if macro.get("base_extra_pct") is not None else None,
                "macro_stress_level": round(price * max(0.05, 1 + beta * macro["stress_extra_pct"] / 100), 2) if macro.get("stress_extra_pct") is not None else None})
    return row, cone, history


def write_dataset(name: str, rows: list[dict], generated_at: str, price_asof: str, analog_episodes: int, regime: dict, analyst_snapshot: str, method: str) -> None:
    payload = {
        "rows": rows,
        "meta": {
            "generated_at": generated_at,
            "price_asof": price_asof,
            "generator": "scripts/build_data.py",
            "sources": ["Yahoo Finance daily adjusted prices and volume", "FRED BAA10Y (corporate credit spread)", "config/analyst_targets.json"],
            "macro_bottom": MACRO_META,
            "beta_window": "5 years",
            "sp500_analog_episodes": analog_episodes,
            "sp500_setup": regime,
            "analyst_snapshot_at": analyst_snapshot,
            "analyst_max_age_days": ANALYST_MAX_AGE_DAYS,
            "method": method,
        },
    }
    target = DATA / f"{name}.json"
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, separators=(",", ":"), allow_nan=False), encoding="utf-8")
    os.replace(temporary, target)


SECTOR_NAMES = {"XLK": "Technology", "XLF": "Financials", "XLE": "Energy", "XLV": "Health care", "XLY": "Consumer discretionary",
                "XLP": "Consumer staples", "XLI": "Industrials", "XLU": "Utilities", "XLB": "Materials", "XLRE": "Real estate",
                "XLC": "Communication", "SMH": "Semiconductors"}
INDEX_NAMES = {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Russell 2000 (small caps)", "DIA": "Dow 30"}


def http_get(url: str, accept: str = "*/*", timeout: int = 25) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (market-analysis)", "Accept": accept})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (OSError, urllib.error.URLError):
            time.sleep(1 + attempt)
    return None


NEWS_DESKS = {"Reuters", "Bloomberg", "Associated Press", "AP", "CNBC", "Barron's", "The Wall Street Journal", "MarketWatch", "Financial Times",
              "Investopedia", "Yahoo Finance", "TheStreet", "Benzinga", "Axios", "Fortune", "Business Insider", "Quartz", "Forbes"}
RELEASES = {"PR Newswire", "Business Wire", "GlobeNewswire", "Accesswire", "ACCESS Newswire"}
LISTICLE = re.compile(r"^(\d+|three|two|five|top)\b.*\bstocks?\b|\bbetter buy\b|\bshould you\b|\bis it time to\b|\bbillionaire\b|\bmillionaire\b|"
                      r"\bstocks? to (buy|watch|own)\b|\bhere'?s (why|what|how)\b|\bif you'?d invested\b|\bpenny stocks?\b|\bdividend stocks?\b", re.I)


def fetch_headlines(symbol: str, limit: int = 6) -> tuple[list[dict], str | None]:
    """Headlines about this symbol from Yahoo Finance search. Kept only when the headline names the company or
    ticker; listicles are dropped; news desks and company
    releases rank above opinion sites. Returns the headlines and the company's short name."""
    raw = http_get(f"https://query1.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(symbol)}&newsCount=25&quotesCount=1", "application/json")
    try:
        payload = json.loads(raw) if raw else {}
    except ValueError:
        payload = {}
    quote = next((q for q in payload.get("quotes") or [] if q.get("symbol") == symbol), {})
    name = quote.get("shortname") or quote.get("longname")
    words = [w for w in re.split(r"[\s,.]+", re.sub(r"\b(inc|corp|corporation|co|company|holdings|group|plc|ltd|the|class [a-z]|etf|trust|spdr|ishares|select sector)\b", " ", (name or "").lower())) if len(w) > 2][:2]
    keep = []
    for item in payload.get("news") or []:
        title = (item.get("title") or "").strip()
        if not title or LISTICLE.search(title):
            continue
        tickers = item.get("relatedTickers") or []
        low = title.lower()
        named = re.search(r"\b" + re.escape(symbol.lower()) + r"\b", low) or (words and all(w in low for w in words[:1]))
        publisher = item.get("publisher") or ""
        tier = 0 if publisher in NEWS_DESKS else 1 if publisher in RELEASES else 2
        # Opinion sites and releases must name the company or ticker; news desks may also qualify by a tight ticker tag.
        if not named:  # ticker tags are too loose to trust on their own
            continue
        link = item.get("link") or ""
        stamp = item.get("providerPublishTime")
        keep.append({"title": title[:220], "publisher": publisher, "kind": ["news", "company release", "opinion"][tier],
                     "link": link if link.startswith("https://") else None,
                     "published": dt.datetime.fromtimestamp(stamp, dt.timezone.utc).isoformat().replace("+00:00", "Z") if stamp else None, "_tier": tier})
    keep.sort(key=lambda x: (x["_tier"], -(dt.datetime.fromisoformat(x["published"].replace("Z", "+00:00")).timestamp() if x["published"] else 0)))
    for k in keep:
        k.pop("_tier")
    return keep[:limit], name


def fetch_earnings_calendar(symbols: set[str], days: int = 60, back: int = 100) -> dict[str, dict]:
    """Next and last earnings per symbol from Nasdaq's public earnings calendar, one request per weekday.
    The last report carries actual vs forecast EPS; when no next date is scheduled, an estimate of last + 91 days."""
    found: dict[str, dict] = {}
    last: dict[str, dict] = {}
    today = dt.datetime.now(dt.timezone.utc).date()
    past = [today - dt.timedelta(days=k) for k in range(1, back) if (today - dt.timedelta(days=k)).weekday() < 5]
    dates = [today + dt.timedelta(days=k) for k in range(days) if (today + dt.timedelta(days=k)).weekday() < 5]
    def one(day: dt.date):
        raw = http_get(f"https://api.nasdaq.com/api/calendar/earnings?date={day.isoformat()}", "application/json")
        try:
            rows = (json.loads(raw).get("data") or {}).get("rows") or [] if raw else []
        except ValueError:
            rows = []
        return day, rows
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for day, rows in pool.map(one, past):
            for r in rows:
                sym = (r.get("symbol") or "").replace(".", "-")
                if sym in symbols and (sym not in last or day.isoformat() > last[sym]["date"]):
                    last[sym] = {"date": day.isoformat(), "eps": r.get("eps") or None, "eps_forecast": r.get("epsForecast") or None,
                                 "surprise_pct": r.get("surprise") if r.get("surprise") not in (None, "", "N/A") else None}
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for day, rows in pool.map(one, dates):
            for r in rows:
                sym = (r.get("symbol") or "").replace(".", "-")
                if sym in symbols and (sym not in found or day.isoformat() < found[sym]["date"]):
                    timing = {"time-after-hours": "after close", "time-pre-market": "before open"}.get(r.get("time"), "time not set")
                    found[sym] = {"date": day.isoformat(), "timing": timing, "eps_forecast": r.get("epsForecast") or None}
    out = {}
    for sym in symbols:
        nxt, prev = found.get(sym), last.get(sym)
        if not nxt and prev:
            est = dt.date.fromisoformat(prev["date"]) + dt.timedelta(days=91)
            nxt = {"date": est.isoformat(), "timing": "estimated from the last report", "eps_forecast": None, "estimated": True}
        if nxt or prev:
            out[sym] = {**(nxt or {}), "last": prev}
    return out


def build_market(rows: list[dict], market_prices: dict, macro: dict, headlines: list[dict]) -> dict:
    by = {r["symbol"]: r for r in rows}
    spx = market_prices["^GSPC"]; vix = market_prices["^VIX"]
    spx_days = sorted(spx); vix_days = sorted(vix)
    def chg(series: dict, days: list, n: int) -> float | None:
        return round((series[days[-1]] / series[days[-1 - n]] - 1) * 100, 1) if len(days) > n else None
    spx_peak = max(spx[d] for d in spx_days[-252:])
    indices = [{"symbol": s, "name": n, "price": by[s]["price"], "ret_1d": by[s]["ret_1d"], "ret_1w": by[s]["ret_1w"], "ret_1m": by[s]["ret_1m"],
                "ret_ytd": by[s]["ret_ytd"], "dd_52w_pct": by[s]["dd_52w_pct"], "trend": by[s]["trend"]} for s, n in INDEX_NAMES.items() if s in by]
    ratio = spx[spx_days[-1]] / by["SPY"]["price"] if "SPY" in by else None
    spx_zones = [{"role": z["role"], "lo": round(z["lo"] * ratio), "hi": round(z["hi"] * ratio), "score": z["score"], "methods": z["methods"]}
                 for z in (by.get("SPY", {}).get("bottom_zones") or []) if ratio and z["role"] != "other"]
    sectors = sorted(({"symbol": s, "name": n, "ret_1w": by[s]["ret_1w"], "ret_1m": by[s]["ret_1m"], "ret_3m": by[s]["ret_3m"],
                       "vs_ma200_pct": by[s]["vs_ma200_pct"], "trend": by[s]["trend"]} for s, n in SECTOR_NAMES.items() if s in by),
                     key=lambda x: -(x["ret_1m"] or -999))
    stocks = [r for r in rows if r.get("group") not in ("Market & sector ETFs", "Leveraged ETFs")]
    movers_up = sorted(stocks, key=lambda r: -(r["ret_1w"] or -999))[:8]
    movers_dn = sorted(stocks, key=lambda r: (r["ret_1w"] or 999))[:8]
    pick = lambda r: {"symbol": r["symbol"], "ret_1w": r["ret_1w"], "ret_1m": r["ret_1m"], "action": r["action"]}
    breadth = {"n": len(stocks), "above_200d_pct": round(sum(r["vs_ma200_pct"] > 0 for r in stocks) / len(stocks) * 100),
               "uptrend_pct": round(sum(r["trend"] == "Uptrend" for r in stocks) / len(stocks) * 100),
               "near_high_pct": round(sum(r["dd_52w_pct"] > -5 for r in stocks) / len(stocks) * 100)}
    setups = []
    for r in rows:
        if r["action"] not in ("Probe", "Watch: odds good, trend not yet"):
            continue
        risk = r["plan_limit"] - r["plan_stop"]
        if risk <= 0:
            continue
        reward = r["m3_hi"] - r["plan_limit"]
        main = next((z for z in r.get("bottom_zones") or [] if z["role"] == "main"), None)
        setups.append({"symbol": r["symbol"], "group": r["group"], "action": r["action"], "price": r["price"], "entry": r["plan_limit"],
                       "stop": r["plan_stop"], "target": r["m3_hi"], "reward_risk": round(reward / risk, 2), "zone_score": main["score"] if main else 0,
                       "entry_gap_pct": round((r["plan_limit"] / r["price"] - 1) * 100, 1), "trend": r["trend"]})
    setups.sort(key=lambda x: (x["action"] != "Probe", -(x["zone_score"] >= 3), -x["reward_risk"]))
    return {"asof": spx_days[-1].isoformat(), "spx": round(spx[spx_days[-1]], 2), "spx_ret_1d": chg(spx, spx_days, 1), "spx_ret_1w": chg(spx, spx_days, 5),
            "spx_ret_1m": chg(spx, spx_days, 21), "spx_off_high_pct": round((spx[spx_days[-1]] / spx_peak - 1) * 100, 1),
            "vix": round(vix[vix_days[-1]], 2), "vix_chg_1w": round(vix[vix_days[-1]] - vix[vix_days[-6]], 2) if len(vix_days) > 5 else None,
            "indices": indices, "spx_zones": spx_zones, "macro": macro, "sectors": sectors, "breadth": breadth,
            "movers_up": [pick(r) for r in movers_up], "movers_down": [pick(r) for r in movers_dn], "setups": setups[:15],
            "action_counts": {a: sum(r["action"] == a for r in rows) for a in ("Probe", "Watch: odds good, trend not yet", "Watch", "Avoid for now")},
            "headlines": headlines}


def main() -> None:
    universe, prior_by_symbol = load_universe()
    analyst_payload = json.loads(ANALYST_TARGETS.read_text(encoding="utf-8"))
    analyst_snapshot = dt.date.fromisoformat(analyst_payload["synced_at"])
    analyst_targets = analyst_payload["targets"]
    symbols = [row["symbol"] for row in universe]
    starts = {symbol: dt.date(2017, 1, 1) for symbol in symbols + ["SPY"]}
    starts.update({"^GSPC": dt.date(1990, 1, 1), "^VIX": dt.date(1990, 1, 1)})
    market: dict[str, dict[dt.date, float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_symbol, symbol, start): symbol for symbol, start in starts.items()}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            market[symbol] = future.result()
            print(f"fetched {symbol}: {len(market[symbol])} closes")
    spx_path, episodes, regime = sp500_analog_path(market["^GSPC"], market["^VIX"])
    starts_ = sell_off_starts(market["^GSPC"], market["^VIX"])
    long_history: dict[str, dict[dt.date, float]] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futures = {pool.submit(fetch_symbol, symbol, dt.date(1985, 1, 1), "_long"): symbol for symbol in symbols + ["SPY"]}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                long_history[symbol] = future.result()
            except RuntimeError as exc:
                print(f"long history unavailable for {symbol}: {exc}")
    crash_by_symbol = {symbol: crash_replay(long_history.get(symbol, {}), starts_) for symbol in symbols + ["SPY"]}
    macro = macro_bottom_stats(market["^GSPC"], market["^VIX"], fetch_fred("BAA10Y"))
    print("macro bottom stats:", json.dumps(macro))
    MACRO_META.update(macro)
    summary_rows, cone_rows, history_rows = [], [], []
    for symbol in symbols:
        row, cone, history = build_symbol(symbol, prior_by_symbol[symbol], analyst_targets.get(symbol), analyst_snapshot, market[symbol], market["SPY"], spx_path, regime, crash_by_symbol.get(symbol), crash_by_symbol.get("SPY"), macro, load_ohlc(symbol))
        summary_rows.append(row); cone_rows.extend(cone); history_rows.extend(history)
        print(f"built {symbol}: {row['action']} ({row['trend']}, beta {row['beta']})")
    if len(summary_rows) != len(symbols):
        raise RuntimeError(f"Built {len(summary_rows)} of {len(symbols)} symbols")
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    price_asof = max(row["asof"] for row in summary_rows)
    write_dataset("outlook_summary", summary_rows, generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(), "Volatility ranges, five-year beta-scaled S&P analog path, trend and evidence action rules.")
    write_dataset("outlook_cone", cone_rows, generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(), "Forward percentile bands by trading day; no directional drift assumed.")
    write_dataset("outlook_history", history_rows, generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(), "Last 126 aligned trading days with 50-day and 200-day moving averages.")
    # News and earnings: best effort; a failed source leaves those fields empty and never blocks the build.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        fetched = dict(zip(symbols, pool.map(fetch_headlines, symbols)))
    news = {sym: v[0] for sym, v in fetched.items()}
    names = {sym: v[1] for sym, v in fetched.items()}
    market_news, seen = [], set()
    for src in ("^GSPC", "SPY", "QQQ"):
        for h in fetch_headlines(src, 12)[0]:
            if h["title"] not in seen:
                seen.add(h["title"]); market_news.append(h)
    market_news.sort(key=lambda x: x["published"] or "", reverse=True)
    earnings = fetch_earnings_calendar(set(symbols))
    news_rows = [{"symbol": sym, "name": names.get(sym), "headlines": news.get(sym) or [], "earnings": earnings.get(sym)} for sym in symbols]
    # Zone track record across the universe.
    agg = {k: sum((r.get("zone_backtest") or {}).get(k, 0) for r in summary_rows) for k in ("samples", "reached", "held", "base_reached", "base_held", "strong_reached", "strong_held", "strong_base_reached", "strong_base_held")}
    pct = lambda a, b: round(a / b * 100) if b else None
    track = {**agg, "held_pct": pct(agg["held"], agg["reached"]), "base_held_pct": pct(agg["base_held"], agg["base_reached"]),
             "strong_held_pct": pct(agg["strong_held"], agg["strong_reached"]), "strong_base_held_pct": pct(agg["strong_base_held"], agg["strong_base_reached"]), "reached_pct": pct(agg["reached"], agg["samples"]),
             "groups": {g: (lambda rs: {"samples": sum(r["zone_backtest"]["samples"] for r in rs), "held_pct": pct(sum(r["zone_backtest"]["held"] for r in rs), sum(r["zone_backtest"]["reached"] for r in rs))})([r for r in summary_rows if r.get("group") == g])
                        for g in sorted({r.get("group") for r in summary_rows})}}
    print("zone track record:", json.dumps({k: v for k, v in track.items() if k != "groups"}))
    write_dataset("news", news_rows, generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(), "Yahoo Finance RSS headlines per symbol; next earnings date from Nasdaq's public earnings calendar (next 60 days).")
    market_row = build_market(summary_rows, market, macro, market_news[:12]); market_row["zone_track"] = track
    write_dataset("market", [market_row], generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(),
                  "Market overview built from the same rows: indices, S&P bottom zones in index points, sectors, breadth, movers, ranked setups and headlines.")
    print(f"news for {sum(bool(n) for n in news.values())} symbols; earnings dates for {len(earnings)}; {len(market_news)} market headlines")
    print(f"built {len(summary_rows)} symbols through {price_asof}; {episodes} S&P analog episodes")


if __name__ == "__main__":
    main()
