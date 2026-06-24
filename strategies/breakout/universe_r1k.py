"""Russell 1000 current-members loader.

Source: iShares IWB ETF holdings CSV. This is the most reliable snapshot of
the current Russell 1000 constituency. Fetched from:
  https://www.ishares.com/us/products/239707/ishares-russell-1000-etf

Limitations:
  - Current membership only (no point-in-time). yfinance does not return
    delisted tickers, so trades on names that left the Russell 1000 are
    invisible. Survivorship bias is documented in SPEC §2.
  - Non-equity holdings (cash placeholders, derivative lines) are filtered out.
"""

import csv
import io
import os
import ssl
import urllib.request
from pathlib import Path

import certifi
import pandas as pd

from shared.config import CACHE_DIR

IWB_URL = (
    "https://www.ishares.com/us/products/239707/ishares-russell-1000-etf/"
    "1467271812596.ajax?fileType=csv&fileName=IWB_holdings&dataType=fund"
)
CACHE_FILE = "russell1000_constituents.csv"

# Placeholder/non-tradable tickers that iShares sometimes lists as Equity but
# are housekeeping entries (cash proxies, futures, etc.) — skip.
_SKIP = {"USD", "XTSLA", "-", ""}


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, CACHE_FILE)


def _fetch_from_ishares() -> tuple[list[str], str]:
    """Download IWB CSV, parse equities. Returns (tickers, as_of_date)."""
    ctx = ssl.create_default_context(cafile=certifi.where())
    req = urllib.request.Request(IWB_URL, headers={"User-Agent": "Mozilla/5.0 (breakout-backtester)"})
    with urllib.request.urlopen(req, context=ctx, timeout=60) as resp:
        raw = resp.read().decode("utf-8-sig")

    as_of = "unknown"
    lines = raw.splitlines()

    # Find header row; metadata is the first ~10 lines.
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Fund Holdings as of"):
            # Format: Fund Holdings as of,"Apr 22, 2026"
            parts = line.split(",", 1)
            if len(parts) == 2:
                as_of = parts[1].strip().strip('"')
        if line.startswith("Ticker,Name,"):
            header_idx = i
            break
    if header_idx is None:
        raise RuntimeError("IWB holdings CSV: could not locate Ticker header row")

    # Parse from header onward with csv module (handles quoted commas).
    reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
    tickers: list[str] = []
    for row in reader:
        asset = (row.get("Asset Class") or "").strip().strip('"')
        ticker = (row.get("Ticker") or "").strip().strip('"').upper()
        if asset != "Equity":
            continue
        if ticker in _SKIP or not ticker:
            continue
        # yfinance hyphen convention for class-share tickers
        ticker = ticker.replace(".", "-")
        tickers.append(ticker)

    tickers = sorted(set(tickers))
    return tickers, as_of


def load_universe(refresh: bool = False) -> tuple[list[str], str]:
    """Return (tickers, as_of_date). Cached CSV sits in cache/."""
    path = _cache_path()
    if not refresh and os.path.exists(path):
        df = pd.read_csv(path)
        if not df.empty and "ticker" in df.columns:
            as_of = df["as_of"].iloc[0] if "as_of" in df.columns else "cached"
            return df["ticker"].tolist(), str(as_of)

    tickers, as_of = _fetch_from_ishares()
    pd.DataFrame({"ticker": tickers, "as_of": [as_of] * len(tickers)}).to_csv(path, index=False)
    return tickers, as_of


if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    ts, as_of = load_universe(refresh=True)
    print(f"Russell 1000 holdings as of {as_of}: {len(ts)} tickers")
    print(ts[:15], "...")
