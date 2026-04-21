"""Backtest engine for the Supply & Demand zone strategy.

Chronological bar-by-bar loop:
  1. Update zone statuses (age, tappable, freshness).
  2. If in a trade: check exits (stop → tp → earnings → time).
  3. Else: check each active+tappable zone for a new entry signal.
  4. If signal generated, open trade at next bar's open.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

import config  # runtime access for TIMEFRAME-sensitive caps
from config import (
    ACCOUNT_SIZE,
    RISK_PCT,
    EARNINGS_EXIT_DAYS,
)
from zones import detect_all_zones, update_zone_status
from zone_signals import generate_entry_signal, check_zone_touch


def _compute_trade_exit(
    df: pd.DataFrame,
    entry_idx: int,
    trade: dict,
    earnings_dates: list[datetime],
) -> dict:
    """Walk forward from entry bar until an exit condition triggers.

    Exit priority (conservative): stop loss → take profit → earnings proximity
    → max duration. If SL and TP are both touched on the same bar, SL wins.
    """
    direction = trade["direction"]
    stop = trade["stop_loss"]
    target = trade["take_profit"]
    n = len(df)
    entry_date = df.index[entry_idx]
    entry_dt = (
        entry_date.to_pydatetime() if hasattr(entry_date, "to_pydatetime") else entry_date
    )

    # Precompute next earnings relative to entry
    next_earn_dt: Optional[datetime] = None
    for d in earnings_dates:
        if d.date() >= entry_dt.date():
            next_earn_dt = d
            break

    max_hold_bars = config.get_max_trade_period()

    for i in range(entry_idx + 1, n):
        row_high = float(df["High"].iloc[i])
        row_low = float(df["Low"].iloc[i])
        row_close = float(df["Close"].iloc[i])
        date = df.index[i]
        bars_in = i - entry_idx

        # 1) Stop loss
        if direction == "long" and row_low <= stop:
            return {"exit_idx": i, "exit_date": date, "exit_price": stop, "exit_reason": "stop_loss"}
        if direction == "short" and row_high >= stop:
            return {"exit_idx": i, "exit_date": date, "exit_price": stop, "exit_reason": "stop_loss"}

        # 2) Take profit
        if direction == "long" and row_high >= target:
            return {"exit_idx": i, "exit_date": date, "exit_price": target, "exit_reason": "take_profit"}
        if direction == "short" and row_low <= target:
            return {"exit_idx": i, "exit_date": date, "exit_price": target, "exit_reason": "take_profit"}

        # 3) Earnings approaching within EARNINGS_EXIT_DAYS (calendar days, tf-agnostic)
        if next_earn_dt is not None:
            date_dt = date.to_pydatetime() if hasattr(date, "to_pydatetime") else date
            days_to_earn = (next_earn_dt.date() - date_dt.date()).days
            if 0 <= days_to_earn <= EARNINGS_EXIT_DAYS:
                return {
                    "exit_idx": i,
                    "exit_date": date,
                    "exit_price": row_close,
                    "exit_reason": "earnings",
                }

        # 4) Max duration (bars — 1 bar = 1 day on daily, 1 hour on 1h)
        if bars_in >= max_hold_bars:
            return {
                "exit_idx": i,
                "exit_date": date,
                "exit_price": row_close,
                "exit_reason": "time_exit",
            }

    # End of data — force close at last bar
    last = n - 1
    return {
        "exit_idx": last,
        "exit_date": df.index[last],
        "exit_price": float(df["Close"].iloc[last]),
        "exit_reason": "end_of_data",
    }


def run_backtest(
    ticker: str,
    df: pd.DataFrame,
    atr: pd.Series,
    earnings_dates: list[datetime],
) -> list[dict]:
    """Run the full S&D backtest for one ticker. Returns list of trade dicts."""

    zones = detect_all_zones(df, atr)
    trades: list[dict] = []

    in_trade = False
    current_exit_idx = -1
    n = len(df)

    for i in range(n - 1):  # we need i+1 to exist for entry
        # Skip bars while a trade is active (prevent overlapping entries on same ticker)
        if in_trade and i <= current_exit_idx:
            continue
        if in_trade and i > current_exit_idx:
            in_trade = False

        # Update all zone statuses using only information up to bar i
        update_zone_status(zones, df, i)

        # Increment test_count if any tappable zone is touched on this bar,
        # regardless of whether we take a trade
        for z in zones:
            if not z["active"] or not z["tappable"]:
                continue
            if z["formation_idx"] > i:
                continue
            if check_zone_touch(df, i, z):
                z["test_count"] += 1

        # Look for an entry signal on any zone that was tappable BEFORE this bar
        # (touch already incremented; we check zones whose test_count == 1 after this bar).
        # To keep it clean: try every active+tappable zone; generate_entry_signal will
        # re-verify touch and all other gates.
        for z in zones:
            if not z["active"] or not z["tappable"]:
                continue
            if z["formation_idx"] > i:
                continue
            # Only allow entry on the first test of the zone
            if z["test_count"] > 1:
                continue
            signal = generate_entry_signal(
                df, i, z, earnings_dates, atr, ticker, in_trade_on_ticker=in_trade
            )
            if signal is None:
                continue

            # Open the trade
            risk_dollars = ACCOUNT_SIZE * RISK_PCT
            risk_per_share = abs(signal["entry_price"] - signal["stop_loss"])
            if risk_per_share <= 0:
                continue
            shares = int(risk_dollars / risk_per_share)
            if shares <= 0:
                continue

            entry_idx = signal["entry_idx"]
            exit_info = _compute_trade_exit(df, entry_idx, signal, earnings_dates)

            entry_price = signal["entry_price"]
            exit_price = exit_info["exit_price"]
            if signal["direction"] == "long":
                gain_per_share = exit_price - entry_price
            else:
                gain_per_share = entry_price - exit_price
            gl_dollars = gain_per_share * shares
            gl_pct = gain_per_share / entry_price if entry_price > 0 else 0.0
            actual_rr = gl_dollars / risk_dollars if risk_dollars > 0 else 0.0
            entry_date = signal["entry_date"]
            exit_date = exit_info["exit_date"]
            duration_days = (
                exit_date - entry_date
            ).days if hasattr(exit_date - entry_date, "days") else int(
                (exit_date - entry_date) / pd.Timedelta(days=1)
            )
            duration_bars = exit_info["exit_idx"] - entry_idx
            zone_age = entry_idx - signal["zone_formation_idx"]

            trades.append(
                {
                    "ticker": ticker,
                    "direction": signal["direction"],
                    "zone_type": signal["zone_type"],
                    "priority": signal["priority"],
                    "confirmation_candle": signal["confirmation_candle"],
                    "signal_date": signal["signal_date"],
                    "entry_date": entry_date,
                    "entry_price": round(entry_price, 2),
                    "stop_loss": round(signal["stop_loss"], 2),
                    "take_profit": round(signal["take_profit"], 2),
                    "exit_date": exit_date,
                    "exit_price": round(exit_price, 2),
                    "exit_reason": exit_info["exit_reason"],
                    "gain_loss_dollars": round(gl_dollars, 2),
                    "gain_loss_pct": round(gl_pct, 4),
                    "trade_rr": round(actual_rr, 3),
                    "trade_duration": duration_days,
                    "trade_duration_bars": duration_bars,
                    "win_loss": "Win" if gl_dollars > 0 else "Loss",
                    "risk_dollars": round(risk_dollars, 2),
                    "shares": shares,
                    "atr_at_entry": round(signal["atr"], 4),
                    "htf_aligned": signal["htf_aligned"],
                    "zone_age_at_entry": zone_age,
                    "zone_proximal": round(signal["zone_proximal"], 2),
                    "zone_distal": round(signal["zone_distal"], 2),
                    "earnings_days_away": signal["earnings_days_away"],
                }
            )

            # Lock out further entries until this trade exits
            in_trade = True
            current_exit_idx = exit_info["exit_idx"]
            # Mark this zone as retired — it's been traded
            z["active"] = False
            break  # don't consider other zones this bar

    return trades
