"""Breakout-local daily data fetcher.

Uses its own cache prefix (`{ticker}_bo.csv`) so the 2013-start pull does not
collide with SID/S&D (which expect START_DATE=2020 per shared/config.py).
Bulk yf.download for speed; per-ticker CSV for compatibility with existing
caching conventions.
"""

import os
import time
import warnings
from typing import Iterable

import pandas as pd
import yfinance as yf

from shared.config import CACHE_DIR
from config import DATA_START


def _cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}_bo.csv")


def _cache_fresh(path: str, max_age_hours: int = 24) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def _save_df(ticker: str, df: pd.DataFrame) -> None:
    if df is None or df.empty:
        return
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.to_csv(_cache_path(ticker))


def _load_cached(ticker: str) -> pd.DataFrame:
    path = _cache_path(ticker)
    if not _cache_fresh(path):
        return pd.DataFrame()
    df = pd.read_csv(path, index_col=0, parse_dates=True)
    return df


def bulk_fetch(tickers: Iterable[str], start: str = None,
               end: str = None, force_refresh: bool = False) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for all tickers, using cache where fresh.

    Returns dict ticker -> DataFrame with columns Open/High/Low/Close/Volume.
    Tickers with empty data are skipped from the dict.
    """
    tickers = list(tickers)
    start = start or DATA_START
    if end is None:
        end = pd.Timestamp.today().strftime("%Y-%m-%d")

    result: dict[str, pd.DataFrame] = {}
    to_pull: list[str] = []

    if not force_refresh:
        for t in tickers:
            df = _load_cached(t)
            if not df.empty and df.index.min() <= pd.Timestamp(start) + pd.Timedelta(days=10):
                result[t] = df
            else:
                to_pull.append(t)
    else:
        to_pull = list(tickers)

    if not to_pull:
        return result

    print(f"[data] fetching {len(to_pull)} tickers from yfinance "
          f"({start} -> {end})...")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(
            to_pull,
            start=start,
            end=end,
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker",
            threads=True,
        )

    if df.empty:
        return result

    # yf.download returns a multi-level df when multiple tickers are passed.
    if isinstance(df.columns, pd.MultiIndex):
        for t in to_pull:
            if t not in df.columns.get_level_values(0):
                continue
            sub = df[t].dropna(how="all")
            if sub.empty:
                continue
            _save_df(t, sub)
            result[t] = sub
    else:
        # Single-ticker case — df has flat columns
        t = to_pull[0]
        sub = df.dropna(how="all")
        if not sub.empty:
            _save_df(t, sub)
            result[t] = sub

    return result


def fetch_one(ticker: str, start: str = None) -> pd.DataFrame:
    """Convenience wrapper for single-ticker pulls (e.g. SPY)."""
    out = bulk_fetch([ticker], start=start)
    return out.get(ticker, pd.DataFrame())
