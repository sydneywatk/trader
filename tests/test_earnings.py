"""Tests for shared.earnings — the pure (no-network) helpers.

``fetch_earnings_dates`` hits yfinance and is intentionally not exercised here;
the date logic that the strategy actually depends on is pure and fully testable:

  * next_earnings_date            — first date on/after a cutoff
  * earnings_safe                 — next earnings strictly > 14 days out
  * last_trading_day_before_earnings
"""

from datetime import datetime

import pandas as pd

from shared.config import EARNINGS_MIN_DAYS
from shared.earnings import (
    next_earnings_date,
    earnings_safe,
    last_trading_day_before_earnings,
)


def _dt(s):
    return datetime.fromisoformat(s)


# --------------------------------------------------------------------------- #
# next_earnings_date
# --------------------------------------------------------------------------- #

def test_next_earnings_picks_first_future_date():
    dates = [_dt("2024-01-10"), _dt("2024-04-15"), _dt("2024-07-20")]
    assert next_earnings_date(dates, _dt("2024-02-01")) == _dt("2024-04-15")


def test_next_earnings_includes_same_day():
    dates = [_dt("2024-04-15")]
    assert next_earnings_date(dates, _dt("2024-04-15")) == _dt("2024-04-15")


def test_next_earnings_none_when_all_past():
    dates = [_dt("2023-01-10"), _dt("2023-04-15")]
    assert next_earnings_date(dates, _dt("2024-01-01")) is None


def test_next_earnings_empty_list():
    assert next_earnings_date([], _dt("2024-01-01")) is None


# --------------------------------------------------------------------------- #
# earnings_safe  (buffer is EARNINGS_MIN_DAYS = 14, strict ">")
# --------------------------------------------------------------------------- #

def test_earnings_safe_far_away_is_safe():
    nxt = _dt("2024-05-01")
    safe, returned = earnings_safe([nxt], _dt("2024-04-01"))  # 30 days out
    assert safe is True
    assert returned == nxt


def test_earnings_safe_exactly_buffer_is_not_safe():
    # Exactly 14 days out -> not strictly > 14 -> unsafe.
    entry = _dt("2024-04-01")
    nxt = _dt("2024-04-15")
    assert (nxt.date() - entry.date()).days == EARNINGS_MIN_DAYS
    safe, _ = earnings_safe([nxt], entry)
    assert safe is False


def test_earnings_safe_one_day_past_buffer_is_safe():
    safe, _ = earnings_safe([_dt("2024-04-16")], _dt("2024-04-01"))  # 15 days
    assert safe is True


def test_earnings_safe_no_data_defaults_to_safe():
    safe, returned = earnings_safe([], _dt("2024-04-01"))
    assert safe is True
    assert returned is None


def test_earnings_safe_only_past_dates_defaults_to_safe():
    safe, returned = earnings_safe([_dt("2023-01-01")], _dt("2024-04-01"))
    assert safe is True
    assert returned is None


# --------------------------------------------------------------------------- #
# last_trading_day_before_earnings
# --------------------------------------------------------------------------- #

def test_last_trading_day_strictly_before_earnings():
    idx = pd.to_datetime(
        ["2024-04-10", "2024-04-11", "2024-04-12", "2024-04-15", "2024-04-16"]
    )
    df = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    result = last_trading_day_before_earnings(df, _dt("2024-04-15"))
    assert result == pd.Timestamp("2024-04-12")  # strictly before, not 04-15


def test_last_trading_day_none_when_no_prior_days():
    idx = pd.to_datetime(["2024-04-16", "2024-04-17"])
    df = pd.DataFrame({"Close": range(len(idx))}, index=idx)
    assert last_trading_day_before_earnings(df, _dt("2024-04-15")) is None
