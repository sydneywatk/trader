"""Validation study: survivorship check, full-watchlist WR, open trade check."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from datetime import datetime
from config import WATCHLIST, RSI_OVERSOLD, RSI_OVERBOUGHT, RSI_EXIT, WEEKLY_RSI_MIN_DELTA
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from earnings import fetch_earnings_dates
from backtest import run_backtest_for_ticker, _check_entry_conditions

TOP_20 = ["GPN","NUGT","AKAM","CME","CMG","FIS","HBAN","MKC","RF","APO",
          "COST","FANG","MS","WM","XLU","PAYX","TSLA","BLK","CMCSA","CDW"]

WINDOW_START = pd.Timestamp("2023-05-17")

print("Loading SPY...", flush=True)
spy = add_daily_indicators(fetch_daily("SPY"))

# Q1: Count signals in window for top-20 that were filtered
print("\n=== Q1: Top-20 filter analysis (2023-05-17 to today) ===", flush=True)
top20_signals_in_window = 0
top20_taken = 0
top20_filtered_detail = {"weekly":0,"spy":0,"earnings":0,"gap":0,"other":0}

for tkr in TOP_20:
    d = add_daily_indicators(fetch_daily(tkr))
    w = add_weekly_rsi(fetch_weekly(tkr))
    sigs = find_rsi_signals(d)
    earn = fetch_earnings_dates(tkr)
    # Signals within window
    in_win = [s for s in sigs if s["date"] >= WINDOW_START]
    top20_signals_in_window += len(in_win)
    trades, skipped = run_backtest_for_ticker(tkr, d, w, sigs, earn, spy)
    taken = [t for t in trades if t["entry_date"] >= WINDOW_START]
    top20_taken += len(taken)
    # Classify skipped that fell in window
    for s in skipped:
        if s["signal_date"] >= WINDOW_START:
            r = s.get("reason","")
            if "SPY" in r: top20_filtered_detail["spy"] += 1
            elif "gap" in r.lower(): top20_filtered_detail["gap"] += 1
            else: top20_filtered_detail["other"] += 1

print(f"Top-20 signals in window: {top20_signals_in_window}")
print(f"Top-20 trades taken:      {top20_taken}")
print(f"Top-20 filtered/skipped:  {top20_signals_in_window - top20_taken}")
print(f"  Reported skip detail: {top20_filtered_detail}")
print(f"  (Unreported = no valid entry day found — weekly RSI / entry conditions never aligned)")

# Q2: Full 100-ticker WR in same 730-day window
print("\n=== Q2: Full 100-ticker watchlist WR in window ===", flush=True)
all_trades = []
ticker_summary = []
latest_entry = None
latest_exit = None
open_trades = []
today = pd.Timestamp(datetime.now().date())

for i, tkr in enumerate(WATCHLIST, 1):
    try:
        d = add_daily_indicators(fetch_daily(tkr))
        w = add_weekly_rsi(fetch_weekly(tkr))
        sigs = find_rsi_signals(d)
        earn = fetch_earnings_dates(tkr)
        trades, _ = run_backtest_for_ticker(tkr, d, w, sigs, earn, spy)
        in_win = [t for t in trades if t["entry_date"] >= WINDOW_START]
        all_trades.extend(in_win)
        # Track most recent entry across all
        for t in trades:
            if latest_entry is None or t["entry_date"] > latest_entry["entry_date"]:
                latest_entry = t
            # Open trade: exit reason "End of data" or exit_date == last bar
            last_bar = d.index[-1]
            if t["exit_date"] == last_bar and "End of data" in t.get("notes",""):
                open_trades.append(t)
    except Exception as e:
        print(f"  {tkr}: ERROR {e}")

wins = sum(1 for t in all_trades if t["win_loss"]=="Win")
n = len(all_trades)
print(f"Full 100-ticker in-window trades: {n}")
print(f"Wins: {wins}  Losses: {n-wins}  WR: {wins/n*100:.1f}%" if n else "none")
total_pnl = sum(t["total_profit"] for t in all_trades)
print(f"Total P&L: ${total_pnl:,.0f}")

# Q3: Most recent trade / active open
print("\n=== Q3: Most recent trade & open positions ===", flush=True)
if latest_entry:
    print(f"Most recent entry: {latest_entry['ticker']} {latest_entry['order']} "
          f"entry {latest_entry['entry_date'].date()} -> exit {latest_entry['exit_date'].date()} "
          f"({latest_entry['win_loss']}, ${latest_entry['total_profit']:,.0f})")

print(f"\nOpen/active trades (exit=End of data): {len(open_trades)}")
for t in open_trades:
    days_in = (today - t["entry_date"]).days
    print(f"  {t['ticker']} {t['order']} entry {t['entry_date'].date()} "
          f"@ ${t['entry_price']} stop ${t['stop_loss']} "
          f"last exit_date {t['exit_date'].date()} ({days_in}d in trade)")
