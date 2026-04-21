"""Quick TEAM (Atlassian) analysis."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from datetime import datetime
from config import RSI_EXIT, WEEKLY_RSI_MIN_DELTA, EARNINGS_MIN_DAYS
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from earnings import fetch_earnings_dates, next_earnings_date
from backtest import run_backtest_for_ticker

spy = add_daily_indicators(fetch_daily("SPY"))
d = add_daily_indicators(fetch_daily("TEAM"))
w = add_weekly_rsi(fetch_weekly("TEAM"))
sigs = find_rsi_signals(d)
earn = fetch_earnings_dates("TEAM")
trades, skipped = run_backtest_for_ticker("TEAM", d, w, sigs, earn, spy)

n = len(trades)
wins = sum(1 for t in trades if t["win_loss"] == "Win")
wr = wins/n*100 if n else 0
pnl = sum(t["total_profit"] for t in trades)
avg = pnl/n if n else 0
print(f"\n=== TEAM Backtest (2020-today) ===")
print(f"Trades: {n}  Wins: {wins}  Losses: {n-wins}  WR: {wr:.1f}%")
print(f"Total P&L: ${pnl:,.0f}  Avg/trade: ${avg:,.0f}")
print(f"Signals: {len(sigs)}   Skipped: {len(skipped)}")
# Qualifying threshold: 15 trades (config.MIN_QUALIFYING_TRADES)
qualifies = n >= 15 and wr >= 80
print(f"Qualifies (≥15 trades & ≥80% WR): {qualifies}")

# Recent signals
print(f"\n--- Last 5 signals ---")
for s in sigs[-5:]:
    print(f"  {s['date'].date()}  {s['type']}  RSI {s['rsi']:.1f}")

# Last trade
if trades:
    t = trades[-1]
    print(f"\nLast trade: {t['order']} entry {t['entry_date'].date()} "
          f"-> exit {t['exit_date'].date()} ({t['win_loss']})")

# Current state — mirror diagnostic logic
last_i = len(d)-1
last_date = d.index[last_i]
row = d.iloc[last_i]; prev = d.iloc[last_i-1]
print(f"\n=== Current TEAM state (as of {last_date.date()}) ===")
print(f"Close ${row['Close']:.2f}   Daily RSI {row['RSI']:.1f} (prev {prev['RSI']:.1f})")

# Most recent signal (any age)
if sigs:
    recent = sigs[-1]
    days_ago = (last_date - recent['date']).days
    print(f"Most recent RSI signal: {recent['date'].date()} {recent['type']} "
          f"RSI {recent['rsi']:.1f}  ({days_ago} days ago)")
    # Is it inside a completed trade window?
    in_trade = any(t['signal_date'] <= recent['date'] <= t['exit_date'] for t in trades)
    print(f"Already produced a trade? {in_trade}")
    # Within scanner 10-day lookback?
    cutoff = d.index[max(0, last_i-10)]
    in_window = recent['date'] >= cutoff
    print(f"Within scanner 10-bar lookback? {in_window}")

# Full entry condition breakdown on most recent signal
if sigs:
    sig = sigs[-1]; typ = sig['type']
    rsi_today, rsi_y = row['RSI'], prev['RSI']
    gap_dead = (typ=='OS' and rsi_today>=RSI_EXIT) or (typ=='OB' and rsi_today<=RSI_EXIT)
    print(f"\n--- Entry conditions today for {typ} signal ---")
    print(f"Gap-dead (RSI already past 50)? {gap_dead}")
    if typ=='OS':
        rsi_ok = rsi_today > rsi_y
    else:
        rsi_ok = rsi_today < rsi_y
    print(f"RSI direction OK: {rsi_ok}  ({rsi_today:.1f} vs {rsi_y:.1f})")
    macd, sig_l, hist = row['MACD'], row['MACD_signal'], row['MACD_hist']
    prev_hist = prev['MACD_hist']
    if typ=='OS':
        macd_ok = (macd>sig_l) or (pd.notna(hist) and pd.notna(prev_hist) and hist>0 and hist>prev_hist)
    else:
        macd_ok = (macd<sig_l) or (pd.notna(hist) and pd.notna(prev_hist) and hist<0 and hist<prev_hist)
    print(f"MACD OK: {macd_ok}  (hist {hist:+.2f} vs prev {prev_hist:+.2f}, line {macd:+.2f} vs signal {sig_l:+.2f})")
    wmask = w.index <= last_date
    wr_c = w.loc[wmask,'RSI'].iloc[-1]; wr_p = w.loc[wmask,'RSI'].iloc[-2]
    w_delta = wr_c - wr_p
    if typ=='OS':
        wk_ok = w_delta > WEEKLY_RSI_MIN_DELTA
    else:
        wk_ok = w_delta < -WEEKLY_RSI_MIN_DELTA
    print(f"Weekly RSI Δ: {w_delta:+.2f}  (need {'>+' if typ=='OS' else '<-'}{WEEKLY_RSI_MIN_DELTA})  OK: {wk_ok}")
    nxt = next_earnings_date(earn, last_date.to_pydatetime()) if earn else None
    if nxt:
        da = (nxt.date()-last_date.date()).days
        ern_ok = da > EARNINGS_MIN_DAYS
        print(f"Earnings: {nxt.date()} ({da} days)   OK: {ern_ok}")
    else:
        ern_ok = True
        print(f"Earnings: N/A")
    sm = spy.index <= last_date
    sr = spy.loc[sm].iloc[-1]; sp = spy.loc[sm].iloc[-2]
    if typ=='OS':
        spy_ok = sr['RSI']>sp['RSI'] and sr['Close']>sr['SMA50']
    else:
        spy_ok = sr['RSI']<sp['RSI'] and sr['Close']<sr['SMA50']*1.02
    print(f"SPY OK: {spy_ok}")
    checks = [not gap_dead, rsi_ok, macd_ok, wk_ok, ern_ok, spy_ok]
    print(f"\nStatus: {'ACTIONABLE' if all(checks) else 'WATCHING'}  ({sum(checks)}/6 conditions)")
