"""1-hour OHLCV bar fetcher — Alpaca (primary) / yfinance (fallback).

Caches to `cache/{ticker}_1h.csv` with 24-hour freshness.
Filters to US regular trading hours (09:30–16:00 ET) — 7 bars per day
indexed at 09:30, 10:30, ..., 15:30 local ET (tz-naive).

Alpaca credentials, when present, come from environment variables loaded
via python-dotenv at the caller:
    ALPACA_API_KEY, ALPACA_SECRET_KEY
"""

from __future__ import annotations

import os
import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from shared.config import CACHE_DIR

_ALPACA_BASE = "https://data.alpaca.markets/v2"


def _cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}_1h.csv")


def _cache_fresh(path: str, max_age_hours: int = 24) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def _alpaca_keys() -> Optional[tuple[str, str]]:
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_SECRET_KEY")
    if not key or not secret:
        return None
    return key, secret


def _to_regular_hours_et(df: pd.DataFrame) -> pd.DataFrame:
    """Filter to US regular hours and return a tz-naive ET-indexed frame."""
    if df.empty:
        return df
    idx = df.index
    if getattr(idx, "tz", None) is None:
        idx = idx.tz_localize("UTC")
    et = idx.tz_convert("America/New_York")
    mins_of_day = et.hour * 60 + et.minute
    # Regular session: 09:30 (min 570) through the 15:30 bar (last hourly open)
    mask = (mins_of_day >= 570) & (mins_of_day <= 930)
    kept = df[mask].copy()
    # Re-index to tz-naive ET wall time (stable, CSV-friendly)
    kept.index = et[mask].tz_localize(None)
    kept.index.name = "datetime"
    return kept


def _fetch_alpaca(ticker: str, start_iso: str, end_iso: str) -> Optional[pd.DataFrame]:
    """Pull 1h bars from Alpaca. Returns None if keys missing or request fails."""
    import requests  # local import so module imports stay cheap

    keys = _alpaca_keys()
    if keys is None:
        return None
    key, secret = keys
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    url = f"{_ALPACA_BASE}/stocks/{ticker}/bars"
    all_bars: list[dict] = []
    token: Optional[str] = None

    while True:
        params = {
            "timeframe": "1Hour",
            "start": start_iso,
            "end": end_iso,
            "limit": 10000,
            "adjustment": "split",
            "feed": "iex",  # free-tier feed
        }
        if token:
            params["page_token"] = token
        try:
            r = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception:
            return None
        if r.status_code != 200:
            return None
        data = r.json()
        bars = data.get("bars", []) or []
        all_bars.extend(bars)
        token = data.get("next_page_token")
        if not token:
            break

    if not all_bars:
        return pd.DataFrame()

    df = pd.DataFrame(all_bars)
    df["datetime"] = pd.to_datetime(df["t"])
    df = df.set_index("datetime")
    df = df.rename(
        columns={"o": "Open", "h": "High", "l": "Low", "c": "Close", "v": "Volume"}
    )[["Open", "High", "Low", "Close", "Volume"]]
    return df


def _fetch_yfinance(ticker: str) -> pd.DataFrame:
    """yfinance 1h bars — hard-capped at 730 days by Yahoo."""
    import yfinance as yf

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(
            ticker,
            period="730d",
            interval="1h",
            progress=False,
            auto_adjust=True,
        )
    if df is None or df.empty:
        return pd.DataFrame()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df


def fetch_hourly(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    source: str = "auto",
) -> pd.DataFrame:
    """Fetch 1-hour OHLCV bars.

    source:
        'auto'     — Alpaca if ALPACA_API_KEY set, else yfinance
        'alpaca'   — force Alpaca (returns empty if keys missing)
        'yfinance' — force yfinance (730-day cap)

    Returns a DataFrame indexed by tz-naive ET timestamps with columns
    Open/High/Low/Close/Volume, filtered to 09:30–16:00 ET.
    """
    path = _cache_path(ticker)
    if _cache_fresh(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) > 0:
            return df

    if source == "auto":
        source = "alpaca" if _alpaca_keys() else "yfinance"

    df: Optional[pd.DataFrame] = None
    if source == "alpaca":
        start_iso = start_date or (
            datetime.now(timezone.utc) - timedelta(days=3650)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        # Free tier restricts the last 15 minutes — back off to 20 to be safe
        end_iso = end_date or (
            datetime.now(timezone.utc) - timedelta(minutes=20)
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
        df = _fetch_alpaca(ticker, start_iso, end_iso)
    if df is None or df.empty:
        df = _fetch_yfinance(ticker)

    if df is None or df.empty:
        return pd.DataFrame()

    df = _to_regular_hours_et(df)
    if df.empty:
        return df

    df.to_csv(path)
    return df
