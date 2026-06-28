"""Tests for shared.indicators — RSI / MACD / SMA.

Two kinds of test live here:

  * **Invariants** — properties that must hold for any correct implementation
    (RSI bounded [0, 100], all-gains -> 100, all-losses -> 0, warmup is NaN).
    These catch real logic bugs.
  * **Characterization** — exact values locked against a fixed input vector, so
    an accidental change to the formula (e.g. swapping ``adjust=False``) trips a
    test. Note: this RSI uses pandas EWM smoothing, which differs slightly from
    canonical Wilder seeding, so the golden numbers are this implementation's
    own output, not the textbook 70.53 figure.
"""

import numpy as np
import pandas as pd
import pytest

from shared.config import RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL
from shared.indicators import (
    _rsi,
    _macd,
    add_daily_indicators,
    add_weekly_rsi,
)

# StockCharts' classic worked-example close series (33 points).
CLOSES = [
    44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
    45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    46.21, 46.25, 45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57,
    43.42, 42.66, 43.13,
]


def _frame(closes):
    """A daily OHLCV frame with a business-day index (only Close is used here)."""
    idx = pd.bdate_range("2020-01-01", periods=len(closes))
    return pd.DataFrame({"Close": closes}, index=idx)


# --------------------------------------------------------------------------- #
# RSI — invariants
# --------------------------------------------------------------------------- #

def test_rsi_strictly_rising_is_100():
    """All gains, no losses -> avg_loss 0 -> RSI pinned at 100."""
    rsi = _rsi(pd.Series(np.arange(1.0, 41.0)), RSI_PERIOD)
    assert (rsi.dropna() == 100.0).all()


def test_rsi_strictly_falling_is_0():
    """All losses, no gains -> avg_gain 0 -> RSI pinned at 0."""
    rsi = _rsi(pd.Series(np.arange(40.0, 0.0, -1.0)), RSI_PERIOD)
    assert (rsi.dropna() == 0.0).all()


def test_rsi_constant_series_is_nan():
    """No movement -> 0/0 -> RSI undefined (NaN) throughout."""
    rsi = _rsi(pd.Series([50.0] * 40), RSI_PERIOD)
    assert rsi.isna().all()


def test_rsi_is_bounded():
    rsi = _rsi(pd.Series(CLOSES), RSI_PERIOD).dropna()
    assert ((rsi >= 0.0) & (rsi <= 100.0)).all()


def test_rsi_warmup_is_nan():
    """min_periods + the leading diff() NaN means the first valid value is at
    index 13 for this implementation; everything before is NaN."""
    rsi = _rsi(pd.Series(CLOSES), RSI_PERIOD)
    assert rsi.iloc[:13].isna().all()
    assert not np.isnan(rsi.iloc[13])
    assert rsi.first_valid_index() == 13


# --------------------------------------------------------------------------- #
# RSI — characterization (regression lock on CLOSES)
# --------------------------------------------------------------------------- #

def test_rsi_golden_values():
    rsi = _rsi(pd.Series(CLOSES), RSI_PERIOD)
    assert rsi.iloc[14] == pytest.approx(71.802411, abs=1e-5)
    assert rsi.iloc[20] == pytest.approx(61.244120, abs=1e-5)
    assert rsi.iloc[-1] == pytest.approx(35.511902, abs=1e-5)


# --------------------------------------------------------------------------- #
# MACD
# --------------------------------------------------------------------------- #

def test_macd_constant_series_is_zero():
    """Equal fast/slow EMAs on a flat series -> MACD, signal, hist all 0."""
    macd = _macd(pd.Series([100.0] * 60), MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    assert macd["MACD"].abs().max() == pytest.approx(0.0, abs=1e-9)
    assert macd["MACD_signal"].abs().max() == pytest.approx(0.0, abs=1e-9)
    assert macd["MACD_hist"].abs().max() == pytest.approx(0.0, abs=1e-9)


def test_macd_hist_equals_line_minus_signal():
    """Definitional identity that must hold on every row."""
    macd = _macd(pd.Series(CLOSES), MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    expected = macd["MACD"] - macd["MACD_signal"]
    pd.testing.assert_series_equal(
        macd["MACD_hist"], expected, check_names=False
    )


def test_macd_uptrend_line_positive():
    """A sustained ramp -> fast EMA above slow EMA -> positive MACD line."""
    macd = _macd(pd.Series(np.arange(1.0, 80.0)), MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    assert macd["MACD"].iloc[-1] > 0


def test_macd_golden_values():
    macd = _macd(pd.Series(CLOSES), MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    assert macd["MACD"].iloc[-1] == pytest.approx(-0.501505, abs=1e-5)
    assert macd["MACD_signal"].iloc[-1] == pytest.approx(-0.142651, abs=1e-5)
    assert macd["MACD_hist"].iloc[-1] == pytest.approx(-0.358854, abs=1e-5)


# --------------------------------------------------------------------------- #
# SMA50 (via add_daily_indicators)
# --------------------------------------------------------------------------- #

def test_sma50_constant_series():
    df = add_daily_indicators(_frame([7.0] * 60))
    assert df["SMA50"].iloc[:49].isna().all()      # not enough history yet
    assert df["SMA50"].iloc[49:].eq(7.0).all()      # flat -> equals the level


def test_sma50_matches_rolling_mean():
    df = add_daily_indicators(_frame(list(np.arange(1.0, 61.0))))
    # Last window is the mean of values 11..60 -> 35.5
    assert df["SMA50"].iloc[-1] == pytest.approx(35.5)


# --------------------------------------------------------------------------- #
# Wiring — add_daily_indicators / add_weekly_rsi do not mutate input
# --------------------------------------------------------------------------- #

def test_add_daily_indicators_adds_expected_columns():
    df = add_daily_indicators(_frame(CLOSES))
    for col in ("RSI", "MACD", "MACD_hist", "MACD_signal", "SMA50"):
        assert col in df.columns


def test_add_daily_indicators_does_not_mutate_input():
    original = _frame(CLOSES)
    cols_before = list(original.columns)
    add_daily_indicators(original)
    assert list(original.columns) == cols_before  # operated on a copy


def test_add_weekly_rsi_adds_rsi_only():
    weekly = add_weekly_rsi(_frame(CLOSES))
    assert "RSI" in weekly.columns
    assert "MACD" not in weekly.columns
