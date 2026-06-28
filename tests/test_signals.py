"""Tests for strategies.sid_method.signals.find_rsi_signals.

The function reads only ``df["RSI"]`` and ``df.index``, so these tests feed a
hand-built RSI column and assert the exact crosses — no indicator math involved.
Threshold semantics under test (from the SID Method checklist):

  * oversold cross  : prev >= 30 and curr < 30   -> type "OS"
  * overbought cross: prev <= 70 and curr > 70   -> type "OB"
"""

import numpy as np
import pandas as pd

from config import RSI_OVERSOLD, RSI_OVERBOUGHT
from signals import find_rsi_signals


def _rsi_frame(values):
    idx = pd.bdate_range("2020-01-01", periods=len(values))
    return pd.DataFrame({"RSI": values}, index=idx)


def test_thresholds_are_30_70():
    """Guard against a config drift silently changing what the tests mean."""
    assert RSI_OVERSOLD == 30
    assert RSI_OVERBOUGHT == 70


def test_detects_oversold_and_overbought_crosses():
    df = _rsi_frame([40, 35, 28, 25, 33, 45, 68, 72, 69, 71])
    signals = find_rsi_signals(df)

    assert [s["type"] for s in signals] == ["OS", "OB", "OB"]
    # OS cross when 35 -> 28 (index 2); OB crosses at 68->72 (7) and 69->71 (9).
    assert [s["date"] for s in signals] == [
        df.index[2], df.index[7], df.index[9],
    ]
    assert [s["rsi"] for s in signals] == [28, 72, 71]


def test_no_signal_when_threshold_only_touched_not_crossed():
    # 30 -> 30 is not "< 30"; 70 -> 70 is not "> 70". Neither should fire.
    df = _rsi_frame([35, 30, 30, 65, 70, 70])
    assert find_rsi_signals(df) == []


def test_exact_boundary_crosses_fire():
    # 30 -> 29.99 is an OS cross; 70 -> 70.01 is an OB cross.
    df = _rsi_frame([30, 29.99, 50, 70, 70.01])
    types = [s["type"] for s in find_rsi_signals(df)]
    assert types == ["OS", "OB"]


def test_sustained_oversold_fires_once_on_the_cross():
    # Dipping below 30 and staying there should signal only on the crossing bar.
    df = _rsi_frame([40, 25, 22, 20, 28])
    signals = find_rsi_signals(df)
    assert len(signals) == 1
    assert signals[0]["date"] == df.index[1]


def test_nan_values_are_skipped():
    # Warmup NaNs (and any gaps) must not raise or produce phantom crosses.
    df = _rsi_frame([np.nan, np.nan, 35, 28, np.nan, 25])
    signals = find_rsi_signals(df)
    assert [s["type"] for s in signals] == ["OS"]
    assert signals[0]["date"] == df.index[3]


def test_empty_frame_returns_empty_list():
    assert find_rsi_signals(_rsi_frame([])) == []
