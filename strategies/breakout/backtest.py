"""Breakout v1 backtester — SPEC §4.8–4.11.

Chronological bar loop. On each bar:
  1. Update open positions -> check exits in priority order.
  2. Look at today's new signals; apply concurrent-position cap; enter at next open.

Exit priority (SPEC §4.11 baseline = partial_trail):
  1. Hard stop (intrabar low <= stop_price).
  2. Partial at +1R (intrabar high >= entry + R). Half sold; remainder trails.
  3. After partial: close < sma_10 triggers full exit of remainder at next open.
  4. Earnings within EARNINGS_EXIT_DAYS -> exit_all at close.
  5. days_in_trade >= TIME_STOP_DAYS -> exit_all at close.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

from config import (
    ACCOUNT_SIZE, RISK_PCT,
    STOP_PCT_NORMAL, STOP_PCT_WEAK_TAPE,
    PARTIAL_R, TRAIL_MA_LEN, FIXED_TARGET_R,
    TIME_STOP_DAYS, EARNINGS_EXIT_DAYS,
    MAX_CONCURRENT_POSITIONS,
)
from shared.earnings import next_earnings_date
from signals import Signal


@dataclass
class Position:
    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    initial_shares: int
    shares: int                # shares currently held
    r_per_share: float         # entry - stop (dollar R)
    partial_taken: bool = False
    partial_pnl: float = 0.0
    rs_rank_at_entry: float = float("nan")


@dataclass
class Trade:
    ticker: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    initial_shares: int
    partial_date: Optional[pd.Timestamp]
    partial_price: Optional[float]
    partial_pnl: float
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    final_pnl: float
    total_pnl: float
    trade_rr: float
    duration_days: int
    win_loss: str
    rs_rank_at_entry: float


def _compute_stop_pct(spy_row: pd.Series) -> float:
    """Tight stop in weak tape (SPY close < SPY SMA50); else normal."""
    c, s50 = spy_row["Close"], spy_row["spy_sma_50"]
    if not pd.isna(s50) and c < s50:
        return STOP_PCT_WEAK_TAPE
    return STOP_PCT_NORMAL


def _size_position(entry_price: float, stop_price: float) -> int:
    risk_dollars = ACCOUNT_SIZE * RISK_PCT
    r_per_share = entry_price - stop_price
    if r_per_share <= 0:
        return 0
    return int(risk_dollars // r_per_share)


def _next_trading_day(df: pd.DataFrame, date: pd.Timestamp) -> Optional[pd.Timestamp]:
    idx = df.index
    pos = idx.searchsorted(date, side="right")
    if pos >= len(idx):
        return None
    return idx[pos]


def run_backtest(signals: list[Signal],
                 prices: dict[str, pd.DataFrame],
                 spy: pd.DataFrame,
                 earnings_map: dict[str, list[datetime]],
                 window_start: pd.Timestamp,
                 window_end: pd.Timestamp,
                 max_concurrent: int | None = MAX_CONCURRENT_POSITIONS,
                 exit_mode: str = "partial_trail",
                 partial_r: float = PARTIAL_R,
                 trail_ma_len: int = TRAIL_MA_LEN,
                 target_r: float = FIXED_TARGET_R) -> tuple[list[Trade], list[dict]]:
    """Backtest with configurable concurrent cap + exit logic.

    exit_mode:
      "partial_trail" — sell half at +partial_r R, trail remainder on SMA(trail_ma_len).
      "fixed"         — exit full position at +target_r R.

    max_concurrent = None means unlimited. Returns (trades, cap_skips).
    """
    trail_col = f"sma_{trail_ma_len}"
    # Group signals by signal_date for deterministic daily processing.
    by_date: dict[pd.Timestamp, list[Signal]] = {}
    for s in signals:
        by_date.setdefault(s.signal_date, []).append(s)

    # Master calendar: union of all ticker indices in [window_start, window_end].
    all_dates = pd.DatetimeIndex([])
    for df in prices.values():
        mask = (df.index >= window_start) & (df.index <= window_end)
        all_dates = all_dates.union(df.index[mask])
    all_dates = all_dates.sort_values()

    trades: list[Trade] = []
    cap_skips: list[dict] = []
    open_positions: list[Position] = []

    for d_idx, date in enumerate(all_dates):
        # ---- 1) update open positions: evaluate exits ----
        still_open: list[Position] = []
        for p in open_positions:
            df = prices[p.ticker]
            if date not in df.index:
                still_open.append(p)
                continue
            row = df.loc[date]
            high, low, close = row["High"], row["Low"], row["Close"]

            # 1a) Hard stop (applies in both exit modes)
            if low <= p.stop_price:
                exit_price = p.stop_price
                pnl = p.shares * (exit_price - p.entry_price)
                total = p.partial_pnl + pnl
                trades.append(_make_trade(p, date, exit_price, "stop",
                                          pnl, total,
                                          partial_date=getattr(p, "partial_date", None),
                                          partial_price=getattr(p, "partial_price", None)))
                continue

            if exit_mode == "fixed":
                # 1b-fixed) Target hit -> exit full at target.
                target_price = p.entry_price + target_r * p.r_per_share
                if high >= target_price:
                    exit_price = target_price
                    pnl = p.shares * (exit_price - p.entry_price)
                    trades.append(_make_trade(p, date, exit_price, "target",
                                              pnl, pnl))
                    continue
            else:
                # 1b-partial) Partial at +partial_r R (only if not yet taken)
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
                    # After partial, remainder continues. Fall through to trail logic.

                # 1c-partial) Trail: after partial, exit remainder when close < sma_N
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
                        trades.append(_make_trade(p, exit_date, exit_price, "trail",
                                                  pnl, p.partial_pnl + pnl,
                                                  partial_date=getattr(p, "partial_date", None),
                                                  partial_price=getattr(p, "partial_price", None)))
                        continue

            # 1d) Earnings blackout exit
            earn = earnings_map.get(p.ticker, [])
            nxt_e = next_earnings_date(earn, date.to_pydatetime()) if earn else None
            if nxt_e is not None:
                days_to = (nxt_e.date() - date.date()).days
                if 0 <= days_to <= EARNINGS_EXIT_DAYS:
                    exit_price = close
                    pnl = p.shares * (exit_price - p.entry_price)
                    trades.append(_make_trade(p, date, exit_price, "earnings_exit",
                                              pnl, p.partial_pnl + pnl,
                                              partial_date=getattr(p, "partial_date", None),
                                              partial_price=getattr(p, "partial_price", None)))
                    continue

            # 1e) Time stop
            days_in = (date - p.entry_date).days
            if days_in >= TIME_STOP_DAYS:
                exit_price = close
                pnl = p.shares * (exit_price - p.entry_price)
                trades.append(_make_trade(p, date, exit_price, "time_stop",
                                          pnl, p.partial_pnl + pnl,
                                          partial_date=getattr(p, "partial_date", None),
                                          partial_price=getattr(p, "partial_price", None)))
                continue

            still_open.append(p)
        open_positions = still_open

        # ---- 2) process today's signals for entries ----
        today_signals = by_date.get(date, [])
        if not today_signals:
            continue

        # SPY regime-based stop selection for today
        spy_row = spy.loc[date] if date in spy.index else None
        stop_pct = _compute_stop_pct(spy_row) if spy_row is not None else STOP_PCT_NORMAL

        # Rank today's candidates by RS (desc) for deterministic tie-break under cap.
        today_signals.sort(key=lambda s: (-s.rs_63_rank if not pd.isna(s.rs_63_rank) else 0, s.ticker))

        cap = max_concurrent if max_concurrent is not None else float("inf")
        for sig in today_signals:
            if len(open_positions) >= cap:
                cap_skips.append({
                    "date": date, "ticker": sig.ticker,
                    "filter_name": "concurrent_cap",
                    "setup_close": sig.close, "pivot": sig.pivot,
                })
                continue

            df = prices[sig.ticker]
            nxt = _next_trading_day(df, date)
            if nxt is None:
                cap_skips.append({
                    "date": date, "ticker": sig.ticker,
                    "filter_name": "no_next_day_bar",
                    "setup_close": sig.close, "pivot": sig.pivot,
                })
                continue

            entry_price = float(df.loc[nxt, "Open"])
            stop_price = entry_price * (1 - stop_pct)
            r_per_share = entry_price - stop_price
            shares = _size_position(entry_price, stop_price)
            if shares <= 0:
                cap_skips.append({
                    "date": date, "ticker": sig.ticker,
                    "filter_name": "zero_size",
                    "setup_close": sig.close, "pivot": sig.pivot,
                })
                continue

            open_positions.append(Position(
                ticker=sig.ticker,
                signal_date=date,
                entry_date=nxt,
                entry_price=entry_price,
                stop_price=stop_price,
                initial_shares=shares,
                shares=shares,
                r_per_share=r_per_share,
                rs_rank_at_entry=sig.rs_63_rank,
            ))

    # Close any positions still open at window end -- honest mark-to-last-bar exit.
    for p in open_positions:
        df = prices[p.ticker]
        mask = df.index <= window_end
        if not mask.any():
            continue
        last_row = df[mask].iloc[-1]
        last_date = df[mask].index[-1]
        exit_price = float(last_row["Close"])
        pnl = p.shares * (exit_price - p.entry_price)
        trades.append(_make_trade(p, last_date, exit_price, "window_end",
                                  pnl, p.partial_pnl + pnl,
                                  partial_date=getattr(p, "partial_date", None),
                                  partial_price=getattr(p, "partial_price", None)))

    return trades, cap_skips


def _make_trade(p: Position, exit_date: pd.Timestamp, exit_price: float,
                reason: str, final_pnl: float, total_pnl: float,
                partial_date: Optional[pd.Timestamp] = None,
                partial_price: Optional[float] = None) -> Trade:
    r_dollars = ACCOUNT_SIZE * RISK_PCT  # 1R in $ for the trade (account-level)
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
