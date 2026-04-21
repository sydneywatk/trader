"""Trade execution logic — Steps 2-5 of the SID Method."""

import math
from datetime import datetime
from typing import Optional

import pandas as pd

from config import (
    ACCOUNT_SIZE, RISK_PCT, RSI_EXIT, RSI_OVERSOLD, RSI_OVERBOUGHT,
    EARNINGS_MIN_DAYS, WEEKLY_RSI_MIN_DELTA, MAX_TRADE_DAYS,
)
from shared.earnings import earnings_safe, next_earnings_date, last_trading_day_before_earnings


def _get_weekly_rsi_on_date(weekly_df: pd.DataFrame, date: pd.Timestamp) -> tuple[float, float]:
    """Return (current_week_rsi, prev_week_rsi) for the given date."""
    # Find the weekly bar that contains this date
    mask = weekly_df.index <= date
    if mask.sum() < 2:
        return float("nan"), float("nan")
    curr_rsi = weekly_df.loc[mask, "RSI"].iloc[-1]
    prev_rsi = weekly_df.loc[mask, "RSI"].iloc[-2]
    return curr_rsi, prev_rsi


def _check_spy_alignment(spy_daily: pd.DataFrame, date: pd.Timestamp,
                         signal_type: str) -> bool:
    """Check if trade direction aligns with SPY's short-term trend.

    For Long (OS): SPY RSI must be RISING AND SPY must be above 50-day SMA
    For Short (OB): SPY RSI must be FALLING AND SPY must be below or within
                    2% of its 50-day SMA
    """
    if spy_daily is None or spy_daily.empty:
        return True  # No SPY data — skip filter

    mask = spy_daily.index <= date
    if mask.sum() < 2:
        return True

    spy_row = spy_daily.loc[mask].iloc[-1]
    spy_prev = spy_daily.loc[mask].iloc[-2]

    spy_rsi = spy_row["RSI"]
    spy_rsi_prev = spy_prev["RSI"]
    spy_close = spy_row["Close"]
    spy_sma50 = spy_row["SMA50"]

    if pd.isna(spy_rsi) or pd.isna(spy_rsi_prev):
        return True

    if signal_type == "OS":
        # Long: SPY RSI rising AND SPY above SMA50
        rsi_rising = spy_rsi > spy_rsi_prev
        above_sma = (not pd.isna(spy_sma50)) and spy_close > spy_sma50
        return rsi_rising and above_sma
    else:  # OB
        # Short: SPY RSI falling AND SPY below or within 2% of SMA50
        rsi_falling = spy_rsi < spy_rsi_prev
        near_or_below_sma = (not pd.isna(spy_sma50)) and spy_close < spy_sma50 * 1.02
        return rsi_falling and near_or_below_sma


def _check_entry_conditions(daily_df: pd.DataFrame, weekly_df: pd.DataFrame,
                            idx: int, signal_type: str,
                            earnings_dates: list[datetime],
                            spy_daily: pd.DataFrame = None) -> tuple[bool, bool, Optional[datetime], bool]:
    """Check all entry conditions for a given day index.

    Returns (conditions_met, earnings_available, next_earnings_dt, spy_blocked).
    spy_blocked is True when all other conditions pass but SPY alignment fails.
    """
    row = daily_df.iloc[idx]
    prev_row = daily_df.iloc[idx - 1]
    date = daily_df.index[idx]

    rsi_today = row["RSI"]
    rsi_yesterday = prev_row["RSI"]

    if pd.isna(rsi_today) or pd.isna(rsi_yesterday):
        return False, True, None, False

    # --- Condition D: Gap / RSI 50 check ---
    if signal_type == "OS" and rsi_today >= RSI_EXIT:
        return False, True, None, False  # No room to run (long)
    if signal_type == "OB" and rsi_today <= RSI_EXIT:
        return False, True, None, False  # No room to run (short)

    # --- Condition A: RSI + MACD daily aligned ---
    macd_line = row["MACD"]
    macd_signal = row["MACD_signal"]
    macd_hist = row["MACD_hist"]
    prev_macd_hist = prev_row["MACD_hist"]

    if pd.isna(macd_line) or pd.isna(macd_signal):
        return False, True, None, False

    if signal_type == "OS":
        rsi_rising = rsi_today > rsi_yesterday
        # MACD crossing/pointing up: MACD line above signal line (bullish crossover),
        # OR histogram positive and increasing (momentum building)
        macd_bullish = (macd_line > macd_signal) or (
            not pd.isna(macd_hist) and not pd.isna(prev_macd_hist)
            and macd_hist > 0 and macd_hist > prev_macd_hist
        )
        if not (rsi_rising and macd_bullish):
            return False, True, None, False
    else:  # OB
        rsi_falling = rsi_today < rsi_yesterday
        # MACD crossing/pointing down: MACD line below signal line (bearish crossover),
        # OR histogram negative and decreasing (momentum building)
        macd_bearish = (macd_line < macd_signal) or (
            not pd.isna(macd_hist) and not pd.isna(prev_macd_hist)
            and macd_hist < 0 and macd_hist < prev_macd_hist
        )
        if not (rsi_falling and macd_bearish):
            return False, True, None, False

    # --- Condition B: Weekly RSI aligned (must move >3 points in trade direction) ---
    curr_w_rsi, prev_w_rsi = _get_weekly_rsi_on_date(weekly_df, date)
    if pd.isna(curr_w_rsi) or pd.isna(prev_w_rsi):
        return False, True, None, False

    weekly_rsi_delta = curr_w_rsi - prev_w_rsi
    if signal_type == "OS" and weekly_rsi_delta <= WEEKLY_RSI_MIN_DELTA:
        return False, True, None, False
    if signal_type == "OB" and weekly_rsi_delta >= -WEEKLY_RSI_MIN_DELTA:
        return False, True, None, False

    # --- Condition C: Earnings check ---
    earnings_available = len(earnings_dates) > 0
    if earnings_available:
        is_safe, nxt = earnings_safe(earnings_dates, date.to_pydatetime())
        if not is_safe:
            return False, True, nxt, False
    else:
        nxt = None

    # --- Condition E: SPY market alignment ---
    if not _check_spy_alignment(spy_daily, date, signal_type):
        has_earn = earnings_available
        return False, has_earn, nxt, True  # All other conditions met, SPY blocked

    if earnings_available:
        return True, True, nxt, False
    else:
        return True, False, None, False


def _calc_stop_loss(daily_df: pd.DataFrame, signal_idx: int, entry_idx: int,
                    signal_type: str) -> float:
    """Calculate stop loss from prices between signal date and entry date (inclusive)."""
    window = daily_df.iloc[signal_idx:entry_idx + 1]

    if signal_type == "OS":
        lowest_low = window["Low"].min()
        # Round DOWN to nearest whole number; if already whole, go one below
        if lowest_low == math.floor(lowest_low):
            return math.floor(lowest_low) - 1.0
        return math.floor(lowest_low)
    else:  # OB
        highest_high = window["High"].max()
        # Round UP to nearest whole number; if already whole, go one above
        if highest_high == math.ceil(highest_high):
            return math.ceil(highest_high) + 1.0
        return math.ceil(highest_high)


def _find_exit(daily_df: pd.DataFrame, entry_idx: int, signal_type: str,
               stop_loss: float, earnings_dates: list[datetime],
               has_earnings_data: bool) -> dict:
    """Find exit point after entry. Returns exit info dict."""
    entry_price = daily_df.iloc[entry_idx]["Close"]
    entry_date = daily_df.index[entry_idx]

    # Determine next earnings date for earnings exit
    nxt_earn = None
    if has_earnings_data:
        nxt_earn = next_earnings_date(earnings_dates, entry_date.to_pydatetime())
    earn_exit_date = None
    if nxt_earn is not None:
        earn_exit_date = last_trading_day_before_earnings(daily_df, nxt_earn)

    for i in range(entry_idx + 1, len(daily_df)):
        row = daily_df.iloc[i]
        date = daily_df.index[i]
        rsi_today = row["RSI"]

        if pd.isna(rsi_today):
            continue

        # --- Priority 1: Stop loss ---
        if signal_type == "OS" and row["Low"] <= stop_loss:
            return {
                "exit_date": date,
                "exit_price": stop_loss,
                "exit_reason": "Stop loss",
            }
        if signal_type == "OB" and row["High"] >= stop_loss:
            return {
                "exit_date": date,
                "exit_price": stop_loss,
                "exit_reason": "Stop loss",
            }

        # --- Priority 2: Take profit (RSI reaches 50) ---
        if signal_type == "OS" and rsi_today >= RSI_EXIT:
            return {
                "exit_date": date,
                "exit_price": row["Close"],
                "exit_reason": "RSI reached 50",
            }
        if signal_type == "OB" and rsi_today <= RSI_EXIT:
            return {
                "exit_date": date,
                "exit_price": row["Close"],
                "exit_reason": "RSI reached 50",
            }

        # --- Priority 3: Time exit (10 trading days) ---
        trading_days_in = i - entry_idx
        if trading_days_in >= MAX_TRADE_DAYS:
            return {
                "exit_date": date,
                "exit_price": row["Close"],
                "exit_reason": "Time exit - 10 days",
            }

        # --- Priority 4: Earnings exit ---
        if earn_exit_date is not None and date >= earn_exit_date:
            return {
                "exit_date": earn_exit_date,
                "exit_price": daily_df.loc[earn_exit_date, "Close"],
                "exit_reason": "Earnings approaching",
            }

        # Note: The 2-day RSI reversal rule is intentionally disabled.
        # In backtesting with daily closes, the mechanical 2-day rule
        # catches normal RSI oscillations (not genuine reversals),
        # exiting ~48% of trades at 21% WR. The stop loss provides
        # adequate downside protection. In live trading, the reversal
        # check uses intraday judgment that daily data cannot replicate.

    # If we reach the end of data without exit, close at last available price
    last = daily_df.iloc[-1]
    return {
        "exit_date": daily_df.index[-1],
        "exit_price": last["Close"],
        "exit_reason": "End of data",
    }


def run_backtest_for_ticker(ticker: str, daily_df: pd.DataFrame,
                            weekly_df: pd.DataFrame,
                            signals: list[dict],
                            earnings_dates: list[datetime],
                            spy_daily: pd.DataFrame = None) -> tuple[list[dict], list[dict]]:
    """Run the full SID backtest for one ticker.

    Returns (trades, skipped) where each is a list of dicts.
    """
    trades = []
    skipped = []
    in_trade = False
    current_trade_exit_idx = -1

    for signal in signals:
        signal_date = signal["date"]
        signal_type = signal["type"]

        # Find index of signal date in daily_df
        try:
            signal_idx = daily_df.index.get_loc(signal_date)
        except KeyError:
            continue

        # If overlapping with an active trade, skip
        if in_trade and signal_idx <= current_trade_exit_idx:
            continue

        in_trade = False

        # --- Step 2: Find entry day ---
        entry_idx = None
        entry_has_earnings = True
        entry_next_earnings = None
        spy_blocked_signal = False

        for i in range(signal_idx + 1, len(daily_df)):
            conditions_met, has_earn, nxt_earn, spy_blocked = _check_entry_conditions(
                daily_df, weekly_df, i, signal_type, earnings_dates, spy_daily
            )

            if spy_blocked:
                spy_blocked_signal = True

            # Check if RSI has already recovered past exit threshold before entry
            rsi_val = daily_df.iloc[i]["RSI"]
            if not pd.isna(rsi_val):
                if signal_type == "OS" and rsi_val >= RSI_EXIT:
                    # RSI gapped/recovered to 50+ without valid entry — skip as gap
                    reason = "Signal found, skipped — gap to RSI 50"
                    if spy_blocked_signal:
                        reason = "Skipped — SPY misalignment"
                    skipped.append({
                        "ticker": ticker,
                        "signal_date": signal_date,
                        "signal_type": signal_type,
                        "reason": reason,
                    })
                    break
                if signal_type == "OB" and rsi_val <= RSI_EXIT:
                    reason = "Signal found, skipped — gap to RSI 50"
                    if spy_blocked_signal:
                        reason = "Skipped — SPY misalignment"
                    skipped.append({
                        "ticker": ticker,
                        "signal_date": signal_date,
                        "signal_type": signal_type,
                        "reason": reason,
                    })
                    break

            if conditions_met:
                entry_idx = i
                entry_has_earnings = has_earn
                entry_next_earnings = nxt_earn
                break

            # Also check if a new opposite signal fires (RSI recovered) — abort search
            if not pd.isna(rsi_val):
                if signal_type == "OS" and rsi_val >= RSI_OVERBOUGHT:
                    if spy_blocked_signal:
                        skipped.append({
                            "ticker": ticker,
                            "signal_date": signal_date,
                            "signal_type": signal_type,
                            "reason": "Skipped — SPY misalignment",
                        })
                    break
                if signal_type == "OB" and rsi_val <= RSI_OVERSOLD:
                    if spy_blocked_signal:
                        skipped.append({
                            "ticker": ticker,
                            "signal_date": signal_date,
                            "signal_type": signal_type,
                            "reason": "Skipped — SPY misalignment",
                        })
                    break

        if entry_idx is None:
            continue

        # Capture signal RSI for tier scoring
        signal_rsi = signal["rsi"]

        # --- Step 3: Stop loss ---
        stop_loss = _calc_stop_loss(daily_df, signal_idx, entry_idx, signal_type)

        entry_price = daily_df.iloc[entry_idx]["Close"]
        entry_date = daily_df.index[entry_idx]

        # Risk per share
        risk_per_share = abs(entry_price - stop_loss)
        if risk_per_share <= 0:
            continue  # Invalid stop loss

        # --- Step 4: Find exit ---
        exit_info = _find_exit(daily_df, entry_idx, signal_type, stop_loss,
                               earnings_dates, entry_has_earnings)

        exit_date = exit_info["exit_date"]
        exit_price = exit_info["exit_price"]

        # Mark trade range to prevent overlapping signals
        try:
            current_trade_exit_idx = daily_df.index.get_loc(exit_date)
        except KeyError:
            current_trade_exit_idx = entry_idx
        in_trade = True

        # --- Step 5: Risk calculations ---
        risk_per_position = ACCOUNT_SIZE * RISK_PCT
        max_shares = math.floor(risk_per_position / risk_per_share)
        if max_shares <= 0:
            continue

        position_size = max_shares * entry_price
        pct_risk_per_share = risk_per_share / entry_price

        if signal_type == "OS":
            gain_per_share = exit_price - entry_price
        else:
            gain_per_share = entry_price - exit_price

        total_profit = gain_per_share * max_shares
        pct_return = total_profit / position_size if position_size > 0 else 0
        trade_rr = total_profit / risk_per_position if risk_per_position > 0 else 0
        win_loss = "Win" if total_profit > 0 else "Loss"
        duration = (exit_date - entry_date).days

        # Weekly RSI direction for notes
        curr_w, prev_w = _get_weekly_rsi_on_date(weekly_df, entry_date)
        weekly_dir = "up" if (not pd.isna(curr_w) and not pd.isna(prev_w) and curr_w > prev_w) else "down"
        weekly_delta = abs(curr_w - prev_w) if (not pd.isna(curr_w) and not pd.isna(prev_w)) else 0

        # MACD cross detection (informational only — not used as filter)
        entry_row = daily_df.iloc[entry_idx]
        prev_entry_row = daily_df.iloc[entry_idx - 1] if entry_idx > 0 else entry_row
        macd_l = entry_row["MACD"]
        macd_s = entry_row["MACD_signal"]
        prev_macd_l = prev_entry_row["MACD"]
        prev_macd_s = prev_entry_row["MACD_signal"]
        if signal_type == "OS":
            macd_crossed = (not pd.isna(prev_macd_l) and not pd.isna(prev_macd_s)
                            and prev_macd_l <= prev_macd_s and macd_l > macd_s)
        else:
            macd_crossed = (not pd.isna(prev_macd_l) and not pd.isna(prev_macd_s)
                            and prev_macd_l >= prev_macd_s and macd_l < macd_s)

        # Earnings info for notes
        if entry_next_earnings is not None:
            earn_str = entry_next_earnings.strftime("%m/%d/%Y")
        elif not entry_has_earnings:
            earn_str = "N/A (no data)"
        else:
            earn_str = "N/A"

        # Earnings days away for tier scoring
        if entry_next_earnings is not None:
            earnings_days_away = (entry_next_earnings.date() - entry_date.to_pydatetime().date()).days
        else:
            earnings_days_away = None

        notes = (
            f"RSI {'OS' if signal_type == 'OS' else 'OB'} {signal_date.strftime('%m/%d/%Y')}. "
            f"Aligned {entry_date.strftime('%m/%d/%Y')}. "
            f"Earnings {earn_str}. "
            f"Weekly RSI {weekly_dir} ({weekly_delta:.1f}pts)."
        )
        if macd_crossed:
            notes += " MACD cross."
        if not entry_has_earnings:
            notes += " [Earnings data unavailable]"
        if exit_info["exit_reason"] != "RSI reached 50":
            notes += f" Exit: {exit_info['exit_reason']}."

        trades.append({
            "ticker": ticker,
            "order": "Long" if signal_type == "OS" else "Short",
            "account_value": ACCOUNT_SIZE,
            "risk_pct": RISK_PCT,
            "risk_per_position": risk_per_position,
            "entry_price": round(entry_price, 2),
            "stop_loss": round(stop_loss, 2),
            "risk_per_share": round(risk_per_share, 2),
            "pct_risk_per_share": pct_risk_per_share,
            "position_size": round(position_size, 2),
            "max_shares": max_shares,
            "signal_date": signal_date,
            "entry_date": entry_date,
            "exit_date": exit_date,
            "exit_price": round(exit_price, 2),
            "gain_per_share": round(gain_per_share, 2),
            "total_profit": round(total_profit, 2),
            "trade_rr": round(trade_rr, 4),
            "pct_return": pct_return,
            "win_loss": win_loss,
            "duration": duration,
            "notes": notes,
            # Tier scoring fields
            "signal_rsi": round(signal_rsi, 2),
            "weekly_rsi_delta": round(weekly_delta, 2),
            "macd_crossed": macd_crossed,
            "earnings_days_away": earnings_days_away,
        })

    return trades, skipped
