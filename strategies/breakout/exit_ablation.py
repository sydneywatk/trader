"""Breakout v1 exit ablation — SPEC §4.12.

Replays identical entry list through a 2R fixed-target exit rule. Shares the
entry/sizing logic from the baseline run.

Exit priority (fixed_2r):
  1. Hard stop (intrabar low <= stop).
  2. Fixed target: high >= entry + 2R -> sell full at target.
  3. Earnings within EARNINGS_EXIT_DAYS -> exit at close.
  4. Time stop (days_in_trade >= TIME_STOP_DAYS) -> exit at close.
"""

from datetime import datetime
from typing import Optional

import pandas as pd

from config import (
    ACCOUNT_SIZE, RISK_PCT,
    FIXED_TARGET_R, PARTIAL_R, TRAIL_MA_LEN,
    TIME_STOP_DAYS, EARNINGS_EXIT_DAYS,
)
from shared.earnings import next_earnings_date
from backtest import Trade, Position, _next_trading_day


def run_ablation(entries: list[Position],
                 prices: dict[str, pd.DataFrame],
                 earnings_map: dict[str, list[datetime]],
                 window_end: pd.Timestamp,
                 exit_mode: str = "fixed",
                 target_r: float = FIXED_TARGET_R,
                 partial_r: float = PARTIAL_R,
                 trail_ma_len: int = TRAIL_MA_LEN) -> list[Trade]:
    """Replay the given entries with configurable exit logic.

    exit_mode:
      "fixed"         — exit full position at entry + target_r * R.
      "partial_trail" — sell half at entry + partial_r * R, trail remainder on
                        SMA(trail_ma_len). Hard stop, earnings exit, and time
                        stop still apply in both modes.
    """
    trades: list[Trade] = []
    trail_col = f"sma_{trail_ma_len}"

    for p0 in entries:
        p = Position(
            ticker=p0.ticker,
            signal_date=p0.signal_date,
            entry_date=p0.entry_date,
            entry_price=p0.entry_price,
            stop_price=p0.stop_price,
            initial_shares=p0.initial_shares,
            shares=p0.initial_shares,
            r_per_share=p0.r_per_share,
            rs_rank_at_entry=p0.rs_rank_at_entry,
        )

        df = prices[p.ticker]
        mask = (df.index >= p.entry_date) & (df.index <= window_end)
        sub = df[mask]
        if sub.empty:
            continue

        earn = earnings_map.get(p.ticker, [])
        resolved = False

        for date, row in sub.iterrows():
            high, low, close = row["High"], row["Low"], row["Close"]
            days_in = (date - p.entry_date).days

            if low <= p.stop_price:
                exit_price = p.stop_price
                pnl = p.shares * (exit_price - p.entry_price)
                trades.append(_finalize(p, date, exit_price, "stop", pnl,
                                        p.partial_pnl + pnl))
                resolved = True
                break

            if exit_mode == "fixed":
                target_price = p.entry_price + target_r * p.r_per_share
                if high >= target_price:
                    exit_price = target_price
                    pnl = p.shares * (exit_price - p.entry_price)
                    trades.append(_finalize(p, date, exit_price, "target", pnl, pnl))
                    resolved = True
                    break
            else:  # partial_trail
                partial_target = p.entry_price + partial_r * p.r_per_share
                if not p.partial_taken and high >= partial_target:
                    half = p.shares // 2
                    if half > 0:
                        ppnl = half * (partial_target - p.entry_price)
                        p.partial_pnl += ppnl
                        p.shares -= half
                        p.partial_taken = True
                        p.partial_date = date
                        p.partial_price = partial_target
                if p.partial_taken:
                    sma_n = row.get(trail_col, float("nan"))
                    if not pd.isna(sma_n) and close < sma_n:
                        nxt = _next_trading_day(df, date)
                        if nxt is not None and nxt in df.index:
                            exit_price = float(df.loc[nxt, "Open"])
                            exit_date = nxt
                        else:
                            exit_price = close
                            exit_date = date
                        pnl = p.shares * (exit_price - p.entry_price)
                        trades.append(_finalize(p, exit_date, exit_price, "trail",
                                                pnl, p.partial_pnl + pnl,
                                                partial_date=getattr(p, "partial_date", None),
                                                partial_price=getattr(p, "partial_price", None)))
                        resolved = True
                        break

            if earn:
                nxt_e = next_earnings_date(earn, date.to_pydatetime())
                if nxt_e is not None:
                    d_to_e = (nxt_e.date() - date.date()).days
                    if 0 <= d_to_e <= EARNINGS_EXIT_DAYS:
                        exit_price = close
                        pnl = p.shares * (exit_price - p.entry_price)
                        trades.append(_finalize(p, date, exit_price, "earnings_exit",
                                                pnl, p.partial_pnl + pnl,
                                                partial_date=getattr(p, "partial_date", None),
                                                partial_price=getattr(p, "partial_price", None)))
                        resolved = True
                        break
            if days_in >= TIME_STOP_DAYS:
                exit_price = close
                pnl = p.shares * (exit_price - p.entry_price)
                trades.append(_finalize(p, date, exit_price, "time_stop",
                                        pnl, p.partial_pnl + pnl,
                                        partial_date=getattr(p, "partial_date", None),
                                        partial_price=getattr(p, "partial_price", None)))
                resolved = True
                break

        if not resolved:
            last_row = sub.iloc[-1]
            last_date = sub.index[-1]
            exit_price = float(last_row["Close"])
            pnl = p.shares * (exit_price - p.entry_price)
            trades.append(_finalize(p, last_date, exit_price, "window_end",
                                    pnl, p.partial_pnl + pnl,
                                    partial_date=getattr(p, "partial_date", None),
                                    partial_price=getattr(p, "partial_price", None)))

    return trades


def _finalize(p: Position, exit_date: pd.Timestamp, exit_price: float,
              reason: str, final_pnl: float, total_pnl: float,
              partial_date=None, partial_price=None) -> Trade:
    r_dollars = ACCOUNT_SIZE * RISK_PCT
    trade_rr = total_pnl / r_dollars if r_dollars else 0.0
    duration = (exit_date - p.entry_date).days
    return Trade(
        ticker=p.ticker,
        signal_date=p.signal_date,
        entry_date=p.entry_date,
        entry_price=p.entry_price,
        stop_price=p.stop_price,
        initial_shares=p.initial_shares,
        partial_date=partial_date,
        partial_price=partial_price,
        partial_pnl=p.partial_pnl,
        exit_date=exit_date,
        exit_price=exit_price,
        exit_reason=reason,
        final_pnl=final_pnl,
        total_pnl=total_pnl,
        trade_rr=trade_rr,
        duration_days=duration,
        win_loss="Win" if total_pnl > 0 else ("Loss" if total_pnl < 0 else "Flat"),
        rs_rank_at_entry=p.rs_rank_at_entry,
    )
