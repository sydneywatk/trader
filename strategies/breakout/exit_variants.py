"""Breakout v2 Phase 2 — exit variants replayed on v1 entry list.

All variants share: 7% hard stop until +1R (the "Rubicon"). 60-bar time stop.
Earnings exit when ≤3 days away. Differ in post-Rubicon trailing logic.

E1 — R-ratchet step: at +1R stop→+0.1R, +1.5R→+0.5R, +2R→+1R, then +0.5R
     behind price for every +0.5R advance.
E2 — MFE-giveback: +1R→trail MFE−0.5R; +2R→tighten to MFE−0.3R.
E3 — Chandelier-tight: +1R→trail max_high_since_entry − 1.5*ATR14.
E4 — Partial 1/3 @ +1R + R-ratchet on remaining 2/3.
E5 — D3 control: partial 1/2 @ +2R + SMA20 trail (delegates to existing logic).
"""

import math
from datetime import datetime
from typing import Optional

import pandas as pd

from config import (
    ACCOUNT_SIZE, RISK_PCT,
    TIME_STOP_DAYS, EARNINGS_EXIT_DAYS,
)
from shared.earnings import next_earnings_date
from backtest import Trade, Position, _next_trading_day
from exit_ablation import run_ablation as run_partial_trail  # for E5


# ---------- per-variant stop-level helpers ----------------------------------

def _stop_r_for_e1(high_r: float) -> float | None:
    """E1 R-ratchet. Returns stop level as R above entry; None = keep initial."""
    if high_r < 1.0:
        return None
    if high_r < 1.5:
        return 0.1
    if high_r < 2.0:
        return 0.5
    # For high_r >= 2.0: 0.5R steps. floor(high_r / 0.5) * 0.5 - 1.0
    # high_r=2.0 -> 1.0 ; 2.5 -> 1.5 ; 3.0 -> 2.0 ; 4.0 -> 3.0
    return math.floor(high_r / 0.5) * 0.5 - 1.0


def _stop_r_for_e2(high_r: float) -> float | None:
    """E2 MFE-giveback. Returns stop level as R above entry; None = keep initial."""
    if high_r < 1.0:
        return None
    if high_r < 2.0:
        return high_r - 0.5
    return high_r - 0.3


# ---------- core replay loop -------------------------------------------------

def _finalize(p: Position, exit_date: pd.Timestamp, exit_price: float,
              reason: str, final_pnl: float, total_pnl: float,
              partial_date=None, partial_price=None,
              partial_pnl_val: float = 0.0) -> Trade:
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
        partial_pnl=partial_pnl_val,
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


def _run_one_trade(p0: Position,
                   df: pd.DataFrame,
                   atr: Optional[pd.Series],
                   earn: list[datetime],
                   window_end: pd.Timestamp,
                   variant: str,
                   time_stop: int = TIME_STOP_DAYS,
                   earn_exit_days: int = EARNINGS_EXIT_DAYS) -> Optional[Trade]:
    entry = p0.entry_price
    R = max(p0.r_per_share, 1e-9)
    initial_stop = p0.stop_price
    shares = p0.initial_shares
    stop = initial_stop
    max_high = entry
    partial_pnl = 0.0
    partial_shares = 0
    partial_date = None
    partial_price = None

    mask = (df.index >= p0.entry_date) & (df.index <= window_end)
    sub = df[mask]
    if sub.empty:
        return None

    for _, (date, row) in enumerate(sub.iterrows()):
        high, low, close = row["High"], row["Low"], row["Close"]
        if high > max_high:
            max_high = high
        mfe_r = (max_high - entry) / R

        # 1) hard stop / trailed stop
        if low <= stop:
            exit_price = stop
            pnl = shares * (exit_price - entry)
            return _finalize(p0, date, exit_price, "stop", pnl,
                             partial_pnl + pnl,
                             partial_date, partial_price, partial_pnl)

        # 2) E4: partial 1/3 at +1R
        if variant == "E4" and partial_shares == 0 and high >= entry + 1.0 * R:
            partial_shares = p0.initial_shares // 3
            if partial_shares > 0:
                partial_pnl = partial_shares * 1.0 * R
                shares = p0.initial_shares - partial_shares
                partial_date = date
                partial_price = entry + 1.0 * R

        # 3) update trailed stop per variant (never loosen)
        candidate = None
        if variant in ("E1", "E4"):
            level = _stop_r_for_e1(mfe_r)
            if level is not None:
                candidate = entry + level * R
        elif variant == "E2":
            level = _stop_r_for_e2(mfe_r)
            if level is not None:
                candidate = entry + level * R
        elif variant == "E3":
            if mfe_r >= 1.0 and atr is not None and date in atr.index:
                atr_now = atr.loc[date]
                if not pd.isna(atr_now):
                    candidate = max_high - 1.5 * float(atr_now)

        if candidate is not None and candidate > stop:
            stop = candidate

        # 4) earnings exit
        if earn:
            nxt = next_earnings_date(earn, date.to_pydatetime())
            if nxt is not None:
                days_to = (nxt.date() - date.date()).days
                if 0 <= days_to <= earn_exit_days:
                    exit_price = close
                    pnl = shares * (exit_price - entry)
                    return _finalize(p0, date, exit_price, "earnings_exit", pnl,
                                     partial_pnl + pnl,
                                     partial_date, partial_price, partial_pnl)

        # 5) time stop
        days_in = (date - p0.entry_date).days
        if days_in >= time_stop:
            exit_price = close
            pnl = shares * (exit_price - entry)
            return _finalize(p0, date, exit_price, "time_stop", pnl,
                             partial_pnl + pnl,
                             partial_date, partial_price, partial_pnl)

    # window end
    last_row = sub.iloc[-1]
    last_date = sub.index[-1]
    exit_price = float(last_row["Close"])
    pnl = shares * (exit_price - entry)
    return _finalize(p0, last_date, exit_price, "window_end", pnl,
                     partial_pnl + pnl,
                     partial_date, partial_price, partial_pnl)


def run_variant(entries: list[Position],
                prices: dict[str, pd.DataFrame],
                atrs: dict[str, pd.Series],
                earnings_map: dict[str, list[datetime]],
                window_end: pd.Timestamp,
                variant: str) -> list[Trade]:
    """Replay v1 entries through one of E1..E5."""
    if variant == "E5":
        # D3 control: partial 1/2 at +2R, trail SMA20. Existing logic.
        return run_partial_trail(
            entries, prices, earnings_map, window_end,
            exit_mode="partial_trail", partial_r=2.0, trail_ma_len=20,
        )

    out = []
    for p0 in entries:
        # Fresh copy — replay doesn't mutate the source Positions.
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
        df = prices.get(p.ticker)
        if df is None or df.empty:
            continue
        atr = atrs.get(p.ticker)
        earn = earnings_map.get(p.ticker, [])
        t = _run_one_trade(p, df, atr, earn, window_end, variant)
        if t is not None:
            out.append(t)
    return out
