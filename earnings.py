"""Earnings date fetching and proximity checking."""

import os
import json
import warnings
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
import yfinance as yf

from config import EARNINGS_MIN_DAYS, CACHE_DIR


def _earnings_cache_path(ticker: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"{ticker}_earnings.json")


def _earnings_cache_fresh(path: str, max_age_hours: int = 24) -> bool:
    if not os.path.exists(path):
        return False
    import time
    age = time.time() - os.path.getmtime(path)
    return age < max_age_hours * 3600


def fetch_earnings_dates(ticker: str) -> list[datetime]:
    """Fetch all known earnings dates for a ticker.

    Paginates through yfinance's get_earnings_dates to get historical data.
    Returns a sorted list of datetime objects, or empty list on failure.
    """
    cache_path = _earnings_cache_path(ticker)
    if _earnings_cache_fresh(cache_path):
        with open(cache_path, "r") as f:
            date_strs = json.load(f)
        return [datetime.fromisoformat(d) for d in date_strs]

    all_dates = set()
    try:
        t = yf.Ticker(ticker)
        # Paginate through earnings dates — each call returns ~12 entries
        for offset in range(0, 100, 12):
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    ed = t.get_earnings_dates(limit=12, offset=offset)
                except Exception:
                    break
            if ed is None or ed.empty:
                break
            dates = ed.index.tz_localize(None).to_pydatetime().tolist()
            prev_count = len(all_dates)
            all_dates.update(dates)
            # If no new dates were added, we've exhausted the data
            if len(all_dates) == prev_count:
                break
            # Stop if we've gone far enough back (before 2019)
            earliest = min(dates)
            if earliest.year < 2019:
                break
    except Exception:
        pass

    result = sorted(all_dates)

    # Cache the results
    if result:
        with open(cache_path, "w") as f:
            json.dump([d.isoformat() for d in result], f)

    return result


def next_earnings_date(earnings_dates: list[datetime], as_of: datetime) -> Optional[datetime]:
    """Return the next earnings date on or after `as_of`, or None."""
    for d in earnings_dates:
        if d.date() >= as_of.date():
            return d
    return None


def earnings_safe(earnings_dates: list[datetime], entry_date: datetime) -> tuple[bool, Optional[datetime]]:
    """Check if next earnings is more than EARNINGS_MIN_DAYS away from entry_date.

    Returns (is_safe, next_earnings_dt).
    If no earnings data available, returns (True, None) — proceed without filter.
    """
    nxt = next_earnings_date(earnings_dates, entry_date)
    if nxt is None:
        return True, None
    days_away = (nxt.date() - entry_date.date()).days
    return days_away > EARNINGS_MIN_DAYS, nxt


def last_trading_day_before_earnings(daily_df: pd.DataFrame, earnings_dt: datetime) -> Optional[pd.Timestamp]:
    """Return the last trading day strictly before the earnings date."""
    mask = daily_df.index < pd.Timestamp(earnings_dt.date())
    if mask.any():
        return daily_df.index[mask][-1]
    return None
