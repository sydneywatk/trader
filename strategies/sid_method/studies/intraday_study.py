"""Intraday Execution Backtest Study.

Tests whether hourly data improves execution quality on the top 20 tickers
(by universe scan WR) over a 730-day hourly window (~May 2023 – Apr 2026).
Daily signals remain unchanged — hourly data is only for execution timing.

Usage:  python3 intraday_study.py
"""

import math
import os
import sys
import time
import warnings
from copy import deepcopy
from datetime import datetime

import pandas as pd
import yfinance as yf

# Ensure project imports work
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # trader/
sys.path.insert(0, str(_HERE.parents[1]))  # strategies/sid_method/ (for local imports)

from config import ACCOUNT_SIZE, RISK_PCT, RSI_EXIT, MAX_TRADE_DAYS, CACHE_DIR, RSI_PERIOD
from shared.data import fetch_daily, fetch_weekly
from shared.indicators import add_daily_indicators, add_weekly_rsi, _rsi
from signals import find_rsi_signals
from shared.earnings import fetch_earnings_dates, next_earnings_date, last_trading_day_before_earnings
from backtest import run_backtest_for_ticker

# Top 20 tickers by WR from universe scan
TOP_20 = [
    "GPN", "NUGT", "AKAM", "CME", "CMG", "FIS", "HBAN", "MKC", "RF", "APO",
    "COST", "FANG", "MS", "WM", "XLU", "PAYX", "TSLA", "BLK", "CMCSA", "CDW",
]


# ─── Data Layer ────────────────────────────────────────────────────────────────

def fetch_hourly(ticker: str) -> pd.DataFrame:
    """Fetch 730d of hourly OHLCV data, cached to ./cache/{TICKER}_hourly.csv.

    Hourly data arrives in UTC; converted to US/Eastern (tz-naive) so that
    .date gives the correct market-hours calendar date.
    """
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, f"{ticker}_hourly.csv")

    if os.path.exists(path):
        age = time.time() - os.path.getmtime(path)
        if age < 24 * 3600:
            df = pd.read_csv(path, index_col=0, parse_dates=True)
            if len(df) > 0:
                return df

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        df = yf.download(ticker, period="730d", interval="1h",
                         progress=False, auto_adjust=True)

    if df.empty:
        return df

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # Convert to US/Eastern then strip tz for clean date comparisons
    if df.index.tz is not None:
        df.index = df.index.tz_convert("US/Eastern").tz_localize(None)
    else:
        df.index = df.index.tz_localize("UTC").tz_convert("US/Eastern").tz_localize(None)

    df.to_csv(path)
    return df


def add_hourly_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI(14) computed on hourly closes."""
    df = df.copy()
    df["RSI"] = _rsi(df["Close"], RSI_PERIOD)
    return df


def get_hourly_bars_for_date(hourly_df: pd.DataFrame,
                             date: pd.Timestamp) -> pd.DataFrame:
    """Return all hourly bars for a given calendar date.

    Typical market-hours bars: 9:30, 10:30, 11:30, 12:30, 13:30, 14:30, 15:30 ET.
    """
    target = date.date() if hasattr(date, "date") else date
    mask = hourly_df.index.date == target
    return hourly_df.loc[mask]


# ─── P&L Recomputation ────────────────────────────────────────────────────────

def recompute_trade(trade: dict, new_entry_price: float = None,
                    new_exit_price: float = None) -> dict | None:
    """Return a copy of *trade* with P&L recalculated for new prices.

    Returns None when the new prices make the trade invalid (zero risk, etc.).
    """
    t = deepcopy(trade)

    entry_price = new_entry_price if new_entry_price is not None else t["entry_price"]
    exit_price = new_exit_price if new_exit_price is not None else t["exit_price"]
    stop_loss = t["stop_loss"]

    risk_per_share = abs(entry_price - stop_loss)
    if risk_per_share <= 0:
        return None

    risk_per_position = ACCOUNT_SIZE * RISK_PCT
    max_shares = math.floor(risk_per_position / risk_per_share)
    if max_shares <= 0:
        return None

    position_size = max_shares * entry_price

    if t["order"] == "Long":
        gain_per_share = exit_price - entry_price
    else:
        gain_per_share = entry_price - exit_price

    total_profit = gain_per_share * max_shares
    pct_return = total_profit / position_size if position_size > 0 else 0
    trade_rr = total_profit / risk_per_position if risk_per_position > 0 else 0

    t["entry_price"] = round(entry_price, 2)
    t["exit_price"] = round(exit_price, 2)
    t["risk_per_share"] = round(risk_per_share, 2)
    t["max_shares"] = max_shares
    t["position_size"] = round(position_size, 2)
    t["gain_per_share"] = round(gain_per_share, 2)
    t["total_profit"] = round(total_profit, 2)
    t["trade_rr"] = round(trade_rr, 4)
    t["pct_return"] = pct_return
    t["win_loss"] = "Win" if total_profit > 0 else "Loss"
    return t


# ─── Test 1a — Enter at Next-Day Open ─────────────────────────────────────────

def test_1a(trade: dict, daily_df: pd.DataFrame) -> dict | None:
    """Replace entry at alignment-day close with next-day open."""
    entry_date = trade["entry_date"]
    try:
        idx = daily_df.index.get_loc(entry_date)
    except KeyError:
        return deepcopy(trade)

    next_idx = idx + 1
    if next_idx >= len(daily_df):
        return deepcopy(trade)

    next_day_open = daily_df.iloc[next_idx]["Open"]
    result = recompute_trade(trade, new_entry_price=next_day_open)
    if result is None:
        return None
    result["entry_date"] = daily_df.index[next_idx]
    result["duration"] = (result["exit_date"] - result["entry_date"]).days
    return result


# ─── Test 1b — Hourly RSI Confirmation Entry ──────────────────────────────────

def test_1b(trade: dict, daily_df: pd.DataFrame,
            hourly_df: pd.DataFrame) -> dict | None:
    """Enter when hourly RSI is rising within the first 3 bars of the day
    after alignment.  Fallback to baseline if no confirmation.
    """
    entry_date = trade["entry_date"]
    try:
        idx = daily_df.index.get_loc(entry_date)
    except KeyError:
        return deepcopy(trade)

    next_idx = idx + 1
    if next_idx >= len(daily_df):
        return deepcopy(trade)

    next_day = daily_df.index[next_idx]
    target = next_day.date() if hasattr(next_day, "date") else next_day
    day_indices = hourly_df.index[hourly_df.index.date == target]

    if len(day_indices) == 0:
        return deepcopy(trade)  # No hourly data — fallback

    # Check up to 3 bars (9:30, 10:30, extend to 11:30)
    check_limit = min(3, len(day_indices))
    for bar_num in range(check_limit):
        bar_pos = hourly_df.index.get_loc(day_indices[bar_num])
        if bar_pos == 0:
            continue  # No previous bar in series

        curr_rsi = hourly_df.iloc[bar_pos]["RSI"]
        prev_rsi = hourly_df.iloc[bar_pos - 1]["RSI"]

        if pd.isna(curr_rsi) or pd.isna(prev_rsi):
            continue

        if curr_rsi > prev_rsi:
            confirm_price = hourly_df.iloc[bar_pos]["Close"]
            result = recompute_trade(trade, new_entry_price=confirm_price)
            if result is None:
                return None
            result["entry_date"] = next_day
            result["duration"] = (result["exit_date"] - result["entry_date"]).days
            return result

    # No confirmation — fallback to baseline (same entry)
    return deepcopy(trade)


# ─── Test 2 — Hourly Peak Exit on RSI-50 Day ──────────────────────────────────

def _is_rsi50_exit(trade: dict) -> bool:
    """True when the baseline trade exited via RSI reaching 50.

    Convention from run_backtest_for_ticker: the notes string only contains
    'Exit: <reason>.' for NON-RSI-50 exits.
    """
    return "Exit:" not in trade.get("notes", "")


def test_2(trade: dict, hourly_df: pd.DataFrame) -> dict:
    """On RSI-50 exit days, use best hourly close instead of daily close."""
    if not _is_rsi50_exit(trade):
        return deepcopy(trade)

    exit_date = trade["exit_date"]
    bars = get_hourly_bars_for_date(hourly_df, exit_date)
    if bars.empty:
        return deepcopy(trade)

    if trade["order"] == "Long":
        best_price = bars["Close"].max()
    else:
        best_price = bars["Close"].min()

    result = recompute_trade(trade, new_exit_price=best_price)
    return result if result is not None else deepcopy(trade)


# ─── Test 3 — Stop Loss via Hourly Close ──────────────────────────────────────

def _is_stop_exit(trade: dict) -> bool:
    return "Exit: Stop loss." in trade.get("notes", "")


def test_3(trade: dict, daily_df: pd.DataFrame, hourly_df: pd.DataFrame,
           earnings_dates: list) -> tuple[dict, bool]:
    """Stop triggers only if an hourly *Close* breaches stop, not a daily wick.

    Returns (modified_trade, stop_was_avoided).
    """
    if not _is_stop_exit(trade):
        return deepcopy(trade), False

    exit_date = trade["exit_date"]
    stop_loss = trade["stop_loss"]
    order = trade["order"]
    signal_type = "OS" if order == "Long" else "OB"

    # Check hourly bars on the original stop day
    bars = get_hourly_bars_for_date(hourly_df, exit_date)
    if bars.empty:
        return deepcopy(trade), False  # No hourly data — keep baseline

    if order == "Long":
        hourly_confirms = (bars["Close"] <= stop_loss).any()
    else:
        hourly_confirms = (bars["Close"] >= stop_loss).any()

    if hourly_confirms:
        return deepcopy(trade), False  # Hourly confirms the stop — same result

    # ── Stop AVOIDED — re-simulate remaining exit path ──
    try:
        exit_idx = daily_df.index.get_loc(exit_date)
        entry_idx = daily_df.index.get_loc(trade["entry_date"])
    except KeyError:
        return deepcopy(trade), True

    # Earnings exit date (for completeness)
    entry_dt = trade["entry_date"].to_pydatetime() if hasattr(trade["entry_date"], "to_pydatetime") else trade["entry_date"]
    nxt_earn = next_earnings_date(earnings_dates, entry_dt) if earnings_dates else None
    earn_exit_date = last_trading_day_before_earnings(daily_df, nxt_earn) if nxt_earn else None

    # Scan from original stop day onward (stop was avoided, but check other exits)
    for i in range(exit_idx, len(daily_df)):
        row = daily_df.iloc[i]
        date = daily_df.index[i]
        rsi_today = row["RSI"]
        if pd.isna(rsi_today):
            continue

        # Stop check with hourly confirmation
        daily_breach = False
        if signal_type == "OS" and row["Low"] <= stop_loss:
            daily_breach = True
        elif signal_type == "OB" and row["High"] >= stop_loss:
            daily_breach = True

        if daily_breach:
            day_bars = get_hourly_bars_for_date(hourly_df, date)
            if not day_bars.empty:
                if signal_type == "OS":
                    h_confirm = (day_bars["Close"] <= stop_loss).any()
                else:
                    h_confirm = (day_bars["Close"] >= stop_loss).any()
                if h_confirm:
                    result = recompute_trade(trade, new_exit_price=stop_loss)
                    if result:
                        result["exit_date"] = date
                        result["duration"] = (date - trade["entry_date"]).days
                    return result or deepcopy(trade), True
                # Hourly didn't confirm — stop avoided again, continue
            else:
                # No hourly data for this day — conservatively trigger stop
                result = recompute_trade(trade, new_exit_price=stop_loss)
                if result:
                    result["exit_date"] = date
                    result["duration"] = (date - trade["entry_date"]).days
                return result or deepcopy(trade), True

        # RSI-50 take profit
        if signal_type == "OS" and rsi_today >= RSI_EXIT:
            result = recompute_trade(trade, new_exit_price=row["Close"])
            if result:
                result["exit_date"] = date
                result["duration"] = (date - trade["entry_date"]).days
            return result or deepcopy(trade), True
        if signal_type == "OB" and rsi_today <= RSI_EXIT:
            result = recompute_trade(trade, new_exit_price=row["Close"])
            if result:
                result["exit_date"] = date
                result["duration"] = (date - trade["entry_date"]).days
            return result or deepcopy(trade), True

        # Time exit
        trading_days_in = i - entry_idx
        if trading_days_in >= MAX_TRADE_DAYS:
            result = recompute_trade(trade, new_exit_price=row["Close"])
            if result:
                result["exit_date"] = date
                result["duration"] = (date - trade["entry_date"]).days
            return result or deepcopy(trade), True

        # Earnings exit
        if earn_exit_date is not None and date >= earn_exit_date:
            ep = daily_df.loc[earn_exit_date, "Close"] if earn_exit_date in daily_df.index else row["Close"]
            result = recompute_trade(trade, new_exit_price=ep)
            if result:
                result["exit_date"] = earn_exit_date
                result["duration"] = (earn_exit_date - trade["entry_date"]).days
            return result or deepcopy(trade), True

    # End of data
    last = daily_df.iloc[-1]
    result = recompute_trade(trade, new_exit_price=last["Close"])
    if result:
        result["exit_date"] = daily_df.index[-1]
        result["duration"] = (daily_df.index[-1] - trade["entry_date"]).days
    return result or deepcopy(trade), True


# ─── Test 4 — First Hourly RSI-50 Touch Exit ──────────────────────────────────

def test_4(trade: dict, hourly_df: pd.DataFrame) -> tuple[dict, float | None]:
    """On RSI-50 exit day, exit at first hourly bar where hourly RSI crosses 50.

    Returns (modified_trade, hours_earlier_vs_daily_close).
    - hours_earlier > 0  ⇒  exited earlier in the session
    - hours_earlier == 0 ⇒  exited at last bar (same as daily close)
    - None               ⇒  trade not eligible (non-RSI50 exit) OR no hourly
                            data for the day (fell back to daily close)
    """
    if not _is_rsi50_exit(trade):
        return deepcopy(trade), None

    exit_date = trade["exit_date"]
    bars = get_hourly_bars_for_date(hourly_df, exit_date)
    if bars.empty:
        return deepcopy(trade), None

    order = trade["order"]
    last_bar_ts = bars.index[-1]

    for ts, row in bars.iterrows():
        rsi = row["RSI"]
        if pd.isna(rsi):
            continue
        if (order == "Long" and rsi >= RSI_EXIT) or \
           (order == "Short" and rsi <= RSI_EXIT):
            hours_earlier = (last_bar_ts - ts).total_seconds() / 3600.0
            result = recompute_trade(trade, new_exit_price=row["Close"])
            return (result if result is not None else deepcopy(trade)), hours_earlier

    # No hourly bar crossed 50 on the exit day — keep daily close
    return deepcopy(trade), 0.0


# ─── Test 4b — Earliest Hourly RSI-50 Touch Across Holding Period ─────────────

def test_4b(trade: dict, daily_df: pd.DataFrame,
            hourly_df: pd.DataFrame) -> tuple[dict, int, bool]:
    """Scan every day of the holding period for the earliest hourly bar
    where hourly RSI crosses 50 — exit there.

    Returns (modified_trade, days_earlier_vs_daily_exit, triggered_before_daily).
    - triggered_before_daily = True when we found an hourly 50-touch on a day
      BEFORE the baseline's daily exit (catches the "closed below 50 intraday
      but touched 50" cases).
    """
    order = trade["order"]
    try:
        entry_idx = daily_df.index.get_loc(trade["entry_date"])
        exit_idx = daily_df.index.get_loc(trade["exit_date"])
    except KeyError:
        return deepcopy(trade), 0, False

    for i in range(entry_idx + 1, exit_idx + 1):
        day = daily_df.index[i]
        bars = get_hourly_bars_for_date(hourly_df, day)
        if bars.empty:
            continue
        for ts, row in bars.iterrows():
            rsi = row["RSI"]
            if pd.isna(rsi):
                continue
            if (order == "Long" and rsi >= RSI_EXIT) or \
               (order == "Short" and rsi <= RSI_EXIT):
                result = recompute_trade(trade, new_exit_price=row["Close"])
                if result is not None:
                    result["exit_date"] = day
                    result["duration"] = (day - trade["entry_date"]).days
                days_earlier = (trade["exit_date"] - day).days
                triggered_early = i < exit_idx
                return (result if result is not None else deepcopy(trade)), \
                       days_earlier, triggered_early

    # Never crossed 50 at hourly granularity — keep baseline exit
    return deepcopy(trade), 0, False


# ─── Reporting ─────────────────────────────────────────────────────────────────

def compute_stats(trades: list[dict]) -> dict:
    if not trades:
        return {"count": 0, "wins": 0, "wr": 0, "avg_pnl": 0, "total_pnl": 0}
    count = len(trades)
    wins = sum(1 for t in trades if t["win_loss"] == "Win")
    total_pnl = sum(t["total_profit"] for t in trades)
    return {
        "count": count,
        "wins": wins,
        "wr": wins / count * 100 if count else 0,
        "avg_pnl": total_pnl / count if count else 0,
        "total_pnl": total_pnl,
    }


def fmt_dollar(v: float) -> str:
    return f"${v:>,.0f}"


def fmt_dollar_delta(v: float) -> str:
    return f"${v:>+,.0f}"


def print_comparison(label: str, bl: dict, ts: dict,
                     stops_avoided: int = None):
    print(f"\n{'=' * 65}")
    print(f"  {label}")
    print(f"{'=' * 65}")
    print(f"{'':20s} {'Baseline':>14s} {'Test':>14s} {'Delta':>14s}")
    print(f"{'-' * 65}")

    print(f"{'Trades':20s} {bl['count']:>14d} {ts['count']:>14d} "
          f"{ts['count'] - bl['count']:>+14d}")
    print(f"{'Wins':20s} {bl['wins']:>14d} {ts['wins']:>14d} "
          f"{ts['wins'] - bl['wins']:>+14d}")

    wr_d = ts["wr"] - bl["wr"]
    print(f"{'Win Rate':20s} {bl['wr']:>13.1f}% {ts['wr']:>13.1f}% "
          f"{wr_d:>+13.1f}pp")

    print(f"{'Avg P&L/Trade':20s} {fmt_dollar(bl['avg_pnl']):>14s} "
          f"{fmt_dollar(ts['avg_pnl']):>14s} "
          f"{fmt_dollar_delta(ts['avg_pnl'] - bl['avg_pnl']):>14s}")

    print(f"{'Total P&L':20s} {fmt_dollar(bl['total_pnl']):>14s} "
          f"{fmt_dollar(ts['total_pnl']):>14s} "
          f"{fmt_dollar_delta(ts['total_pnl'] - bl['total_pnl']):>14s}")

    if stops_avoided is not None:
        print(f"{'Stops Avoided':20s} {'N/A':>14s} {stops_avoided:>14d}")

    print(f"{'=' * 65}")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("  INTRADAY EXECUTION BACKTEST STUDY")
    print("  Top 20 Tickers | 730-day Hourly Window")
    print("=" * 65)

    # ── Step 1: Pre-fetch SPY daily + indicators ──
    print("\nLoading SPY data...", end=" ", flush=True)
    spy_daily = fetch_daily("SPY")
    if not spy_daily.empty:
        spy_daily = add_daily_indicators(spy_daily)
        print(f"{len(spy_daily)} bars")
    else:
        print("FAILED — alignment filter disabled")
        spy_daily = None

    # ── Step 2: Run baseline backtests, fetch hourly data ──
    baseline_trades = []
    ticker_data = {}
    hourly_start_date = None

    print(f"\nPhase 1: Daily backtests + hourly data for {len(TOP_20)} tickers")
    print("-" * 65)

    for i, ticker in enumerate(TOP_20, 1):
        print(f"  [{i:2d}/{len(TOP_20)}] {ticker}...", end=" ", flush=True)
        try:
            daily_df = fetch_daily(ticker)
            weekly_df = fetch_weekly(ticker)
            if daily_df.empty or weekly_df.empty:
                print("NO DATA")
                continue

            daily_df = add_daily_indicators(daily_df)
            weekly_df = add_weekly_rsi(weekly_df)
            signals = find_rsi_signals(daily_df)
            earnings_dates = fetch_earnings_dates(ticker)

            trades, _ = run_backtest_for_ticker(
                ticker, daily_df, weekly_df, signals, earnings_dates, spy_daily
            )

            # Fetch hourly
            hourly_df = fetch_hourly(ticker)
            if not hourly_df.empty:
                hourly_df = add_hourly_rsi(hourly_df)
                if hourly_start_date is None:
                    hourly_start_date = pd.Timestamp(hourly_df.index.min().date())

            ticker_data[ticker] = {
                "daily": daily_df,
                "weekly": weekly_df,
                "hourly": hourly_df if not hourly_df.empty else pd.DataFrame(),
                "earnings": earnings_dates,
            }

            # Filter trades to hourly window
            if hourly_start_date is not None:
                window_trades = [t for t in trades
                                 if t["entry_date"] >= hourly_start_date]
            else:
                window_trades = []

            baseline_trades.extend(window_trades)
            wins = sum(1 for t in window_trades if t["win_loss"] == "Win")
            total = len(window_trades)
            print(f"{len(trades)} total, {total} in window "
                  f"({wins}W/{total - wins}L)")

        except Exception as e:
            print(f"ERROR: {e}")
            continue

    if not baseline_trades:
        print("\nNo baseline trades in hourly window. Exiting.")
        return

    bl_stats = compute_stats(baseline_trades)
    print(f"\n{'=' * 65}")
    print(f"  BASELINE: {bl_stats['count']} trades in hourly window "
          f"(starts {hourly_start_date.strftime('%Y-%m-%d')})")
    print(f"  Win Rate: {bl_stats['wr']:.1f}%  |  "
          f"Total P&L: {fmt_dollar(bl_stats['total_pnl'])}")
    print(f"{'=' * 65}")

    # ── Step 3–4: Run tests ──
    print(f"\nPhase 2: Intraday execution tests")
    print("-" * 65)

    # Test 1a
    print("  Test 1a: Enter at next-day open...", end=" ", flush=True)
    t1a_trades = []
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        result = test_1a(trade, td["daily"])
        if result is not None:
            t1a_trades.append(result)
    print(f"{len(t1a_trades)} trades")

    # Test 1b
    print("  Test 1b: Hourly RSI confirmation...", end=" ", flush=True)
    t1b_trades = []
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        hourly = td["hourly"]
        if hourly.empty:
            t1b_trades.append(deepcopy(trade))
            continue
        result = test_1b(trade, td["daily"], hourly)
        if result is not None:
            t1b_trades.append(result)
    print(f"{len(t1b_trades)} trades")

    # Test 2
    print("  Test 2: Hourly peak exit (RSI-50)...", end=" ", flush=True)
    t2_trades = []
    rsi50_eligible = 0
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        hourly = td["hourly"]
        if _is_rsi50_exit(trade):
            rsi50_eligible += 1
        if hourly.empty:
            t2_trades.append(deepcopy(trade))
            continue
        result = test_2(trade, hourly)
        if result is not None:
            t2_trades.append(result)
    print(f"{len(t2_trades)} trades ({rsi50_eligible} RSI-50 exits eligible)")

    # Test 3
    print("  Test 3: Stop via hourly close...", end=" ", flush=True)
    t3_trades = []
    stops_avoided = 0
    stop_exits_total = sum(1 for t in baseline_trades if _is_stop_exit(t))
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        hourly = td["hourly"]
        if hourly.empty:
            t3_trades.append(deepcopy(trade))
            continue
        result, avoided = test_3(trade, td["daily"], hourly, td["earnings"])
        if result is not None:
            t3_trades.append(result)
        if avoided:
            stops_avoided += 1
    print(f"{len(t3_trades)} trades, {stops_avoided}/{stop_exits_total} "
          f"stops avoided")

    # Test 4 — First hourly RSI-50 touch on exit day
    print("  Test 4:  First hourly RSI-50 touch...", end=" ", flush=True)
    t4_trades = []
    t4_earlier_count = 0
    t4_same_count = 0
    t4_fallback_count = 0
    t4_hours_earlier = []
    rsi50_for_t4 = 0
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        hourly = td["hourly"]
        if not _is_rsi50_exit(trade):
            t4_trades.append(deepcopy(trade))
            continue
        rsi50_for_t4 += 1
        if hourly.empty:
            t4_trades.append(deepcopy(trade))
            t4_fallback_count += 1
            continue
        result, hrs = test_4(trade, hourly)
        t4_trades.append(result)
        if hrs is None:
            t4_fallback_count += 1
        elif hrs > 0:
            t4_earlier_count += 1
            t4_hours_earlier.append(hrs)
        else:
            t4_same_count += 1
    avg_hrs_earlier = (sum(t4_hours_earlier) / len(t4_hours_earlier)
                       if t4_hours_earlier else 0.0)
    print(f"{len(t4_trades)} trades — {t4_earlier_count} earlier, "
          f"{t4_same_count} same, {t4_fallback_count} fallback")

    # Test 4b — Earliest hourly RSI-50 touch across holding period
    print("  Test 4b: Earliest intraday 50 touch...", end=" ", flush=True)
    t4b_trades = []
    t4b_early_triggers = 0
    t4b_days_earlier_list = []
    for trade in baseline_trades:
        td = ticker_data.get(trade["ticker"])
        if td is None:
            continue
        hourly = td["hourly"]
        if hourly.empty:
            t4b_trades.append(deepcopy(trade))
            continue
        result, days_earlier, triggered_early = test_4b(
            trade, td["daily"], hourly
        )
        t4b_trades.append(result)
        if triggered_early:
            t4b_early_triggers += 1
            t4b_days_earlier_list.append(days_earlier)
    avg_days_earlier = (sum(t4b_days_earlier_list) / len(t4b_days_earlier_list)
                        if t4b_days_earlier_list else 0.0)
    print(f"{len(t4b_trades)} trades, {t4b_early_triggers} exited earlier "
          f"(avg {avg_days_earlier:.1f}d earlier)")

    # ── Step 5: Comparison tables ──
    t1a_stats = compute_stats(t1a_trades)
    t1b_stats = compute_stats(t1b_trades)
    t2_stats = compute_stats(t2_trades)
    t3_stats = compute_stats(t3_trades)
    t4_stats = compute_stats(t4_trades)
    t4b_stats = compute_stats(t4b_trades)

    print_comparison("Test 1a — Enter at Next-Day Open", bl_stats, t1a_stats)
    print_comparison("Test 1b — Hourly RSI Confirmation Entry",
                     bl_stats, t1b_stats)
    print_comparison("Test 2 — Hourly Peak Exit on RSI-50 Day",
                     bl_stats, t2_stats)
    print_comparison("Test 3 — Stop Loss via Hourly Close (not daily wick)",
                     bl_stats, t3_stats, stops_avoided)
    print_comparison("Test 4 — First Hourly RSI-50 Touch (exit day only)",
                     bl_stats, t4_stats)
    print(f"  RSI-50 exits eligible:   {rsi50_for_t4}")
    print(f"  Exited EARLIER:          {t4_earlier_count}  "
          f"(avg {avg_hrs_earlier:.1f} hours earlier vs last bar)")
    print(f"  Exited at last bar:      {t4_same_count}  (no earlier cross)")
    print(f"  Fallback (no hourly):    {t4_fallback_count}")

    print_comparison("Test 4b — Earliest Hourly RSI-50 Across Holding Period",
                     bl_stats, t4b_stats)
    print(f"  Trades exited EARLIER than baseline: {t4b_early_triggers}")
    print(f"  Avg days earlier:                    {avg_days_earlier:.1f}")
    print(f"  (Captures trades where hourly RSI touched 50 intraday but "
          f"daily close didn't.)")

    # ── Recommendation ──
    print(f"\n{'=' * 65}")
    print("  RECOMMENDATION")
    print(f"{'=' * 65}")

    deltas = {
        "1a (Next-Day Open)": t1a_stats["total_pnl"] - bl_stats["total_pnl"],
        "1b (Hourly RSI Entry)": t1b_stats["total_pnl"] - bl_stats["total_pnl"],
        "2 (Hourly Peak Exit)": t2_stats["total_pnl"] - bl_stats["total_pnl"],
        "3 (Hourly Stop)": t3_stats["total_pnl"] - bl_stats["total_pnl"],
        "4 (First RSI-50 Touch, exit day)": t4_stats["total_pnl"] - bl_stats["total_pnl"],
        "4b (Earliest RSI-50, any day)":    t4b_stats["total_pnl"] - bl_stats["total_pnl"],
    }

    best_test = max(deltas, key=deltas.get)
    best_delta = deltas[best_test]

    for name, delta in deltas.items():
        marker = "  <<<" if name == best_test else ""
        print(f"  Test {name}: {fmt_dollar_delta(delta)}{marker}")

    print()
    if best_delta > 0:
        print(f"  Best: Test {best_test} adds "
              f"{fmt_dollar(best_delta)} total P&L.")
        wr_imp = {
            "1a": t1a_stats["wr"] - bl_stats["wr"],
            "1b": t1b_stats["wr"] - bl_stats["wr"],
            "2": t2_stats["wr"] - bl_stats["wr"],
            "3": t3_stats["wr"] - bl_stats["wr"],
        }
        pos_wr = {k: v for k, v in wr_imp.items() if v > 0}
        if pos_wr:
            parts = [f"Test {k} (+{v:.1f}pp)" for k, v in pos_wr.items()]
            print(f"  Win rate improved in: {', '.join(parts)}")
        if stops_avoided > 0:
            print(f"  Test 3 avoided {stops_avoided} false stops "
                  f"(wick-only breaches).")
        print(f"\n  Intraday execution is WORTH PURSUING for the "
              f"strongest tests.")
    else:
        print(f"  No test improved total P&L. Daily execution is sufficient.")
        print(f"  Intraday execution is NOT worth pursuing.")

    print(f"{'=' * 65}\n")


if __name__ == "__main__":
    main()
