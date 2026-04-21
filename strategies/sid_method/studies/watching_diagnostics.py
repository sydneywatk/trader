"""Diagnostic detail for every 'watching' ticker produced by daily_scanner.

For each pending signal, reports which specific entry condition(s) fail today
and how close the setup is to triggering.
"""
import sys, os
from pathlib import Path as _Path
_HERE = _Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # trader/
sys.path.insert(0, str(_HERE.parents[1]))  # strategies/sid_method/

import pandas as pd

from config import (
    WATCHLIST, RSI_EXIT, RSI_OVERSOLD, RSI_OVERBOUGHT,
    WEEKLY_RSI_MIN_DELTA, EARNINGS_MIN_DAYS,
)
from shared.data import fetch_daily, fetch_weekly
from shared.indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from shared.earnings import fetch_earnings_dates, next_earnings_date
from backtest import run_backtest_for_ticker

LOOKBACK = 10


def diagnose(ticker, spy):
    d = fetch_daily(ticker)
    w = fetch_weekly(ticker)
    if d.empty or w.empty:
        return []
    d = add_daily_indicators(d)
    w = add_weekly_rsi(w)
    sigs = find_rsi_signals(d)
    earn = fetch_earnings_dates(ticker)

    last_i = len(d) - 1
    last_date = d.index[last_i]
    row = d.iloc[last_i]
    prev = d.iloc[last_i - 1]

    trades, _ = run_backtest_for_ticker(ticker, d, w, sigs, earn, spy)
    trade_windows = [(t["signal_date"], t["exit_date"]) for t in trades]

    cutoff = d.index[max(0, last_i - LOOKBACK)]
    pending = [s for s in sigs if s["date"] >= cutoff
               and not any(sd <= s["date"] <= ed for sd, ed in trade_windows)]

    results = []
    for sig in pending:
        typ = sig["type"]
        rsi_today = row["RSI"]
        rsi_yest = prev["RSI"]

        # Gap check
        gap_dead = (typ == "OS" and rsi_today >= RSI_EXIT) or \
                   (typ == "OB" and rsi_today <= RSI_EXIT)
        if gap_dead:
            continue  # already gapped — not watching

        blockers = []
        metrics = []

        # A: Daily RSI direction + MACD
        if typ == "OS":
            rsi_dir_ok = rsi_today > rsi_yest
        else:
            rsi_dir_ok = rsi_today < rsi_yest
        if not rsi_dir_ok:
            blockers.append("RSI direction")
        metrics.append(f"RSI {rsi_today:.1f} "
                       f"({'+' if rsi_today>=rsi_yest else ''}{rsi_today-rsi_yest:.1f})")

        macd, sig_l, hist = row["MACD"], row["MACD_signal"], row["MACD_hist"]
        prev_hist = prev["MACD_hist"]
        if typ == "OS":
            macd_ok = (macd > sig_l) or (
                pd.notna(hist) and pd.notna(prev_hist)
                and hist > 0 and hist > prev_hist
            )
        else:
            macd_ok = (macd < sig_l) or (
                pd.notna(hist) and pd.notna(prev_hist)
                and hist < 0 and hist < prev_hist
            )
        if not macd_ok:
            blockers.append("MACD")
        metrics.append(f"MACD hist {hist:+.2f} vs prev {prev_hist:+.2f}")

        # B: Weekly RSI delta
        wmask = w.index <= last_date
        if wmask.sum() >= 2:
            wrsi = w.loc[wmask, "RSI"].iloc[-1]
            wrsi_prev = w.loc[wmask, "RSI"].iloc[-2]
            w_delta = wrsi - wrsi_prev
            if typ == "OS":
                weekly_ok = w_delta > WEEKLY_RSI_MIN_DELTA
            else:
                weekly_ok = w_delta < -WEEKLY_RSI_MIN_DELTA
            if not weekly_ok:
                blockers.append(f"Weekly RSI ({w_delta:+.1f}pts, "
                                f"need {'>+' if typ=='OS' else '<-'}"
                                f"{WEEKLY_RSI_MIN_DELTA})")
            metrics.append(f"Weekly Δ {w_delta:+.1f}")
        else:
            weekly_ok = False
            blockers.append("Weekly RSI (insufficient data)")

        # C: Earnings
        nxt = next_earnings_date(earn, last_date.to_pydatetime()) if earn else None
        if nxt:
            days_to_earn = (nxt.date() - last_date.date()).days
            earn_ok = days_to_earn > EARNINGS_MIN_DAYS
            if not earn_ok:
                blockers.append(f"Earnings in {days_to_earn}d "
                                f"(need >{EARNINGS_MIN_DAYS})")
            metrics.append(f"Earnings in {days_to_earn}d")
        else:
            earn_ok = True

        # E: SPY alignment
        sp_mask = spy.index <= last_date
        sp_row = spy.loc[sp_mask].iloc[-1]
        sp_prev = spy.loc[sp_mask].iloc[-2]
        sp_rsi_dir = sp_row["RSI"] - sp_prev["RSI"]
        sp_vs_sma = sp_row["Close"] / sp_row["SMA50"] - 1
        if typ == "OS":
            spy_ok = sp_rsi_dir > 0 and sp_row["Close"] > sp_row["SMA50"]
        else:
            spy_ok = sp_rsi_dir < 0 and sp_row["Close"] < sp_row["SMA50"] * 1.02
        if not spy_ok:
            blockers.append("SPY regime")

        # Proximity — how many of 4 conditions are met
        checks = [rsi_dir_ok, macd_ok, weekly_ok, earn_ok, spy_ok]
        met = sum(bool(c) for c in checks)
        proximity = f"{met}/5 conditions met"

        results.append({
            "ticker": ticker,
            "order": "Long" if typ == "OS" else "Short",
            "signal_date": sig["date"],
            "signal_rsi": sig["rsi"],
            "current_rsi": rsi_today,
            "blockers": blockers,
            "metrics": metrics,
            "proximity": proximity,
            "met_count": met,
        })
    return results


def main():
    print("Loading SPY...", flush=True)
    spy = add_daily_indicators(fetch_daily("SPY"))
    print(f"SPY through {spy.index[-1].strftime('%Y-%m-%d')}")

    all_watching = []
    for tk in WATCHLIST:
        try:
            all_watching.extend(diagnose(tk, spy))
        except Exception as e:
            print(f"  {tk}: {e}")

    # sort: more conditions met first (closest to triggering), then recency
    all_watching.sort(key=lambda x: (-x["met_count"], -x["signal_date"].value))

    print(f"\n{'=' * 75}")
    print(f"  WATCHING — {len(all_watching)} pending setups  "
          f"(sorted by proximity to trigger)")
    print('=' * 75)

    for w in all_watching:
        print(f"\n  {w['ticker']:6s}  {w['order'].upper():5s}  "
              f"signal {w['signal_date'].strftime('%Y-%m-%d')} "
              f"@ RSI {w['signal_rsi']:.1f}  →  now RSI {w['current_rsi']:.1f}")
        print(f"    Proximity: {w['proximity']}")
        print(f"    Metrics:   {' | '.join(w['metrics'])}")
        if w["blockers"]:
            print(f"    BLOCKED by: {', '.join(w['blockers'])}")
        else:
            print(f"    ★ ALL CONDITIONS MET — should be actionable")

    print(f"\n{'=' * 75}")


if __name__ == "__main__":
    main()
