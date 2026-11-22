#!/usr/bin/env python3
"""Build data/drivers.json: the day's important news, market drivers, macro, and what moved each stock.

Sources (no keys): Yahoo Finance prices, FRED macro series, and public RSS feeds.
Attribution is a regression on the last two years of daily returns. It shows what a stock's move
lined up with; it does not prove why the stock moved.
"""

from __future__ import annotations

import concurrent.futures
import datetime as dt
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_data as B  # noqa: E402  (shared price and FRED fetchers with caching)

OUT = ROOT / "data" / "drivers.json"
START = dt.date(2017, 1, 1)
NEWS_HOURS = 48
REG_DAYS = 504
UA = {"User-Agent": "market-analysis/1.0"}

# id, name, group, Yahoo symbol, unit (pct, bp, pts), whether a rise is good for stocks (None = depends)
DRIVERS = [
    ("spx", "S&P 500", "Stocks", "^GSPC", "pct", True), ("ndx", "Nasdaq 100", "Stocks", "QQQ", "pct", True),
    ("small", "Small caps", "Stocks", "IWM", "pct", True), ("vix", "VIX fear gauge", "Stocks", "^VIX", "pts", False),
    ("y10", "10-year yield", "Rates & dollar", "^TNX", "bp", None), ("y3m", "3-month yield", "Rates & dollar", "^IRX", "bp", None),
    ("dxy", "US dollar", "Rates & dollar", "DX-Y.NYB", "pct", None), ("usdjpy", "Dollar vs yen", "Rates & dollar", "USDJPY=X", "pct", None),
    ("usdcny", "Dollar vs yuan", "Rates & dollar", "USDCNY=X", "pct", None),
    ("wti", "WTI oil", "Commodities", "CL=F", "pct", None), ("brent", "Brent oil", "Commodities", "BZ=F", "pct", None),
    ("natgas", "Natural gas", "Commodities", "NG=F", "pct", None), ("gold", "Gold", "Commodities", "GC=F", "pct", None),
    ("copper", "Copper", "Commodities", "HG=F", "pct", True), ("btc", "Bitcoin", "Commodities", "BTC-USD", "pct", True),
    ("defense", "US defense stocks", "Geopolitics & world", "ITA", "pct", None), ("china", "China stocks", "Geopolitics & world", "FXI", "pct", True),
    ("taiwan", "Taiwan stocks", "Geopolitics & world", "EWT", "pct", True), ("japan", "Japan stocks", "Geopolitics & world", "EWJ", "pct", True),
    ("europe", "Europe stocks", "Geopolitics & world", "VGK", "pct", True), ("em", "Emerging markets", "Geopolitics & world", "EEM", "pct", True),
    ("semis", "Chip stocks", "Risk appetite", "SMH", "pct", True), ("banks", "Regional banks", "Risk appetite", "KRE", "pct", True),
    ("junk", "High-yield bonds", "Risk appetite", "HYG", "pct", True),
]

# id, name, group, FRED series, transform, unit, whether a rise is good for stocks (None = depends)
MACRO = [
    ("dff", "Fed funds rate", "Fed & rates", "DFF", "level", "%", None), ("dgs2", "2-year Treasury", "Fed & rates", "DGS2", "level", "%", None),
    ("dgs10", "10-year Treasury", "Fed & rates", "DGS10", "level", "%", None), ("curve", "10y minus 2y curve", "Fed & rates", "T10Y2Y", "level", " pts", None),
    ("real10", "10-year real yield", "Fed & rates", "DFII10", "level", "%", False),
    ("cpi", "CPI inflation, yearly", "Inflation", "CPIAUCSL", "yoy", "%", False), ("corecpi", "Core CPI, yearly", "Inflation", "CPILFESL", "yoy", "%", False),
    ("corepce", "Core PCE, the Fed's gauge", "Inflation", "PCEPILFE", "yoy", "%", False), ("be10", "10-year inflation expectations", "Inflation", "T10YIE", "level", "%", False),
    ("unrate", "Unemployment rate", "Jobs", "UNRATE", "level", "%", False), ("payrolls", "Payrolls added, monthly", "Jobs", "PAYEMS", "diff", "k", True),
    ("claims", "Weekly jobless claims", "Jobs", "ICSA", "thousands", "k", False),
    ("gdp", "Real GDP growth", "Growth", "A191RL1Q225SBEA", "level", "%", True), ("indpro", "Industrial output, yearly", "Growth", "INDPRO", "yoy", "%", True),
    ("retail", "Retail sales, yearly", "Growth", "RSAFS", "yoy", "%", True), ("sentiment", "Consumer sentiment", "Growth", "UMCSENT", "level", "", True),
    ("hyoas", "High-yield credit spread", "Credit & liquidity", "BAMLH0A0HYM2", "level", "%", False), ("baa", "Corporate credit spread", "Credit & liquidity", "BAA10Y", "level", "%", False),
    ("nfci", "Financial conditions", "Credit & liquidity", "NFCI", "level", "", False), ("fedbs", "Fed balance sheet", "Credit & liquidity", "WALCL", "trillions", "T", True),
    ("epu", "Policy uncertainty, 7-day", "Credit & liquidity", "USEPUINDXD", "ma7", "", False),
]

FACTORS = [("market", "S&P 500", "^GSPC", "pct", "+1%"), ("chips", "AI & chips vs market", "SMH", "neutral", "+1%"), ("rates", "10-year yield", "^TNX", "bp10", "+10bp"), ("oil", "Oil", "CL=F", "pct", "+1%"),
           ("dollar", "Dollar", "DX-Y.NYB", "pct", "+1%"), ("gold", "Gold", "GC=F", "pct", "+1%"), ("bitcoin", "Bitcoin", "BTC-USD", "pct", "+1%")]
SPY_FACTORS = [("broad", "Broad market (equal-weight)", "RSP", "pct", "+1%")] + [f for f in FACTORS if f[0] in ("chips", "rates", "oil", "dollar", "gold")]
FACTOR_TEXT = {"market": ("the S&P 500", "the S&P 500 rises 1%"), "broad": ("the broad market", "the equal-weight S&P rises 1%"), "chips": ("chip stocks moving beyond the market", "chip stocks beat the market by 1%"),
               "rates": ("the 10-year yield", "the 10-year yield rises 10 bp"), "oil": ("oil", "oil rises 1%"), "dollar": ("the dollar", "the dollar rises 1%"), "gold": ("gold", "gold rises 1%"), "bitcoin": ("Bitcoin", "Bitcoin rises 1%")}
FACTOR_THEME = {"chips": "ai", "broad": "markets", "rates": "fed", "oil": "energy", "dollar": "trade", "gold": "geopolitics", "bitcoin": "markets", "market": "markets"}

THEMES = {
    "geopolitics": ("Geopolitics & war", r"\b(wars?|warfare|missiles?|airstrikes?|air strikes?|drone strikes?|invasion|invades?|ceasefire|truce|troops|military|sanctions?|nato|ukrain\w*|russia\w*|crimea\w*|kremlin|zelensk\w*|putin|iran\w*|israel\w*|gaza|hezbollah|houthis?|red sea|hormuz|taiwan strait|north korea\w*|south china sea|venezuela\w*|nuclear talks|geopolitic\w*|coup)\b", ["wti", "gold", "defense", "vix", "taiwan"]),
    "fed": ("Fed & interest rates", r"(\b(?-i:Fed)\b|\b(federal reserve|fomc|powell|rate (?:cut|hike)s?|interest rates?|treasury yields?|bond yields?|10-year|bond market|central bank|ecb|bank of japan|boj)\b)", ["y10", "y3m", "dxy"]),
    "inflation": ("Inflation, jobs & economy", r"\b(inflation|cpi|pce|consumer prices?|jobs report|payrolls?|unemployment|jobless|layoffs?|recession|gdp|consumer (?:sentiment|confidence|spending)|retail sales)\b", ["y10", "copper", "small"]),
    "trade": ("Trade, tariffs & China", r"\b(tariffs?|trade (?:war|deal|talks|deficit)|export controls?|chips? ban|beijing|xi jinping|china|chinese|yuan|supreme court.*tariff)\b", ["china", "usdcny", "copper"]),
    "energy": ("Oil & energy", r"\b(oil|crude|opec\+?|brent|natural gas|lng|gasoline|energy prices?|refiner\w*|pipeline)\b", ["wti", "brent", "natgas"]),
    "ai": ("AI & chips", r"\b(ai|a\.i\.|artificial intelligence|semiconductors?|chips?|chipmakers?|nvidia|tsmc|anthropic|openai|data centers?|hyperscalers?|broadcom|micron|amd)\b", ["semis", "ndx", "taiwan"]),
    "credit": ("Banks & credit", r"(?<!West )\b(banks?|banking|credit|private credit|defaults?|bankruptc\w*|junk bonds?|high-yield|liquidity|margin calls?)\b", ["banks", "junk", "vix"]),
    "markets": ("Market moves", r"\b(stocks?|wall street|s&p|s&amp;p|nasdaq|dow|sell-?off|correction|rally|bear market|futures|vix|volatility|markets?)\b", ["spx", "vix", "small"]),
}
THEME_RE = {key: re.compile(value[1], re.I) for key, value in THEMES.items()}
MARKET_RE = re.compile(r"\b(stocks?|shares|markets?|wall street|s&p|nasdaq|dow|oil|yields?|investors?|traders?|futures|prices?|economy|selloff|sell-off)\b", re.I)
NOISE_RE = re.compile(r"(\b\d+\s+(?:\w+\s+)?(?:stocks?|etfs?|reasons?|things|ways|picks)\b|better buy|should you|millionaire|retire|my (?:husband|wife|son|daughter|mom|dad|sister|brother)|\bI(?:'m| am| have| want| think)\b|could make you|best stocks?|stocks? to buy|buy and hold|too late to buy|buy the dip\?|watchlist|horoscope|quiz|crossword|podcast|price target|upgrades?\b|downgrades?\b|shares (?:sold|bought|acquired) by|college fed|enforcement action|\$[\d,]+ invested|annuity|\bpenny stock)", re.I)
FED_KEEP_RE = re.compile(r"FOMC|federal funds|discount rate|monetary policy|minutes|beige book|statement|chair", re.I)
SOURCE_WEIGHT = [("federal reserve bank", 3), ("federal reserve", 6), ("reuters", 5), ("associated press", 5), ("ap news", 5), ("bloomberg", 5), ("financial times", 5), ("wall street journal", 5), ("wsj", 5),
                 ("cnbc", 4), ("bbc", 4), ("new york times", 4), ("barron", 4), ("economist", 4), ("guardian", 3), ("al jazeera", 3), ("marketwatch", 3), ("axios", 3), ("politico", 3),
                 ("washington post", 3), ("nikkei", 4), ("yahoo", 2)]
STOP = set("a an and are as at be but by for from has have in into is it its of on or over says say said that the their this to up was were will with after amid new how why what".split())

ALIASES = {"NVDA": "nvidia", "AAPL": "apple", "MSFT": "microsoft", "AMZN": "amazon", "GOOGL": "google|alphabet", "META": "(?-i:Meta)|meta platforms|facebook", "TSLA": "tesla|elon musk",
           "AVGO": "broadcom", "AMD": "amd|advanced micro", "INTC": "intel", "TSM": "tsmc|taiwan semiconductor", "MU": "micron", "ORCL": "oracle", "PLTR": "palantir", "NFLX": "netflix",
           "CRM": "salesforce", "ADBE": "adobe", "ARM": "arm holdings", "SMCI": "super micro|supermicro", "DELL": "dell", "IBM": "ibm", "QCOM": "qualcomm", "TXN": "texas instruments",
           "AMAT": "applied materials", "LRCX": "lam research", "KLAC": "kla", "COIN": "coinbase", "MSTR": "microstrategy", "UBER": "uber", "SHOP": "shopify", "CRWD": "crowdstrike",
           "PANW": "palo alto networks", "WMT": "walmart", "TGT": "target corp", "COST": "costco", "HD": "home depot", "LOW": "lowe's", "NKE": "nike", "SBUX": "starbucks", "MCD": "mcdonald's",
           "DIS": "disney", "JPM": "jpmorgan|jamie dimon", "BAC": "bank of america", "GS": "goldman", "MS": "morgan stanley", "WFC": "wells fargo", "BLK": "blackrock", "SCHW": "schwab",
           "BRK-B": "berkshire|warren buffett", "V": "visa inc", "MA": "mastercard", "XOM": "exxon", "CVX": "chevron", "BA": "boeing", "LMT": "lockheed", "RTX": "raytheon|rtx", "GE": "ge aerospace",
           "CAT": "caterpillar", "DE": "john deere", "UNH": "unitedhealth", "LLY": "eli lilly", "JNJ": "johnson & johnson", "PFE": "pfizer", "MRK": "merck", "ABBV": "abbvie", "VST": "vistra",
           "CEG": "constellation energy", "VRT": "vertiv", "NOW": "servicenow", "SNOW": "snowflake", "ANET": "arista", "CSCO": "cisco", "T": "at&t", "VZ": "verizon", "TMUS": "t-mobile"}
ALIAS_RE = {sym: re.compile(r"\b(?:" + names + r")\b", re.I) for sym, names in ALIASES.items()}


def mentions(symbol: str, title: str) -> bool:
    alias = ALIAS_RE.get(symbol)
    if alias and alias.search(title):
        return True
    code = re.escape(symbol)
    return bool(re.search(r"\((?:NYSE|NASDAQ|Nasdaq)?:?\s*" + code + r"\)", title)) or (len(symbol) >= 3 and bool(re.search(r"\b" + code + r"\b", title)))


def google_feed(query: str) -> str:
    return "https://news.google.com/rss/search?" + urllib.parse.urlencode({"q": query + " when:2d", "hl": "en-US", "gl": "US", "ceid": "US:en"})


FEEDS = {
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml", "BBC Business": "https://feeds.bbci.co.uk/news/business/rss.xml",
    "CNBC": "https://www.cnbc.com/id/100003114/device/rss/rss.html", "CNBC Economy": "https://www.cnbc.com/id/20910258/device/rss/rss.html", "CNBC World": "https://www.cnbc.com/id/100727362/device/rss/rss.html",
    "Al Jazeera": "https://www.aljazeera.com/xml/rss/all.xml", "Federal Reserve": "https://www.federalreserve.gov/feeds/press_all.xml",
    "New York Times Business": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "New York Times World": "https://rss.nytimes.com/services/xml/rss/nyt/World.xml", "Guardian World": "https://www.theguardian.com/world/rss",
    "Google News: geopolitics": google_feed("(war OR sanctions OR missile OR Taiwan OR Iran OR Russia OR Ukraine OR Israel) (markets OR oil OR stocks)"),
    "Google News: trade": google_feed("(tariffs OR \"trade war\" OR \"export controls\" OR China) (markets OR economy OR stocks)"),
    "Google News: Fed": google_feed("\"Federal Reserve\" OR \"Treasury yields\" OR inflation OR \"jobs report\""),
    "Google News: oil": google_feed("oil prices OPEC crude"), "Google News: markets": google_feed("Wall Street stocks S&P 500 close"), "Google News: AI": google_feed("AI chips Nvidia selloff stocks"),
}


GENERAL_FEEDS = {"BBC World", "Al Jazeera", "New York Times World", "Guardian World"}


def settled_day() -> dt.date:
    now = dt.datetime.now(ZoneInfo("America/New_York"))
    day = now.date() if now.time() >= dt.time(16, 15) else now.date() - dt.timedelta(days=1)
    while day.weekday() >= 5:
        day -= dt.timedelta(days=1)
    return day


def prices(symbol: str) -> dict[dt.date, float]:
    try:
        return B.fetch_symbol(symbol, START)
    except RuntimeError as exc:
        print(f"price {symbol} unavailable: {exc}")
        return {}


def round_or_none(value: float | None, digits: int = 2) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, digits)


def driver_row(spec: tuple, series: dict[dt.date, float], cut: dt.date) -> dict | None:
    key, name, group, symbol, unit, up_good = spec
    crypto = key == "btc"
    days = sorted(d for d in series if crypto or d <= cut)
    if len(days) < 40:
        return None
    last = series[days[-1]]
    steps = (1, 7, 30) if crypto else (1, 5, 21)
    def change(n: int) -> float:
        before = series[days[-1 - n]]
        return (last - before) * 100 if unit == "bp" else last - before if unit == "pts" else (last / before - 1) * 100
    window = [series[d] for d in days[-(365 if crypto else 252):]]
    return {"id": key, "name": name, "group": group, "symbol": symbol, "unit": unit, "up_good": up_good, "last": round(last, 3), "date": days[-1].isoformat(),
            "chg_1d": round(change(steps[0]), 2), "chg_1w": round(change(steps[1]), 2), "chg_1m": round(change(steps[2]), 2),
            "pct_1y": round(100 * sum(v <= last for v in window) / len(window))}


def macro_points(series: dict[dt.date, float], transform: str) -> list[tuple[dt.date, float]]:
    days = sorted(series)
    if transform == "yoy":
        out = []
        for d in days:
            try:
                base = series.get(d.replace(year=d.year - 1))
            except ValueError:
                base = None
            if base:
                out.append((d, (series[d] / base - 1) * 100))
        return out
    if transform == "diff":
        return [(b, series[b] - series[a]) for a, b in zip(days, days[1:]) if (b - a).days <= 35]
    if transform == "thousands":
        return [(d, series[d] / 1000) for d in days]
    if transform == "trillions":
        return [(d, series[d] / 1_000_000) for d in days]
    if transform == "ma7":
        return [(days[i], sum(series[x] for x in days[i - 6:i + 1]) / 7) for i in range(6, len(days))]
    return [(d, series[d]) for d in days]


def macro_row(spec: tuple, series: dict[dt.date, float], prior: dict) -> dict | None:
    key, name, group, code, transform, unit, up_good = spec
    points = macro_points(series, transform) if series else []
    if len(points) < 3:
        return {**prior[key], "stale": True} if key in prior else None
    daily = (points[-1][0] - points[-2][0]).days <= 4
    back = 5 if daily and len(points) > 6 else 1
    day, value = points[-1]
    then_day, then = points[-1 - back]
    since = day - dt.timedelta(days=365 * 5)
    window = [v for d, v in points if d >= since]
    return {"id": key, "name": name, "group": group, "series": code, "unit": unit, "up_good": up_good, "value": round(value, 2), "date": day.isoformat(),
            "prior": round(then, 2), "prior_date": then_day.isoformat(), "change": round(value - then, 2), "pct_5y": round(100 * sum(v <= value for v in window) / len(window)), "stale": False}


def read_feed(item: tuple[str, str]) -> tuple[str, list[dict] | None]:
    name, url = item
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=15) as response:
            root = ET.fromstring(response.read())
    except (OSError, ET.ParseError) as exc:
        print(f"feed {name} unavailable: {exc}")
        return name, None
    out = []
    for node in root.iter("item"):
        title = re.sub(r"\s+", " ", node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        tag = node.find("source")
        source = tag.text.strip() if tag is not None and tag.text else re.sub(r"\s(World|Business|Economy)$", "", name)
        if name.startswith("Google News") and title.endswith(" - " + source):
            title = title[: -(len(source) + 3)]
        try:
            when = parsedate_to_datetime(node.findtext("pubDate") or "").astimezone(dt.timezone.utc)
        except (TypeError, ValueError):
            continue
        if title and link.startswith("https://"):
            out.append({"title": title[:200], "link": link, "source": source[:40], "when": when, "feed": name})
    return name, out


def source_weight(source: str) -> int:
    low = source.lower()
    return next((w for key, w in SOURCE_WEIGHT if key in low), 1)


def tokens(title: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9&']+", title.lower()) if w not in STOP and len(w) > 1}


def build_news(prior: dict) -> tuple[list[dict], list[str], list[str], bool]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(read_feed, FEEDS.items()))
    ok = [n for n, items in results if items is not None]
    failed = [n for n, items in results if items is None]
    if len(ok) < 4:
        return prior.get("news", []), ok, failed, True
    now = dt.datetime.now(dt.timezone.utc)
    pool_items = []
    for _, items in results:
        for x in items or []:
            age = (now - x["when"]).total_seconds() / 3600
            if age > NEWS_HOURS or age < -1 or NOISE_RE.search(x["title"]):
                continue
            if x["feed"] == "Federal Reserve" and not FED_KEEP_RE.search(x["title"]):
                continue
            # General world-news feeds carry many stories with no market link; keep them only when the headline
            # itself names a market, price or investor reaction.
            if x["feed"] in GENERAL_FEEDS and not MARKET_RE.search(x["title"]):
                continue
            themes = [k for k, rx in THEME_RE.items() if rx.search(x["title"])]
            if themes:
                pool_items.append({**x, "age": age, "themes": themes, "tok": tokens(x["title"]), "weight": source_weight(x["source"])})
    pool_items.sort(key=lambda x: (-x["weight"], x["age"]))
    clusters: list[dict] = []
    for x in pool_items:
        for c in clusters:
            common = len(x["tok"] & c["tok"])
            if common >= 3 and common / max(1, len(x["tok"] | c["tok"])) >= 0.42:
                c["sources"].add(x["source"])
                c["themes"] |= set(x["themes"])
                c["newest"] = min(c["newest"], x["age"])
                break
        else:
            clusters.append({**x, "sources": {x["source"]}, "themes": set(x["themes"]), "newest": x["age"]})
    out = []
    for c in clusters:
        specific = [t for t in THEMES if t in c["themes"] and t != "markets"]
        recency = 3 if c["newest"] <= 6 else 2 if c["newest"] <= 12 else 1 if c["newest"] <= 24 else 0
        score = c["weight"] + min(8, 2 * (len(c["sources"]) - 1)) + recency + (2 if MARKET_RE.search(c["title"]) else 0) + (2 if specific else 0)
        if c["weight"] < 2:
            continue
        out.append({"title": c["title"], "source": c["source"], "link": c["link"], "published": c["when"].replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "themes": (specific or ["markets"])[:3], "score": score, "sources": len(c["sources"]),
                    "tickers": [s for s, rx in ALIAS_RE.items() if rx.search(c["title"])][:5]})
    out.sort(key=lambda x: x["published"], reverse=True)
    out.sort(key=lambda x: -x["score"])
    return out[:80], ok, failed, False


def invert(matrix: list[list[float]]) -> list[list[float]]:
    n = len(matrix)
    a = [row[:] + [float(i == j) for j in range(n)] for i, row in enumerate(matrix)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(a[r][col]))
        if abs(a[pivot][col]) < 1e-12:
            raise ZeroDivisionError("singular")
        a[col], a[pivot] = a[pivot], a[col]
        p = a[col][col]
        a[col] = [v / p for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                f = a[r][col]
                a[r] = [x - f * y for x, y in zip(a[r], a[col])]
    return [row[n:] for row in a]


def ols(y: list[float], x: list[list[float]]) -> tuple[list[float], list[float], float]:
    rows = [[1.0] + r for r in x]
    k, n = len(rows[0]), len(rows)
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * v for r, v in zip(rows, y)) for i in range(k)]
    inv = invert(xtx)
    beta = [sum(inv[i][j] * xty[j] for j in range(k)) for i in range(k)]
    fit = [sum(b * v for b, v in zip(beta, r)) for r in rows]
    sse = sum((v - f) ** 2 for v, f in zip(y, fit))
    mean_y = sum(y) / n
    sst = sum((v - mean_y) ** 2 for v in y) or 1.0
    s2 = sse / (n - k)
    return beta[1:], [math.sqrt(max(s2 * inv[i][i], 1e-18)) for i in range(1, k)], 1 - sse / sst


def factor_step(factors: dict, spec: tuple, a: dt.date, b: dt.date, base: str, chip_beta: dict) -> float:
    if spec[3] == "neutral":
        chips, broad = factors["chips"], factors[base]
        return (chips[b] / chips[a] - 1) * 100 - chip_beta[base] * (broad[b] / broad[a] - 1) * 100
    series = factors[spec[0]]
    return (series[b] - series[a]) * 10 if spec[3] == "bp10" else (series[b] / series[a] - 1) * 100


def chip_betas(factors: dict, common: set, cut: dt.date) -> dict:
    """How much chip stocks move with each market measure, so the chip factor excludes the market's own move."""
    days = sorted(d for d in common if d <= cut)[-(REG_DAYS + 1):]
    pairs = list(zip(days, days[1:]))
    chips = [(factors["chips"][b] / factors["chips"][a] - 1) * 100 for a, b in pairs]
    return {base: ols(chips, [[(factors[base][b] / factors[base][a] - 1) * 100] for a, b in pairs])[0][0] for base in ("market", "broad")}


def attribute(symbol: str, series: dict, factors: dict, common: set, cut: dt.date, chip_beta: dict) -> dict | None:
    days = sorted(d for d in series if d in common and d <= cut)[-(REG_DAYS + 1):]
    if len(days) < 130:
        return None
    use = SPY_FACTORS if symbol == "SPY" else FACTORS
    base = "broad" if symbol == "SPY" else "market"
    pairs = list(zip(days, days[1:]))
    y = [(series[b] / series[a] - 1) * 100 for a, b in pairs]
    x = [[factor_step(factors, f, a, b, base, chip_beta) for f in use] for a, b in pairs]
    try:
        beta, se, r2 = ols(y, x)
    except ZeroDivisionError:
        return None
    def window(n: int) -> dict:
        total = (series[days[-1]] / series[days[-1 - n]] - 1) * 100
        parts = {f[0]: b * sum(r[i] for r in x[-n:]) for i, (f, b) in enumerate(zip(use, beta))}
        return {"total": round(total, 2), "parts": {k: round(v, 2) for k, v in parts.items()}, "other": round(total - sum(parts.values()), 2)}
    exposures = [{"id": f[0], "beta": round(b, 3), "t": round(b / s, 1)} for f, b, s in zip(use, beta, se)]
    return {"n": len(y), "r2": round(r2, 2), "exposures": exposures, "day": window(1), "week": window(5)}


def regime(drivers: dict) -> dict:
    spx, y10, oil, gold, dollar = (drivers[k]["chg_1w"] for k in ("spx", "y10", "wti", "gold", "dxy"))
    if spx <= -1 and y10 <= -5:
        label, detail = "Risk-off", "Stocks are falling while Treasury yields fall, so money is moving into bonds."
    elif spx <= -1 and y10 >= 5:
        label, detail = "Rates-led selloff", "Stocks are falling while yields rise, so higher borrowing costs are pressuring valuations."
    elif spx <= -1:
        label, detail = "Selloff", "Stocks are falling without a clear move in yields."
    elif spx >= 1 and y10 >= 5:
        label, detail = "Growth-led rally", "Stocks and yields are both rising, which usually reflects stronger growth expectations."
    elif spx >= 1:
        label, detail = "Rally", "Stocks are rising without pressure from yields."
    else:
        label, detail = "Mixed", "Stocks are little changed over the week."
    flags = []
    if oil >= 4:
        flags.append("oil is spiking")
    if oil <= -4:
        flags.append("oil is sliding")
    if gold >= 2:
        flags.append("gold is bid")
    if gold <= -2:
        flags.append("gold is falling too, so even a usual safe haven is being sold")
    if dollar >= 1:
        flags.append("the dollar is rising")
    if dollar <= -1:
        flags.append("the dollar is falling")
    return {"label": label, "detail": detail, "flags": flags}


def move(value: float, unit: str) -> str:
    verb = "rose" if value > 0 else "fell" if value < 0 else "held"
    amount = f"{abs(value):.0f} bp" if unit == "bp" else f"{abs(value):.1f}" + ("" if unit == "pts" else "%")
    return f"{verb} {amount}" if value else verb


def build_read(drivers: dict, market: dict, news: list[dict], state: dict) -> list[str]:
    d = drivers
    day = dt.date.fromisoformat(d["spx"]["date"]).strftime("%A, %b %-d")
    lines = [f"The S&P 500 {move(d['spx']['chg_1d'], 'pct')} on {day} and {move(d['spx']['chg_1w'], 'pct')} over the past week. The VIX closed at {d['vix']['last']:.1f}."]
    lines.append(f"Over the week, the 10-year yield {move(d['y10']['chg_1w'], 'bp')} to {d['y10']['last']:.2f}%, oil {move(d['wti']['chg_1w'], 'pct')}, gold {move(d['gold']['chg_1w'], 'pct')}, the dollar {move(d['dxy']['chg_1w'], 'pct')}, and chip stocks {move(d['semis']['chg_1w'], 'pct')}.")
    sectors = sorted(market.get("sectors", []), key=lambda s: s.get("ret_1w", 0))
    if sectors:
        lines.append(f"Strongest sector this week: {sectors[-1]['name']} ({sectors[-1]['ret_1w']:+.1f}%). Weakest: {sectors[0]['name']} ({sectors[0]['ret_1w']:+.1f}%).".replace("-", "−"))
    counts: dict[str, int] = {}
    for n in news[:30]:
        for t in n["themes"]:
            if t != "markets":
                counts[t] = counts.get(t, 0) + 1
    top = sorted(counts, key=counts.get, reverse=True)[:3]
    if top:
        lines.append("The biggest news themes right now: " + ", ".join(THEMES[t][0] for t in top) + ".")
    return lines


def main() -> None:
    prior: dict = {}
    if OUT.exists():
        try:
            prior = json.loads(OUT.read_text(encoding="utf-8"))["rows"][0]
        except (ValueError, KeyError, IndexError):
            prior = {}
    prior_macro = {m["id"]: m for m in prior.get("macro", [])}
    summary = json.loads((ROOT / "data" / "outlook_summary.json").read_text(encoding="utf-8"))["rows"]
    market = json.loads((ROOT / "data" / "market.json").read_text(encoding="utf-8"))["rows"][0]
    stock_news = {r["symbol"]: r["headlines"] for r in json.loads((ROOT / "data" / "news.json").read_text(encoding="utf-8"))["rows"]}
    symbols = [r["symbol"] for r in summary]
    groups = {r["symbol"]: r.get("group") for r in summary}
    cut = settled_day()
    need = sorted({d[3] for d in DRIVERS} | {f[2] for f in FACTORS + SPY_FACTORS} | set(symbols) | {"SPY"})
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        data = dict(zip(need, pool.map(prices, need)))
    rows = [driver_row(spec, data.get(spec[3], {}), cut) for spec in DRIVERS]
    drivers = {r["id"]: r for r in rows if r}
    missing = [k for k in ("spx", "vix", "y10", "wti", "gold", "dxy", "semis") if k not in drivers]
    if missing:
        raise SystemExit(f"core drivers missing: {missing}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        fred = dict(zip([m[3] for m in MACRO], pool.map(B.fetch_fred, [m[3] for m in MACRO])))
    macro = [row for row in (macro_row(spec, fred.get(spec[3], {}), prior_macro) for spec in MACRO) if row]
    news, feeds_ok, feeds_failed, news_stale = build_news(prior)
    factors = {f[0]: data[f[2]] for f in FACTORS + SPY_FACTORS}
    common = set.intersection(*[set(v) for v in factors.values()])
    chip_beta = chip_betas(factors, common, cut)
    index = {}
    for i, n in enumerate(news):
        for s in n["tickers"]:
            index.setdefault(s, []).append(i)
    stocks = {}
    for s in symbols:
        result = attribute(s, data.get(s, {}), factors, common, cut, chip_beta)
        if not result:
            continue
        fund = groups.get(s) in ("Market & sector ETFs", "Leveraged ETFs")
        related = list(range(min(4, len(news)))) if fund else index.get(s, [])[:3]
        strong = sorted((e for e in result["exposures"] if e["id"] != "market" and abs(e["t"]) >= 3), key=lambda e: -abs(e["t"]))
        for e in strong[:2]:
            theme = FACTOR_THEME[e["id"]]
            related += [i for i, n in enumerate(news) if theme in n["themes"] and i not in related][:2]
        result["news"] = related[:4]
        result["company_news"] = [{"title": h["title"], "link": h.get("link"), "published": h.get("published")} for h in stock_news.get(s, []) if not fund and not NOISE_RE.search(h["title"]) and mentions(s, h["title"])][:3]
        stocks[s] = result
    if len(stocks) < 0.9 * len(symbols):
        raise SystemExit(f"attribution covered only {len(stocks)} of {len(symbols)} symbols")
    if len(macro) < 12:
        raise SystemExit(f"only {len(macro)} macro series available")
    state = regime(drivers)
    theme_rows = []
    for key, (name, _, proxies) in THEMES.items():
        theme_rows.append({"id": key, "name": name, "count": sum(key in n["themes"] for n in news),
                           "proxies": [{"id": p, "name": drivers[p]["name"], "unit": drivers[p]["unit"], "chg_1d": drivers[p]["chg_1d"], "chg_1w": drivers[p]["chg_1w"], "up_good": drivers[p]["up_good"]} for p in proxies if p in drivers]})
    generated = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload = {"asof": drivers["spx"]["date"], "regime": state, "read": build_read(drivers, market, news, state), "drivers": list(drivers.values()), "macro": macro, "themes": theme_rows,
               "news": news, "factors": [{"id": f[0], "name": f[1], "per": f[4], "short": FACTOR_TEXT[f[0]][0], "phrase": FACTOR_TEXT[f[0]][1]} for f in FACTORS + SPY_FACTORS[:1]], "stocks": stocks}
    meta = {"generated_at": generated, "price_asof": payload["asof"], "news_window_hours": NEWS_HOURS, "news_stale": news_stale, "feeds_ok": len(feeds_ok), "feeds_failed": feeds_failed,
            "method": f"Each stock's daily returns over the last {REG_DAYS} trading days regressed on the S&P 500, market-neutral chip stocks, 10-year yield, oil, dollar, gold and bitcoin (SPY uses the equal-weight S&P in place of the S&P 500 and omits bitcoin). Shows what a move lined up with, not proof of cause."}
    temp = OUT.with_suffix(".tmp")
    temp.write_text(json.dumps({"rows": [payload], "meta": meta}, allow_nan=False, separators=(",", ":")), encoding="utf-8")
    os.replace(temp, OUT)
    print(f"drivers: {len(drivers)} markets, {len(macro)} macro, {len(news)} news ({len(feeds_ok)} feeds ok, failed {feeds_failed}), {len(stocks)} stocks")


if __name__ == "__main__":
    main()
