"""Data fetching and caching via yfinance."""

import os
import time
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from config import CACHE_DIR, START_DATE, END_DATE


def _get_end_date() -> str:
    if END_DATE == "today":
        return datetime.now().strftime("%Y-%m-%d")
    return END_DATE


def _cache_path(ticker: str, freq: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}_{freq}.csv")


def _cache_is_fresh(path: str, max_age_hours: int = 24) -> bool:
    if not os.path.exists(path):
        return False
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def fetch_daily(ticker: str) -> pd.DataFrame:
    """Fetch daily OHLCV data for a ticker, using cache if fresh."""
    path = _cache_path(ticker, "daily")
    if _cache_is_fresh(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) > 0:
            return df

    end = _get_end_date()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(ticker, start=START_DATE, end=end, interval="1d",
                         progress=False, auto_adjust=True)
    if df.empty:
        return df

    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.to_csv(path)
    return df


def fetch_weekly(ticker: str) -> pd.DataFrame:
    """Fetch weekly OHLCV data for a ticker, using cache if fresh."""
    path = _cache_path(ticker, "weekly")
    if _cache_is_fresh(path):
        df = pd.read_csv(path, index_col=0, parse_dates=True)
        if len(df) > 0:
            return df

    end = _get_end_date()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(ticker, start=START_DATE, end=end, interval="1wk",
                         progress=False, auto_adjust=True)
    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.to_csv(path)
    return df
