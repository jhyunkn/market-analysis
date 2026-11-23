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
from zoneinfo import ZoneInfo
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
MIN_CLUSTERS = 8  # independent clusters needed before setups-like-this history counts toward a score
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


PARTIAL_DROPPED: set[str] = set()


def cache_path(symbol: str, tag: str = "") -> Path:
    safe = symbol.replace("^", "_").replace("=", "_").replace("/", "_")
    return CACHE / f"{safe}{tag}.json"


def fetch_symbol(symbol: str, start: dt.date, tag: str = "") -> dict[dt.date, float]:
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = cache_path(symbol, tag)
    ohlc_file = cache_path(symbol, "_ohlc")
    def fresh(path: Path) -> bool:
        """Cached for 3 hours, except a copy saved during the trading day is stale once the day's close is in."""
        if time.time() - path.stat().st_mtime >= 3 * 3600:
            return False
        et = ZoneInfo("America/New_York")
        saved, now_et = dt.datetime.fromtimestamp(path.stat().st_mtime, et), dt.datetime.now(et)
        return not (saved.date() == now_et.date() and saved.time() < dt.time(16, 20) <= now_et.time())
    if cached.exists() and fresh(cached) and (tag or ohlc_file.exists()):
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
            # During the trading day Yahoo returns today's unfinished bar; the analysis uses completed closes only
            # (live prices are shown separately), so drop it until shortly after the 4 PM ET close.
            now_et = dt.datetime.now(ZoneInfo("America/New_York"))
            if prices and max(prices) == now_et.date() and now_et.weekday() < 5 and now_et.time() < dt.time(16, 20):
                partial = max(prices)
                prices.pop(partial, None)
                PARTIAL_DROPPED.add(symbol)
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
                if symbol in PARTIAL_DROPPED:
                    bars.pop(now_et.date().isoformat(), None)
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


HY_UNSETTLED, HY_JUMP = 3.50, 0.20  # high-yield option-adjusted spread, points: level, or rise over 5 observations


def macro_bottom_stats(spx: dict[dt.date, float], vix: dict[dt.date, float], baa: dict[dt.date, float], hy: dict[dt.date, float] | None = None) -> dict:
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
    hy = hy or {}
    hy_days = sorted(hy)
    hy_now = hy[hy_days[-1]] if hy_days else None
    hy_chg = round(hy_now - hy[hy_days[-6]], 2) if len(hy_days) >= 6 else None
    hy_unsettled = hy_now is not None and (hy_now >= HY_UNSETTLED or (hy_chg is not None and hy_chg >= HY_JUMP))
    shown = "unsettled" if hy_unsettled else state
    return {"credit_spread": latest_baa, "credit_state": shown, "credit_state_baa": state, "credit_threshold": CREDIT_STRESS,
            "hy_spread": hy_now, "hy_date": hy_days[-1].isoformat() if hy_days else None, "hy_chg_5obs": hy_chg, "hy_threshold": HY_UNSETTLED,
            "hy_since": hy_days[0].isoformat() if hy_days else None,
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


FIB_LEVELS = (0.236, 0.382, 0.5, 0.618, 0.786)


def fib_levels(bars: list) -> list[float]:
    """Retracements of the swing between the window's highest high and lowest low: measured down from the high when
    the high came last (a pullback in a rise), up from the low when the low came last (a bounce in a fall)."""
    highs, lows = [b[1] for b in bars], [b[2] for b in bars]
    hi, lo = max(highs), min(lows)
    if hi <= lo:
        return []
    if highs.index(hi) > lows.index(lo):
        return [hi - (hi - lo) * f for f in FIB_LEVELS]
    return [lo + (hi - lo) * f for f in FIB_LEVELS]


def fib_backtest(closes: list[float], bars: list, step: int = 15) -> dict:
    """Walk forward, like the zone test: at past dates when the stock was 5%+ below its 6-month high, take the nearest
    Fibonacci level of the past 126 sessions at least 1% below the price, then check the low of the next 63 sessions.
    'Held' means price reached the level and its low stayed within 3% below it. A level at a random distance 2-20%
    below the price is the baseline."""
    import random
    rng = random.Random(9)
    n = min(len(closes), len(bars))
    closes, bars = closes[-n:], bars[-n:]
    out = {"samples": 0, "reached": 0, "held": 0, "base_reached": 0, "base_held": 0}
    for i in range(130, n - 64, step):
        price = closes[i]
        window = bars[i - 125:i + 1]
        if price > max(b[1] for b in window) * 0.95:
            continue
        below = [x for x in fib_levels(window) if x < price * 0.99]
        if not below:
            continue
        level = max(below)
        low = min(b[2] for b in bars[i + 1:i + 64])
        out["samples"] += 1
        reached = low <= level; out["reached"] += reached; out["held"] += reached and low >= level * 0.97
        rnd = price * (1 - rng.uniform(0.02, 0.20))
        b_reached = low <= rnd; out["base_reached"] += b_reached; out["base_held"] += b_reached and low >= rnd * 0.97
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


CRASH_PATH_DAYS = [0, 5, 10, 21, 42, 63, 84, 105, 126, 168, 210, 252]


def crash_replay(prices: dict[dt.date, float], starts: list[dt.date]) -> dict:
    """Next-year return and worst drop within 3 months after each sell-off start the symbol traded through."""
    days = sorted(prices)
    if not days:
        return {"crash_n": 0}
    closes = [prices[day] for day in days]
    forward, drops, paths = [], [], []
    position = 0
    for start in starts:
        while position < len(days) and days[position] < start:
            position += 1
        if position >= len(days) or (days[position] - start).days > 5 or position + TRADING_YEAR >= len(closes):
            continue
        base = closes[position]
        forward.append(closes[position + TRADING_YEAR] / base - 1)
        drops.append(min(closes[position:position + 64]) / base - 1)
        paths.append([closes[position + k] / base - 1 for k in CRASH_PATH_DAYS])
    out = {"crash_n": len(forward), "crash_since": days[0].year}
    if len(paths) >= 3:
        cols = list(zip(*paths))
        out["crash_path"] = {"days": CRASH_PATH_DAYS, "median": [round(statistics.median(c) * 100, 1) for c in cols],
                             "p25": [round(percentile(list(c), 25) * 100, 1) for c in cols], "p75": [round(percentile(list(c), 75) * 100, 1) for c in cols]}
    if forward:
        out.update({
            "crash_f12_median": round(statistics.median(forward) * 100, 1),
            "crash_f12_worst": round(min(forward) * 100, 1),
            "crash_f12_up_pct": round(sum(value > 0 for value in forward) / len(forward) * 100),
            "crash_dd3_median": round(statistics.median(drops) * 100, 1),
            "crash_dd3_worst": round(min(drops) * 100, 1),
        })
    return out


def build_symbol(symbol: str, prior: dict, analyst: dict | None, analyst_snapshot: dt.date, prices: dict[dt.date, float], spy: dict[dt.date, float], spx_path: list[float], regime: dict, crash: dict | None = None, spy_crash: dict | None = None, macro: dict | None = None, ohlc: dict | None = None, long_prices: dict | None = None) -> tuple[dict, list[dict], list[dict]]:
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
        "name": prior.get("name", symbol),
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
    if analyst is not None and analyst.get("as_of"):
        analyst_snapshot = dt.date.fromisoformat(analyst["as_of"])
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

    # Setups like this: days in the same state (similar distance below the 52-week high, same side of the 200-day).
    # Matched days bunch together, so each episode (a run of matches with gaps under a month) counts once, from its
    # first day; episodes whose 12-month outcome windows overlap (first days under 252 sessions apart) are merged into
    # one cluster and averaged. The baseline uses non-overlapping 12-month windows on the same history.
    # It runs on the stock's full price history (since 1985 or its listing) when available, which gives enough
    # independent clusters for most stocks; today's state comes from the latest prices either way.
    lp = sorted((long_prices or {}).items())
    if len(lp) > len(closes):
        h_days, h_closes = [d for d, _ in lp], [v for _, v in lp]
    else:
        h_days, h_closes = days, closes
    h_ma200 = moving_average(h_closes, 200)
    h_dd = [c / max(h_closes[max(0, i - 251):i + 1]) - 1 for i, c in enumerate(h_closes)]
    match_indexes: list[int] = []
    for index in range(252, len(h_closes) - 252):
        if h_ma200[index] is not None and abs(h_dd[index] - drawdown[-1]) <= 0.05 and ((h_closes[index] > float(h_ma200[index])) == above_200):
            match_indexes.append(index)
    ep_starts = [i for pos, i in enumerate(match_indexes) if pos == 0 or i - match_indexes[pos - 1] > 21]
    clusters: list[list[int]] = []
    for i in ep_starts:
        if clusters and i - clusters[-1][-1] < 252:
            clusters[-1].append(i)
        else:
            clusters.append([i])
    cl12 = [statistics.mean(h_closes[i + 252] / h_closes[i] - 1 for i in c) for c in clusters]
    cl3 = [statistics.mean(h_closes[i + 63] / h_closes[i] - 1 for i in c) for c in clusters]
    base_rates, base_windows = [], 0
    for offset in range(252, 504, 21):
        wins = [h_closes[i + 252] / h_closes[i] - 1 for i in range(offset, len(h_closes) - 252, 252)]
        if wins:
            base_rates.append(sum(w > 0 for w in wins) / len(wins)); base_windows = max(base_windows, len(wins))
    own = {
        "own_days": len(match_indexes),
        "own_episodes": len(ep_starts),
        "own_clusters": len(clusters),
        "own_since": h_days[252].year,
        "base_f12_up_pct": round(statistics.mean(base_rates) * 100) if base_rates else None,
        "base_windows": base_windows,
        "own_f12_up_n": sum(v > 0 for v in cl12),
        "own_min_clusters": MIN_CLUSTERS,
    }
    if len(clusters) >= MIN_CLUSTERS:
        own.update({
            "own_f12_median": round(statistics.median(cl12) * 100, 1),
            "own_f12_up_pct": round(sum(value > 0 for value in cl12) / len(cl12) * 100),
            "own_f3_median": round(statistics.median(cl3) * 100, 1),
            "own_f3_up_pct": round(sum(value > 0 for value in cl3) / len(cl3) * 100),
            "own_f3_p10": round(min(cl3) * 100, 1),
        })

    swing_low = min(closes[-63:])
    averages_above = sorted((value, label) for value, label in ((current_ma50, "50-day average"), (current_ma200, "200-day average")) if value > price)
    promote, promote_basis = averages_above[0] if averages_above else (max(closes[-21:]), "1-month high")

    score, reasons = 0, []
    up_12, base = own.get("own_f12_up_pct"), own["base_f12_up_pct"]
    own["own_edge_pts"] = up_12 - base if up_12 is not None and base is not None else None
    if up_12 is None:
        reasons.append(f"insufficient independent sample: {own['own_clusters']} separate past setups like this since {own['own_since']} (needs {MIN_CLUSTERS}), so history is not scored")
    elif own["own_edge_pts"] is not None and own["own_edge_pts"] >= 10:
        score += 1; reasons.append(f"in past setups like this it was higher a year later {up_12}% of the time, above its normal {base}%")
    elif own["own_edge_pts"] is not None and own["own_edge_pts"] <= -10:
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
        "promote_basis": promote_basis, "evidence_score": score, "action": action,
        "action_why": explanation[:1].upper() + explanation[1:] + ".", **own,
    })

    # How it is doing.
    def back(count: int) -> float | None:
        return round((price / closes[-1 - count] - 1) * 100, 1) if len(closes) > count else None
    year_start = next((index for index, day in enumerate(days) if day.year == asof.year), None)
    row["mom12_1"] = round((closes[-22] / closes[-253] - 1) * 100, 1) if len(closes) > 253 else None
    row.update({"ret_1d": back(1), "ret_1w": back(5), "ret_1m": back(21), "ret_3m": back(63), "ret_1y": back(TRADING_YEAR),
                "ret_ytd": round((price / closes[year_start - 1] - 1) * 100, 1) if year_start else None})

    # Crash replay.
    crash = crash or {"crash_n": 0}
    spy_crash = spy_crash or {}
    row.update(crash)
    row.update({"spy_" + key: value for key, value in spy_crash.items()})

    # Bottom zones.
    macro = macro or {}
    bars = [(ohlc or {})[d] for d in days if d in (ohlc or {})]
    if not bars:
        bars = [[c, c, c, c, 0] for c in closes[-520:]]
    zones, profile, _ = bottom_zones(price, beta, macro, bars, closes, current_ma200, min(closes[-252:]), crash)
    primary, secondary = pick_zones(zones, price)
    for z in zones:
        z["role"] = "main" if z is primary else "deeper" if z is secondary else "other"
    row.update({"plan_confirm": round(promote, 2),
                "bottom_zones": zones[:6], "volume_profile": profile,
                "zone_backtest": zone_backtest(closes, [(ohlc or {})[d] for d in days if d in (ohlc or {})]),
                "fib_backtest": fib_backtest(closes, [(ohlc or {})[d] for d in days if d in (ohlc or {})]),
                "macro_base_level": round(price * (1 + beta * macro["base_extra_pct"] / 100), 2) if macro.get("base_extra_pct") is not None else None,
                "macro_stress_level": round(price * max(0.05, 1 + beta * macro["stress_extra_pct"] / 100), 2) if macro.get("stress_extra_pct") is not None else None})
    row["_cumvar"] = cumulative_variance  # internal: used to translate leveraged funds; removed before writing
    row["_beta"] = beta
    return row, cone, history


LEVERAGED = ROOT / "config" / "leveraged.json"


def lev_map(lev_close: float, u_close: float, u_level: float, leverage: float) -> float:
    """One-session estimate of the leveraged fund's price if the underlying moved to u_level today."""
    return lev_close * (1 + leverage * (u_level / u_close - 1))


def stress_sim(u_prices: dict[dt.date, float], irx: dict[dt.date, float], starts: list[dt.date], leverage: float, fee: float, fund: dict[dt.date, float] | None = None) -> dict:
    """Simulated daily-reset fund on the underlying's adjusted closes since the underlying began, rebalanced each close:
    S[t] = S[t-1] * (1 + L*r[t] - ((L-1)*IRX[t] + fee) / 252), IRX carried forward. Entered at each sell-off start's close,
    held 252 sessions. Starts under 252 sessions apart form one cluster (mean within, median across clusters).
    Swap spreads and slippage are not modeled. A tail-risk stress test, not a probability."""
    days = sorted(u_prices)
    if len(days) < 600:
        return {}
    closes = [u_prices[d] for d in days]
    rate, rates, irx_days, j = 0.0, [], sorted(irx), 0
    for d in days:
        while j < len(irx_days) and irx_days[j] <= d:
            rate = irx[irx_days[j]] / 100; j += 1
        rates.append(rate)
    sim = [1.0]
    for t in range(1, len(days)):
        r = closes[t] / closes[t - 1] - 1
        sim.append(max(0.0, sim[-1] * (1 + leverage * r - ((leverage - 1) * rates[t] + fee) / 252)))
    obs = []
    pos = 0
    for st in starts:
        while pos < len(days) and days[pos] < st:
            pos += 1
        if pos >= len(days) or (days[pos] - st).days > 5 or pos + 252 >= len(days) or sim[pos] <= 0:
            continue
        obs.append((pos, sim[pos + 252] / sim[pos] - 1))
    clusters: list[list[tuple[int, float]]] = []
    for o in obs:
        if clusters and o[0] - clusters[-1][-1][0] < 252:
            clusters[-1].append(o)
        else:
            clusters.append([o])
    if not clusters:
        return {}
    means = [statistics.mean(x[1] for x in c) for c in clusters]
    check = {}
    if fund:
        common = [i for i, d in enumerate(days) if d in fund and i > 0 and days[i - 1] in fund]
        if len(common) > 500:
            a = [sim[i] / sim[i - 1] - 1 for i in common if sim[i - 1] > 0]
            b = [fund[days[i]] / fund[days[i - 1]] - 1 for i in common if sim[i - 1] > 0]
            ma_, mb_ = statistics.mean(a), statistics.mean(b)
            cov = sum((x - ma_) * (y - mb_) for x, y in zip(a, b)); va = sum((x - ma_) ** 2 for x in a); vb = sum((y - mb_) ** 2 for y in b)
            yrs = (days[common[-1]] - days[common[0] - 1]).days / 365.25
            cagr = lambda g: (g ** (1 / yrs) - 1) * 100  # noqa: E731
            check = {"since": days[common[0] - 1].isoformat(), "daily_corr": round(cov / math.sqrt(va * vb), 3),
                     "sim_cagr_pct": round(cagr(sim[common[-1]] / sim[common[0] - 1]), 1), "real_cagr_pct": round(cagr(fund[days[common[-1]]] / fund[days[common[0] - 1]]), 1)}
    return {"since": days[0].isoformat(), "validation": check, "clusters": [{"starts": [days[x[0]].isoformat() for x in c], "mean_pct": round(m * 100, 1)} for c, m in zip(clusters, means)],
            "n_clusters": len(clusters), "n_obs": len(obs), "median_pct": round(statistics.median(means) * 100, 1),
            "up_pct": round(sum(m > 0 for m in means) / len(means) * 100), "worst_obs_pct": round(min(x[1] for x in obs) * 100, 1),
            "fee": fee, "leverage": leverage}


def flat_underlying_test(fund: dict[dt.date, float], under: dict[dt.date, float], band: float = 0.02, step: int = 5) -> dict:
    """One-year windows (a new window every 5 sessions, so windows overlap) since the fund began, where the underlying ended
    within +/-band: how often the fund ended lower, and its median change."""
    days = [d for d in sorted(fund) if d in under]
    out = []
    for i in range(0, len(days) - 252, step):
        u0, u1 = under[days[i]], under[days[i + 252]]
        if abs(u1 / u0 - 1) <= band:
            out.append(fund[days[i + 252]] / fund[days[i]] - 1)
    if not out:
        return {"windows": 0}
    return {"windows": len(out), "step_sessions": step, "overlapping": True, "band_pct": band * 100, "since": days[0].isoformat(),
            "fund_lower": sum(x < 0 for x in out), "median_pct": round(statistics.median(out) * 100, 1)}


def leveraged_adjust(row: dict, cone: list[dict], u: dict, cfg: dict, rf: float, spx_path: list[float], credit_unsettled: bool) -> None:
    """Rebuild a leveraged fund's levels from its underlying (see config/leveraged.json)."""
    L, U, lp, up_ = cfg["leverage"], cfg["underlying"], row["price"], u["price"]
    drag = (L - 1) * rf + cfg["fee_drag"]
    mp = lambda level: round(max(0.01, lev_map(lp, up_, level, L)), 2)  # noqa: E731
    # Fix 1: support zones from the underlying, shown first, with a one-session leveraged estimate.
    zones = []
    for z in u.get("bottom_zones") or []:
        lo, hi = lev_map(lp, up_, z["lo"], L), lev_map(lp, up_, z["hi"], L)
        if lo <= 0:
            continue
        zones.append({**z, "lo": round(lo, 2), "hi": round(hi, 2), "u_lo": z["lo"], "u_hi": z["hi"]})
    row["bottom_zones"] = zones
    row["zone_backtest"] = u.get("zone_backtest")
    row["macro_base_level"] = mp(u["macro_base_level"]) if u.get("macro_base_level") else None
    row["macro_stress_level"] = mp(u["macro_stress_level"]) if u.get("macro_stress_level") else None
    row["leveraged"] = {"underlying": U, "leverage": L, "index": cfg["index"], "inception": cfg["inception"], "u_close": up_, "u_name": u.get("name"),
                        "rf": round(rf, 4), "drag": round(drag, 4), "u_macro_base": u.get("macro_base_level"), "u_macro_stress": u.get("macro_stress_level"),
                        "note": "Levels are built on " + U + " and translated as price x (1 + " + str(L) + " x underlying move): a one-session estimate, recalculated daily."}
    # Fix 2: history path with daily compounding: L0 * (U_t/U0)^L * exp(-(L^2-L)/2 * var_t) * exp(-drag * t).
    ucv, ub = u["_cumvar"], u["_beta"]
    def path(step: int) -> float:
        g = max(0.01, 1 + ub * spx_path[step])
        var_t = ucv[step - 1] if step else 0.0
        return lp * g ** L * math.exp(-(L * L - L) / 2 * var_t) * math.exp(-drag * step / TRADING_YEAR)
    for c in cone:
        c["history_path"] = round(path(c["step"]), 4)
    row["history_3m"], row["history_12m"] = round(path(63), 2), round(path(HORIZON), 2)
    row["history_12m_before_financing"] = round(path(HORIZON) * math.exp(drag * HORIZON / TRADING_YEAR), 2)
    row["history_3m_before_financing"] = round(path(63) * math.exp(drag * 63 / TRADING_YEAR), 2)
    # Fix 4: trend confirmation on the underlying.
    row["promote_underlying"] = {"symbol": U, "level": u["promote_level"], "basis": u["promote_basis"]}
    row["promote_level"] = row["plan_confirm"] = mp(u["promote_level"])
    row["promote_basis"] = f"{U} above ${u['promote_level']:,.2f} ({u['promote_basis']})"
    # Fix 6: the risk warning rests on conditions that can be observed today.
    vol_ratio = row["vol_now_pct"] / row["vol_1y_pct"] if row.get("vol_1y_pct") else None
    fired = []
    if u["trend"] == "Downtrend":
        fired.append(f"{U} is in a downtrend (below its 200-day, 50-day falling)")
    if vol_ratio is not None and vol_ratio >= 1.4:
        fired.append(f"volatility is {vol_ratio:.1f}x normal ({row['vol_now_pct']}% vs {row['vol_1y_pct']}%), which speeds up daily-reset decay")
    if credit_unsettled:
        fired.append("credit is unsettled (high-yield spread at or above 3.5%, or up 0.2 points in five readings)")
    row["risk_conditions"] = fired
    if fired:
        row["action"] = "Avoid for now"
        row["action_why"] = "Leveraged fund, current conditions: " + "; ".join(fired) + "."


def write_dataset(name: str, rows: list[dict], generated_at: str, price_asof: str, analog_episodes: int, regime: dict, analyst_snapshot: str, method: str) -> None:
    payload = {
        "rows": rows,
        "meta": {
            "generated_at": generated_at,
            "price_asof": price_asof,
            "generator": "scripts/build_data.py",
            "sources": ["Yahoo Finance daily adjusted prices and volume", "FRED BAA10Y (corporate credit spread)", "config/analyst_targets.json (Nasdaq analyst consensus)"],
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


def momentum_state(r: dict) -> str:
    """Leader: up over the past year and beating the S&P this month. Holding up: up over the year, lagging this month,
    still above its 200-day. Breaking down: up over the year but lagging and below its 200-day. Turning up: down over
    the year but beating the S&P this month. Weak: down over the year and lagging."""
    m, rel = r.get("mom12_1"), r.get("rel_1m")
    if m is None or rel is None:
        return "Unknown"
    if m > 0:
        return "Leader" if rel > 0 else "Holding up" if r["vs_ma200_pct"] > 0 else "Breaking down"
    return "Turning up" if rel > 0 else "Weak"


def sectors_after_selloffs(long_history: dict, starts: list[dt.date]) -> list[dict]:
    """Each sector ETF's 3-month return minus the S&P's (SPY) after each past sell-off start, since the ETF existed."""
    def fwd(series: dict, start: dt.date, n: int = 63):
        days = sorted(series)
        i = next((k for k, d in enumerate(days) if d >= start), None)
        if i is None or i + n >= len(days) or (days[i] - start).days > 5:
            return None
        return series[days[i + n]] / series[days[i]] - 1
    spy = long_history.get("SPY", {})
    out = []
    for sym, name in SECTOR_NAMES.items():
        series = long_history.get(sym, {})
        ex = [(a - b) * 100 for a, b in ((fwd(series, s), fwd(spy, s)) for s in starts) if a is not None and b is not None]
        if len(ex) >= 5:
            out.append({"symbol": sym, "name": name, "episodes": len(ex), "median_excess_3m": round(statistics.median(ex), 1),
                        "beat_pct": round(sum(x > 0 for x in ex) / len(ex) * 100)})
    return sorted(out, key=lambda x: -x["median_excess_3m"])


def rule_test(market_prices: dict, symbols: list[str], groups: dict[str, str]) -> dict:
    """Walk-forward test of the two checks that can be replayed from prices alone: trend (price vs 200-day and the
    50-day slope) and the 'setups like this' edge (how often the stock was higher a year later after past days in the
    same state, versus its normal year, using only outcomes already known). Every 21 trading days since the history
    allows, each stock gets a score of -2..+2 and a momentum state; the outcome is its next-63-day return minus SPY's."""
    spy = market_prices["SPY"]; days = sorted(spy)
    spy_c = [spy[d] for d in days]
    by_score: dict[int, list[float]] = {}
    by_state: dict[str, list[float]] = {}
    for sym in symbols:
        if groups.get(sym) in ("Market & sector ETFs", "Leveraged ETFs") or sym not in market_prices:
            continue
        ser = market_prices[sym]
        idx = [i for i, d in enumerate(days) if d in ser]
        if len(idx) < 700:
            continue
        c = [ser[days[i]] for i in idx]; sc = [spy_c[i] for i in idx]; n = len(c)
        ma200 = moving_average(c, 200); ma50 = moving_average(c, 50)
        hi = [max(c[max(0, i - 251):i + 1]) for i in range(n)]; dd = [c[i] / hi[i] - 1 for i in range(n)]
        f12 = [c[i + 252] / c[i] - 1 if i + 252 < n else None for i in range(n)]
        for t in range(504, n - 63, 21):
            if ma200[t] is None or ma50[t - 20] is None:
                continue
            above = c[t] > ma200[t]; up = ma50[t] >= ma50[t - 20]
            trend = 1 if above and up else -1 if (not above and not up) else 0
            known = range(252, t - 252)
            base = [f12[i] for i in known if f12[i] is not None]
            match = [f12[i] for i in known if f12[i] is not None and ma200[i] is not None and abs(dd[i] - dd[t]) <= 0.05 and (c[i] > ma200[i]) == above]
            edge = 0
            if len(match) >= 40 and base:
                e = (sum(x > 0 for x in match) / len(match) - sum(x > 0 for x in base) / len(base)) * 100
                edge = 1 if e >= 10 else -1 if e <= -10 else 0
            excess = (c[t + 63] / c[t] - 1) - (sc[t + 63] / sc[t] - 1)
            by_score.setdefault(trend + edge, []).append(excess * 100)
            mom = c[t - 21] / c[t - 252] - 1
            rel = (c[t] / c[t - 21] - 1) - (sc[t] / sc[t - 21] - 1)
            state = ("Leader" if rel > 0 else "Holding up" if above else "Breaking down") if mom > 0 else ("Turning up" if rel > 0 else "Weak")
            by_state.setdefault(state, []).append(excess * 100)
    summ = lambda xs: {"n": len(xs), "median_excess_3m": round(statistics.median(xs), 2), "mean_excess_3m": round(sum(xs) / len(xs), 2),
                       "beat_spy_pct": round(sum(x > 0 for x in xs) / len(xs) * 100)} if xs else {"n": 0}
    return {"by_score": [{"score": k, **summ(v)} for k, v in sorted(by_score.items())],
            "by_state": [{"state": k, **summ(v)} for k, v in sorted(by_state.items(), key=lambda kv: kv[0])],
            "note": "Trend and setups-like-this checks only; the S&P-path and analyst checks cannot be replayed historically."}


FACTORS = [  # name, description, function(closes, i) -> value where HIGHER should be better
    ("Momentum (12-1)", "past 12-month return, skipping the last month", lambda c, i: c[i - 21] / c[i - 252] - 1),
    ("1-month reversal", "last month's losers (lower 1-month return ranks higher)", lambda c, i: -(c[i] / c[i - 21] - 1)),
    ("Low volatility", "lower 3-month daily volatility ranks higher", lambda c, i: -statistics.pstdev([math.log(c[k] / c[k - 1]) for k in range(i - 62, i + 1)])),
    ("Near 52-week high", "closer to its 52-week high ranks higher", lambda c, i: c[i] / max(c[i - 251:i + 1]) - 1),
    ("Trend (vs 200-day)", "further above its 200-day average ranks higher", lambda c, i: c[i] / (sum(c[i - 199:i + 1]) / 200) - 1),
]


def factor_monitor(market_prices: dict, symbols: list[str], groups: dict[str, str], step: int = 63) -> dict:
    """Standard factors, rebalanced every 63 trading days (non-overlapping) since the data allow: top fifth minus
    bottom fifth of the covered stocks, next-63-day return. Reports the average spread, how often it was positive and
    a t-statistic; plus today's top-fifth names per factor."""
    spy = market_prices["SPY"]; days = sorted(spy)
    stocks = [s for s in symbols if groups.get(s) not in ("Market & sector ETFs", "Leveraged ETFs") and s in market_prices]
    aligned = {s: [market_prices[s].get(d) for d in days] for s in stocks}
    out = []
    for name, desc, fn in FACTORS:
        spreads, top_ex, when = [], [], []
        for t in range(260, len(days) - step, step):
            vals = []
            for s_ in stocks:
                c = aligned[s_]
                window = c[t - 260:t + step + 1]
                if any(v is None for v in window):
                    continue
                cc = c[:t + 1]
                try:
                    vals.append((fn(cc, t), c[t + step] / c[t] - 1))
                except (ZeroDivisionError, ValueError, statistics.StatisticsError):
                    continue
            if len(vals) < 40:
                continue
            vals.sort(key=lambda x: x[0]); k = len(vals) // 5
            bottom = sum(v[1] for v in vals[:k]) / k; top = sum(v[1] for v in vals[-k:]) / k
            spreads.append((top - bottom) * 100)
            when.append(days[t + step].isoformat())
            top_ex.append((top - (spy[days[t + step]] / spy[days[t]] - 1)) * 100)
        if len(spreads) < 6:
            continue
        mean = sum(spreads) / len(spreads); sd = statistics.stdev(spreads)
        tstat = mean / (sd / math.sqrt(len(spreads))) if sd else 0.0
        t = len(days) - 1; today = []
        for s_ in stocks:
            c = aligned[s_]
            if any(v is None for v in c[t - 260:t + 1]):
                continue
            try:
                today.append((fn(c[:t + 1], t), s_))
            except (ZeroDivisionError, ValueError, statistics.StatisticsError):
                continue
        today.sort(); k = max(1, len(today) // 5)
        out.append({"factor": name, "description": desc, "periods": len(spreads), "since": days[260].year,
                    "mean_spread_3m": round(mean, 2), "positive_pct": round(sum(x > 0 for x in spreads) / len(spreads) * 100), "t_stat": round(tstat, 2),
                    "last_spread_3m": round(spreads[-1], 2), "top_vs_spy_3m": round(sum(top_ex) / len(top_ex), 2),
                    "top_now": [x[1] for x in reversed(today[-k:])][:12], "bottom_now": [x[1] for x in today[:k]][:8],
                    "series": [{"date": d, "spread": round(v, 2)} for d, v in zip(when, spreads)]})
    return {"factors": out, "step_days": step, "universe": len(stocks),
            "note": "Top fifth minus bottom fifth of the covered large US stocks, equal weight, rebalanced every 63 trading days; t above about 2 suggests the spread is unlikely to be noise."}


def breadth_history(market_prices: dict, symbols: list[str], groups: dict[str, str]) -> dict:
    """Share of stocks (not ETFs) above their 50- and 200-day averages each day, sampled weekly over three years,
    plus where today's reading ranks since the series has a full sample."""
    spy_days = sorted(market_prices["SPY"])
    stocks = [s for s in symbols if groups.get(s) not in ("Market & sector ETFs", "Leveraged ETFs") and s in market_prices]
    above200 = [0] * len(spy_days); above50 = [0] * len(spy_days); counted = [0] * len(spy_days)
    for sym in stocks:
        series = market_prices[sym]
        vals = [series.get(d) for d in spy_days]
        run, window50, window200 = [], 0.0, 0.0
        q50, q200 = [], []
        for i, v in enumerate(vals):
            if v is None:
                continue
            q50.append(v); q200.append(v); window50 += v; window200 += v
            if len(q50) > 50: window50 -= q50.pop(0)
            if len(q200) > 200: window200 -= q200.pop(0)
            if len(q200) == 200:
                counted[i] += 1
                above200[i] += v > window200 / 200
                above50[i] += v > window50 / 50
    pts = [(spy_days[i], above200[i] / counted[i] * 100, above50[i] / counted[i] * 100) for i in range(len(spy_days)) if counted[i] >= 60]
    if not pts:
        return {}
    hist200 = [p[1] for p in pts]
    series = [{"date": d.isoformat(), "above200": round(a, 1), "above50": round(b, 1)} for d, a, b in pts[-756::5]]
    if series[-1]["date"] != pts[-1][0].isoformat():
        series.append({"date": pts[-1][0].isoformat(), "above200": round(pts[-1][1], 1), "above50": round(pts[-1][2], 1)})
    return {"series": series, "now200": round(pts[-1][1]), "now50": round(pts[-1][2]),
            "rank200": round(sum(x < pts[-1][1] for x in hist200) / len(hist200) * 100), "since": pts[0][0].year, "n": max(counted)}


def vol_structure(market_prices: dict, vix3m: dict) -> dict:
    """VIX vs 3-month VIX (above 1 = near-term panic, backwardation) and VIX vs the S&P's realized 21-day volatility."""
    vix = market_prices["^VIX"]; spx = market_prices["^GSPC"]
    vd = sorted(vix); sd_ = sorted(spx)
    rets = [math.log(spx[sd_[i]] / spx[sd_[i - 1]]) for i in range(len(sd_) - 21, len(sd_))]
    realized = math.sqrt(sum(r * r for r in rets) / len(rets)) * math.sqrt(252) * 100
    out = {"vix": round(vix[vd[-1]], 2), "realized_21d": round(realized, 1), "vix_minus_realized": round(vix[vd[-1]] - realized, 1)}
    if vix3m:
        common = sorted(set(vix) & set(vix3m))
        ratios = [vix[d] / vix3m[d] for d in common]
        if ratios:
            out.update({"vix3m": round(vix3m[common[-1]], 2), "term_ratio": round(ratios[-1], 3),
                        "term_ratio_rank": round(sum(x < ratios[-1] for x in ratios) / len(ratios) * 100),
                        "backwardation_days_pct": round(sum(x > 1 for x in ratios) / len(ratios) * 100, 1),
                        "term_series": [{"date": d.isoformat(), "ratio": round(vix[d] / vix3m[d], 3)} for d in common[-252::2]]})
    return out


def build_market(rows: list[dict], market_prices: dict, macro: dict, headlines: list[dict], long_history: dict | None = None, starts: list | None = None) -> dict:
    by = {r["symbol"]: r for r in rows}
    spy_1m = by.get("SPY", {}).get("ret_1m") or 0
    for r in rows:
        r["rel_1m"] = round(r["ret_1m"] - spy_1m, 1) if r.get("ret_1m") is not None else None
        r["mom_state"] = momentum_state(r)
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
        main = next((z for z in r.get("bottom_zones") or [] if z["role"] == "main"), None)
        setups.append({"symbol": r["symbol"], "group": r["group"], "action": r["action"], "price": r["price"], "trend": r["trend"],
                       "evidence_score": r["evidence_score"], "own_edge_pts": r.get("own_edge_pts"), "own_f12_up_pct": r.get("own_f12_up_pct"),
                       "base_f12_up_pct": r.get("base_f12_up_pct"), "mom12_1": r.get("mom12_1"), "rel_1m": r.get("rel_1m"), "mom_state": r.get("mom_state"),
                       "zone_gap_pct": round((main["hi"] / r["price"] - 1) * 100, 1) if main else None, "zone_score": main["score"] if main else 0,
                       "m3_lo": r["m3_lo"], "m3_hi": r["m3_hi"]})
    setups.sort(key=lambda x: (-x["evidence_score"], x["action"] != "Probe", -(x["own_edge_pts"] or -99), -(x["rel_1m"] or -99)))
    momentum = sorted(({"symbol": r["symbol"], "group": r["group"], "mom12_1": r["mom12_1"], "rel_1m": r["rel_1m"], "ret_1m": r["ret_1m"],
                        "ret_3m": r["ret_3m"], "vs_ma200_pct": r["vs_ma200_pct"], "dd_52w_pct": r["dd_52w_pct"], "state": r["mom_state"]}
                       for r in rows if r.get("mom12_1") is not None and r.get("group") != "Leveraged ETFs"), key=lambda x: -(x["mom12_1"] or -999))
    return {"asof": spx_days[-1].isoformat(), "spx": round(spx[spx_days[-1]], 2), "spx_ret_1d": chg(spx, spx_days, 1), "spx_ret_1w": chg(spx, spx_days, 5),
            "spx_ret_1m": chg(spx, spx_days, 21), "spx_off_high_pct": round((spx[spx_days[-1]] / spx_peak - 1) * 100, 1),
            "vix": round(vix[vix_days[-1]], 2), "vix_chg_1w": round(vix[vix_days[-1]] - vix[vix_days[-6]], 2) if len(vix_days) > 5 else None,
            "indices": indices, "spx_zones": spx_zones, "macro": macro, "sectors": sectors, "breadth": breadth,
            "movers_up": [pick(r) for r in movers_up], "movers_down": [pick(r) for r in movers_dn], "setups": setups[:15], "momentum": momentum,
            "momentum_counts": {k: sum(m["state"] == k for m in momentum) for k in ("Leader", "Holding up", "Turning up", "Breaking down", "Weak")},
            "sectors_after": sectors_after_selloffs(long_history or {}, starts or []),
            "action_counts": {a: sum(r["action"] == a for r in rows) for a in ("Probe", "Watch: odds good, trend not yet", "Watch", "Avoid for now")},
            "headlines": headlines}


def main() -> None:
    universe, prior_by_symbol = load_universe()
    analyst_payload = json.loads(ANALYST_TARGETS.read_text(encoding="utf-8"))
    analyst_snapshot = dt.date.fromisoformat(analyst_payload["synced_at"])
    analyst_targets = analyst_payload["targets"]
    symbols = [row["symbol"] for row in universe]
    lev_cfg = json.loads(LEVERAGED.read_text(encoding="utf-8"))["funds"] if LEVERAGED.exists() else {}
    lev_cfg = {k: v for k, v in lev_cfg.items() if k in symbols}
    hidden = sorted({v["underlying"] for v in lev_cfg.values()} - set(symbols))
    starts = {symbol: dt.date(2017, 1, 1) for symbol in symbols + ["SPY"] + hidden}
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
        futures = {pool.submit(fetch_symbol, symbol, dt.date(1985, 1, 1), "_long"): symbol for symbol in symbols + ["SPY"] + hidden}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                long_history[symbol] = future.result()
            except RuntimeError as exc:
                print(f"long history unavailable for {symbol}: {exc}")
    crash_by_symbol = {symbol: crash_replay(long_history.get(symbol, {}), starts_) for symbol in symbols + ["SPY"]}
    macro = macro_bottom_stats(market["^GSPC"], market["^VIX"], fetch_fred("BAA10Y"), fetch_fred("BAMLH0A0HYM2"))
    print("macro bottom stats:", json.dumps(macro))
    MACRO_META.update(macro)
    summary_rows, cone_rows, history_rows = [], [], []
    built: dict[str, tuple[dict, list, list]] = {}
    order = [s for s in symbols if s not in lev_cfg] + hidden + [s for s in symbols if s in lev_cfg]
    try:
        irx = fetch_symbol("^IRX", dt.date(1999, 1, 1), "_hist")
    except RuntimeError as exc:
        print(f"^IRX unavailable ({exc}); financing drag uses 4%"); irx = {}
    rf = irx[max(irx)] / 100 if irx else 0.04
    credit_unsettled = macro.get("credit_state") == "unsettled"
    for symbol in order:
        prior = prior_by_symbol.get(symbol) or {"symbol": symbol, "name": symbol, "group": "Underlying (not listed)"}
        row, cone, history = build_symbol(symbol, prior, analyst_targets.get(symbol), analyst_snapshot, market[symbol], market["SPY"], spx_path, regime, crash_by_symbol.get(symbol), crash_by_symbol.get("SPY"), macro, load_ohlc(symbol), long_history.get(symbol))
        if symbol in lev_cfg:
            cfg = lev_cfg[symbol]
            u = built[cfg["underlying"]][0]
            leveraged_adjust(row, cone, u, cfg, rf, spx_path, credit_unsettled)
            row["leveraged"]["stress_sim"] = stress_sim(long_history.get(cfg["underlying"], {}), irx, starts_, cfg["leverage"], cfg["fee_drag"], long_history.get(symbol))
            row["leveraged"]["flat_test"] = flat_underlying_test(long_history.get(symbol, {}), long_history.get(cfg["underlying"], {}))
            row["crash_label"] = f"real {symbol} since {cfg['inception'][:4]} only"
        built[symbol] = (row, cone, history)
        print(f"built {symbol}: {row['action']} ({row['trend']}, beta {row['beta']})")
    for symbol in symbols:
        row, cone, history = built[symbol]
        summary_rows.append(row); cone_rows.extend(cone); history_rows.extend(history)
    for row, _, _ in built.values():
        row.pop("_cumvar", None); row.pop("_beta", None)
    if len(summary_rows) != len(symbols):
        raise RuntimeError(f"Built {len(summary_rows)} of {len(symbols)} symbols")
    generated_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    price_asof = max(row["asof"] for row in summary_rows)
    write_dataset("outlook_summary", summary_rows, generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(), "Volatility ranges, five-year beta-scaled S&P analog path (leveraged funds: compounded on the underlying), trend and evidence action rules.")
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
    market_row = build_market(summary_rows, market, macro, market_news[:12], long_history, starts_); market_row["zone_track"] = track
    fagg = {k: sum((r.get("fib_backtest") or {}).get(k, 0) for r in summary_rows if r.get("group") != "Leveraged ETFs") for k in ("samples", "reached", "held", "base_reached", "base_held")}
    market_row["fib_track"] = {**fagg, "held_pct": pct(fagg["held"], fagg["reached"]), "base_held_pct": pct(fagg["base_held"], fagg["base_reached"]), "reached_pct": pct(fagg["reached"], fagg["samples"])}
    print("fibonacci track record:", json.dumps(market_row["fib_track"]))
    for r in summary_rows:
        r.pop("fib_backtest", None)
    market_row["breadth_history"] = breadth_history(market, symbols, {r["symbol"]: r["group"] for r in summary_rows})
    market_row["rule_test"] = rule_test(market, symbols, {r["symbol"]: r["group"] for r in summary_rows})
    market_row["factor_monitor"] = factor_monitor(market, symbols, {r["symbol"]: r["group"] for r in summary_rows})
    print("factors:", json.dumps([{k: f[k] for k in ("factor", "periods", "mean_spread_3m", "positive_pct", "t_stat", "last_spread_3m")} for f in market_row["factor_monitor"]["factors"]]))
    print("rule test:", json.dumps(market_row["rule_test"]))
    try:
        vix3m = fetch_symbol("^VIX3M", dt.date(2010, 1, 1))
    except RuntimeError as exc:
        print(f"VIX3M unavailable: {exc}"); vix3m = {}
    market_row["vol"] = vol_structure(market, vix3m)
    today_ = dt.datetime.now(dt.timezone.utc).date()
    market_row["earnings_ahead"] = sorted(({"symbol": sym, "date": e["date"], "timing": e.get("timing"), "eps_forecast": e.get("eps_forecast"),
                                            "group": next((r["group"] for r in summary_rows if r["symbol"] == sym), None)}
                                           for sym, e in earnings.items() if e.get("date") and not e.get("estimated")
                                           and 0 <= (dt.date.fromisoformat(e["date"]) - today_).days <= 28), key=lambda x: (x["date"], x["symbol"]))
    print("breadth now", market_row["breadth_history"].get("now200"), "rank", market_row["breadth_history"].get("rank200"), "| vol", {k: v for k, v in market_row["vol"].items() if k != "term_series"}, "| earnings ahead", len(market_row["earnings_ahead"]))
    write_dataset("market", [market_row], generated_at, price_asof, episodes, regime, analyst_snapshot.isoformat(),
                  "Market overview built from the same rows: indices, S&P bottom zones in index points, sectors, breadth, movers, ranked setups and headlines.")
    print(f"news for {sum(bool(n) for n in news.values())} symbols; earnings dates for {len(earnings)}; {len(market_news)} market headlines")
    print(f"built {len(summary_rows)} symbols through {price_asof}; {episodes} S&P analog episodes")


if __name__ == "__main__":
    main()
