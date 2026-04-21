"""Entry signal detection for S&D zones.

Combines zone touch + confirmation candle + HTF trend + earnings filter.
Signals resolve at the close of the trigger bar; entry is on next day's open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

import config  # access as config.X so runtime overrides from main_sd.py take effect


def check_zone_touch(df: pd.DataFrame, idx: int, zone: dict) -> bool:
    """True iff bar `idx` has entered the zone from the correct side."""
    if zone["direction"] == "demand":
        return float(df["Low"].iloc[idx]) <= zone["proximal"]
    return float(df["High"].iloc[idx]) >= zone["proximal"]


def _is_bullish_engulfing(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    o_now, c_now = float(df["Open"].iloc[idx]), float(df["Close"].iloc[idx])
    o_prev, c_prev = float(df["Open"].iloc[idx - 1]), float(df["Close"].iloc[idx - 1])
    return (
        c_now > o_now
        and c_prev < o_prev
        and o_now < c_prev
        and c_now > o_prev
    )


def _is_bearish_engulfing(df: pd.DataFrame, idx: int) -> bool:
    if idx < 1:
        return False
    o_now, c_now = float(df["Open"].iloc[idx]), float(df["Close"].iloc[idx])
    o_prev, c_prev = float(df["Open"].iloc[idx - 1]), float(df["Close"].iloc[idx - 1])
    return (
        c_now < o_now
        and c_prev > o_prev
        and o_now > c_prev
        and c_now < o_prev
    )


def _is_hammer(df: pd.DataFrame, idx: int) -> bool:
    o = float(df["Open"].iloc[idx])
    c = float(df["Close"].iloc[idx])
    h = float(df["High"].iloc[idx])
    l = float(df["Low"].iloc[idx])
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    if body <= 0:
        return False
    lower_wick = min(o, c) - l
    upper_third_start = l + (rng * 2.0 / 3.0)
    return lower_wick >= 2.0 * body and min(o, c) >= upper_third_start


def _is_shooting_star(df: pd.DataFrame, idx: int) -> bool:
    o = float(df["Open"].iloc[idx])
    c = float(df["Close"].iloc[idx])
    h = float(df["High"].iloc[idx])
    l = float(df["Low"].iloc[idx])
    rng = h - l
    if rng <= 0:
        return False
    body = abs(c - o)
    if body <= 0:
        return False
    upper_wick = h - max(o, c)
    lower_third_end = l + (rng / 3.0)
    return upper_wick >= 2.0 * body and max(o, c) <= lower_third_end


def detect_confirmation_candle(
    df: pd.DataFrame, idx: int, direction: str, zone_proximal: float | None = None
) -> tuple[bool, str]:
    """Return (passed, pattern_tag).

    Daily mode (TIMEFRAME='1d'): strict reversal-pattern requirement —
    bullish engulfing / hammer for demand; bearish engulfing / shooting star
    for supply. Pattern tag is the matched pattern name.

    Intraday mode (TIMEFRAME='1h'): simpler "close confirmation" — the touch
    bar must close back on the trade's side of the zone proximal AND close in
    the trade-direction color (bullish candle for demand, bearish for supply).
    Engulfing/hammer adds a "+engulfing" / "+hammer" suffix to the pattern tag
    as a bonus flag (not required for entry).

    If REQUIRE_CONFIRMATION_CANDLE is False, always passes with tag "none".
    If zone_proximal is None in 1h mode, falls through to the daily rule.
    """
    if not config.REQUIRE_CONFIRMATION_CANDLE:
        return True, "none"

    if config.TIMEFRAME == "1h" and zone_proximal is not None:
        close = float(df["Close"].iloc[idx])
        open_ = float(df["Open"].iloc[idx])
        if direction == "demand":
            if close > zone_proximal and close > open_:
                if _is_bullish_engulfing(df, idx):
                    return True, "close+engulfing"
                if _is_hammer(df, idx):
                    return True, "close+hammer"
                return True, "close"
            return False, "none"
        else:  # supply
            if close < zone_proximal and close < open_:
                if _is_bearish_engulfing(df, idx):
                    return True, "close+engulfing"
                if _is_shooting_star(df, idx):
                    return True, "close+shooting_star"
                return True, "close"
            return False, "none"

    # Daily mode: original strict reversal-pattern rule
    if direction == "demand":
        if _is_bullish_engulfing(df, idx):
            return True, "engulfing"
        if _is_hammer(df, idx):
            return True, "hammer"
    else:
        if _is_bearish_engulfing(df, idx):
            return True, "engulfing"
        if _is_shooting_star(df, idx):
            return True, "shooting_star"
    return False, "none"


def check_htf_trend(df: pd.DataFrame, idx: int, direction: str) -> bool:
    """Require price on the right side of SMA50 for the trade direction."""
    if not config.REQUIRE_HTF_ALIGNMENT:
        return True
    if "SMA50" not in df.columns:
        return True  # no SMA available — skip filter
    sma = df["SMA50"].iloc[idx]
    if pd.isna(sma):
        return True
    close = float(df["Close"].iloc[idx])
    if direction == "demand":
        return close > float(sma)
    return close < float(sma)


def _days_to_next_earnings(
    earnings_dates: list[datetime], as_of: datetime
) -> Optional[int]:
    for d in earnings_dates:
        if d.date() >= as_of.date():
            return (d.date() - as_of.date()).days
    return None


def generate_entry_signal(
    df: pd.DataFrame,
    idx: int,
    zone: dict,
    earnings_dates: list[datetime],
    atr: pd.Series,
    ticker: str,
    in_trade_on_ticker: bool,
) -> Optional[dict]:
    """Produce a full entry signal dict, or None if any gate fails.

    Entry executes at next bar's open. Checks are ordered fail-fast.
    """
    if in_trade_on_ticker:
        return None

    if config.SKIP_CONTINUATION_ZONES and zone["priority"] == "low":
        return None

    if not zone["active"] or not zone["tappable"]:
        return None

    if zone["formation_idx"] > idx:
        return None

    if not check_zone_touch(df, idx, zone):
        return None

    passed, pattern = detect_confirmation_candle(
        df, idx, zone["direction"], zone_proximal=zone.get("proximal")
    )
    if not passed:
        return None

    if not check_htf_trend(df, idx, zone["direction"]):
        return None

    # Earnings — measured at entry bar (idx+1) if it exists; fall back to signal bar
    entry_idx = idx + 1
    if entry_idx >= len(df):
        return None
    entry_date = df.index[entry_idx]
    entry_dt = entry_date.to_pydatetime() if hasattr(entry_date, "to_pydatetime") else entry_date

    earnings_days_away = _days_to_next_earnings(earnings_dates, entry_dt)
    if earnings_days_away is not None and earnings_days_away < config.EARNINGS_MIN_DAYS:
        return None

    entry_price = float(df["Open"].iloc[entry_idx])
    if not (entry_price > 0):
        return None

    # Stop: distal edge +/- SL_ATR_BUFFER * ATR at signal bar
    atr_now = float(atr.iloc[idx])
    if pd.isna(atr_now) or atr_now <= 0:
        return None

    if zone["direction"] == "demand":
        stop_loss = zone["distal"] - config.SL_ATR_BUFFER * atr_now
        risk = entry_price - stop_loss
        if risk <= 0:
            return None
        take_profit = entry_price + config.RR_TARGET * risk
        side = "long"
    else:
        stop_loss = zone["distal"] + config.SL_ATR_BUFFER * atr_now
        risk = stop_loss - entry_price
        if risk <= 0:
            return None
        take_profit = entry_price - config.RR_TARGET * risk
        side = "short"

    return {
        "ticker": ticker,
        "signal_date": df.index[idx],
        "signal_idx": idx,
        "entry_date": entry_date,
        "entry_idx": entry_idx,
        "entry_price": entry_price,
        "direction": side,
        "zone_type": zone["zone_type"],
        "zone_proximal": zone["proximal"],
        "zone_distal": zone["distal"],
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "atr": atr_now,
        "priority": zone["priority"],
        "confirmation_candle": pattern,
        "htf_aligned": True,
        "earnings_days_away": earnings_days_away if earnings_days_away is not None else -1,
        "zone_formation_idx": zone["formation_idx"],
    }
