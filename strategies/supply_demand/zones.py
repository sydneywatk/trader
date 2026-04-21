"""Supply & Demand zone detection and lifecycle management (daily bars).

Algorithm (no lookahead): walk chronologically. For each candidate base-end
bar i, build the longest run of small consolidation candles ending at i.
Then check the next IMPULSE_CHECK_BARS for a strong directional candle —
the "impulse" that confirms the zone. Zone formation_date = the impulse
bar's date (when the zone first becomes visible to a real-time trader).
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import numpy as np
import pandas as pd

import config  # read TIMEFRAME-sensitive values at runtime
from config import (
    BASE_MAX_CANDLES,
    BASE_RANGE_ATR_MULT,
    BASE_BODY_RATIO_MAX,
    IMPULSE_RANGE_ATR_MULT,
    IMPULSE_BODY_RATIO_MIN,
    IMPULSE_CHECK_BARS,
    MIN_MOVE_AWAY_ATR,
    TREND_LOOKBACK,
    MAX_ZONE_TESTS,
)


def calculate_atr(df: pd.DataFrame, period: int = 20) -> pd.Series:
    """Wilder's ATR over `period` bars, returned as a pd.Series aligned to df.index."""
    high = df["High"]
    low = df["Low"]
    close = df["Close"]
    prev_close = close.shift(1)

    tr = pd.concat(
        [
            (high - low),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    return atr


def _candle_range_and_body(df: pd.DataFrame, i: int) -> tuple[float, float]:
    r = float(df["High"].iloc[i]) - float(df["Low"].iloc[i])
    b = abs(float(df["Close"].iloc[i]) - float(df["Open"].iloc[i]))
    return r, b


def detect_base_candles(df: pd.DataFrame, end_idx: int, atr: pd.Series) -> Optional[list[int]]:
    """Walk backwards from end_idx to build the longest valid base run.

    Returns a list of bar indices [start, ..., end_idx] if a valid base exists,
    else None. A base candle satisfies:
        range <= BASE_RANGE_ATR_MULT * atr[end_idx]
        body  <= BASE_BODY_RATIO_MAX  * range   (guards doji-only clusters elsewhere)
    """
    atr_val = atr.iloc[end_idx]
    if pd.isna(atr_val) or atr_val <= 0:
        return None
    max_range = BASE_RANGE_ATR_MULT * float(atr_val)

    base = []
    for k in range(end_idx, max(end_idx - BASE_MAX_CANDLES, -1), -1):
        if k < 0:
            break
        rng, body = _candle_range_and_body(df, k)
        if rng <= 0:
            break
        if rng > max_range:
            break
        if body > BASE_BODY_RATIO_MAX * rng:
            break
        base.append(k)

    if not base:
        return None
    base.reverse()
    return base


def detect_impulse(
    df: pd.DataFrame, base_end_idx: int, atr: pd.Series
) -> tuple[int, Optional[int]]:
    """Look for a strong directional candle in the next IMPULSE_CHECK_BARS bars.

    Returns (direction, impulse_bar_idx):
        direction = 1 for bullish, -1 for bearish, 0 for no impulse found
    """
    atr_val = atr.iloc[base_end_idx]
    if pd.isna(atr_val) or atr_val <= 0:
        return 0, None
    min_range = IMPULSE_RANGE_ATR_MULT * float(atr_val)

    n = len(df)
    last = min(base_end_idx + IMPULSE_CHECK_BARS, n - 1)
    for j in range(base_end_idx + 1, last + 1):
        rng, body = _candle_range_and_body(df, j)
        if rng < min_range:
            continue
        if body < IMPULSE_BODY_RATIO_MIN * rng:
            continue
        close_j = float(df["Close"].iloc[j])
        open_j = float(df["Open"].iloc[j])
        if close_j > open_j:
            return 1, j
        if close_j < open_j:
            return -1, j
    return 0, None


def classify_zone(df: pd.DataFrame, base_indices: list[int], impulse_direction: int) -> str:
    """Return 'DBR', 'RBD', 'RBR', or 'DBD' based on pre-trend and post-impulse."""
    base_start = base_indices[0]
    window_start = base_start - TREND_LOOKBACK
    if window_start < 0:
        preceding_up = True  # degenerate: not enough history — default to neutral up
    else:
        first_close = float(df["Close"].iloc[window_start])
        last_close = float(df["Close"].iloc[base_start - 1])
        preceding_up = last_close > first_close

    if impulse_direction == 1:
        return "RBR" if preceding_up else "DBR"
    else:
        return "RBD" if preceding_up else "DBD"


def get_zone_boundaries(
    df: pd.DataFrame, base_indices: list[int], direction: int
) -> tuple[float, float]:
    """(proximal, distal) given base bars and impulse direction.

    Demand (bullish impulse): proximal = max(base highs), distal = min(base lows).
    Supply (bearish impulse): proximal = min(base lows),  distal = max(base highs).
    """
    base_highs = df["High"].iloc[base_indices]
    base_lows = df["Low"].iloc[base_indices]
    high_max = float(base_highs.max())
    low_min = float(base_lows.min())
    if direction == 1:
        return high_max, low_min   # demand
    return low_min, high_max        # supply


def detect_all_zones(df: pd.DataFrame, atr: pd.Series) -> list[dict]:
    """Scan the dataframe chronologically and return every valid zone.

    Each zone is keyed by the bar index where the impulse confirms it.
    A subsequent scan skips past the impulse bar to avoid duplicate/overlapping
    zones built from the same consolidation.
    """
    zones: list[dict] = []
    n = len(df)
    i = TREND_LOOKBACK  # need some history for classification
    while i < n - 1:
        base = detect_base_candles(df, i, atr)
        if not base:
            i += 1
            continue
        direction, impulse_idx = detect_impulse(df, i, atr)
        if direction == 0 or impulse_idx is None:
            i += 1
            continue

        zone_type = classify_zone(df, base, direction)
        proximal, distal = get_zone_boundaries(df, base, direction)

        zones.append(
            {
                "formation_date": df.index[impulse_idx],
                "formation_idx": impulse_idx,
                "base_start_idx": base[0],
                "base_end_idx": base[-1],
                "zone_type": zone_type,
                "direction": "demand" if direction == 1 else "supply",
                "proximal": proximal,
                "distal": distal,
                "atr_at_formation": float(atr.iloc[impulse_idx]),
                "test_count": 0,
                "active": True,
                "tappable": False,
                "priority": "high" if zone_type in ("DBR", "RBD") else "low",
            }
        )

        # Skip past the impulse to avoid overlapping detections from the same area.
        i = impulse_idx + 1

    return zones


def update_zone_status(
    zones: list[dict], df: pd.DataFrame, current_idx: int
) -> None:
    """Mutate `zones` in place: update tappable flag, deactivate stale or exhausted zones.

    Must be called in chronological order. Only uses information available at current_idx.
    """
    high_now = float(df["High"].iloc[current_idx])
    low_now = float(df["Low"].iloc[current_idx])

    age_cap = config.get_zone_age_cap()
    move_away_window = config.get_move_away_window()

    for z in zones:
        if not z["active"]:
            continue
        if z["formation_idx"] > current_idx:
            continue

        # Age expiration (bars since formation — 1 bar per day on daily,
        # 1 bar per hour on 1h).
        age = current_idx - z["formation_idx"]
        if age > age_cap:
            z["active"] = False
            continue

        # Tappable gate: price must have moved >= MIN_MOVE_AWAY_ATR past proximal
        if not z["tappable"]:
            move = MIN_MOVE_AWAY_ATR * z["atr_at_formation"]
            if z["direction"] == "demand":
                if high_now >= z["proximal"] + move:
                    z["tappable"] = True
            else:  # supply
                if low_now <= z["proximal"] - move:
                    z["tappable"] = True

            # Follow-through window: if the zone hasn't become tappable within
            # MIN_MOVE_AWAY_BARS bars (1h mode), retire it — a zone that doesn't
            # push price away quickly likely isn't a real supply/demand imbalance.
            if not z["tappable"] and age > move_away_window:
                z["active"] = False
                continue

        # Freshness retirement
        if z["test_count"] >= MAX_ZONE_TESTS:
            z["active"] = False
