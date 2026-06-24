"""S&P 500 current-members loader.

Fetches from Wikipedia on first call; caches as CSV; falls back to cache.
Acknowledged limitation: uses today's membership (survivorship biased).
See SPEC §2 for bias handling.
"""

import io
import os
import ssl
import urllib.request
from pathlib import Path

import certifi
import pandas as pd

from config import UNIVERSE_CACHE
from shared.config import CACHE_DIR

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, UNIVERSE_CACHE)


def _fetch_from_wikipedia() -> list[str]:
    # Use certifi's CA bundle — macOS Python.framework installs often lack one.
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(
        WIKI_URL,
        headers={"User-Agent": "Mozilla/5.0 (breakout-backtester)"},
    )
    with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
        html = resp.read().decode("utf-8")
    tables = pd.read_html(io.StringIO(html))
    # First table is the current constituents list.
    df = tables[0]
    # yfinance uses hyphens for some class-share tickers (BRK.B -> BRK-B).
    tickers = df["Symbol"].astype(str).str.replace(".", "-", regex=False).tolist()
    return sorted(set(tickers))


def load_universe(refresh: bool = False) -> list[str]:
    path = _cache_path()
    if not refresh and os.path.exists(path):
        df = pd.read_csv(path)
        return df["ticker"].tolist()
    tickers = _fetch_from_wikipedia()
    pd.DataFrame({"ticker": tickers}).to_csv(path, index=False)
    return tickers


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    tickers = load_universe(refresh=True)
    print(f"Loaded {len(tickers)} S&P 500 tickers")
    print(tickers[:10], "...")
