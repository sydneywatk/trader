"""SID Method Backtester — entry point."""

import sys
import time

from config import WATCHLIST
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from earnings import fetch_earnings_dates
from backtest import run_backtest_for_ticker
from output import generate_excel


def main():
    all_trades = []
    all_skipped = []
    errors = []

    total = len(WATCHLIST)
    print(f"SID Method Backtester — scanning {total} tickers\n")

    # Pre-fetch SPY data for market alignment filter
    print("Loading SPY data for market alignment filter...", end=" ", flush=True)
    try:
        spy_daily = fetch_daily("SPY")
        if not spy_daily.empty:
            spy_daily = add_daily_indicators(spy_daily)
            print(f"{len(spy_daily)} candles loaded")
        else:
            print("WARNING: no SPY data — alignment filter disabled")
            spy_daily = None
    except Exception as e:
        print(f"WARNING: SPY fetch failed ({e}) — alignment filter disabled")
        spy_daily = None

    print()

    for i, ticker in enumerate(WATCHLIST, 1):
        print(f"[{i}/{total}] {ticker}...", end=" ", flush=True)

        try:
            # Fetch data
            daily_df = fetch_daily(ticker)
            if daily_df.empty:
                print("NO DATA — skipped")
                errors.append(f"{ticker}: no daily data returned")
                continue

            weekly_df = fetch_weekly(ticker)
            if weekly_df.empty:
                print("NO WEEKLY DATA — skipped")
                errors.append(f"{ticker}: no weekly data returned")
                continue

            # Compute indicators
            daily_df = add_daily_indicators(daily_df)
            weekly_df = add_weekly_rsi(weekly_df)

            # Find RSI signals
            signals = find_rsi_signals(daily_df)

            # Fetch earnings dates
            earnings_dates = fetch_earnings_dates(ticker)
            earnings_status = f"{len(earnings_dates)} earnings dates" if earnings_dates else "no earnings data"

            # Run backtest (pass SPY data for alignment check)
            trades, skipped = run_backtest_for_ticker(
                ticker, daily_df, weekly_df, signals, earnings_dates, spy_daily
            )

            all_trades.extend(trades)
            all_skipped.extend(skipped)

            wins = sum(1 for t in trades if t["win_loss"] == "Win")
            losses = len(trades) - wins
            spy_skips = sum(1 for s in skipped if "SPY" in s.get("reason", ""))
            skip_info = f"{len(skipped)} skipped"
            if spy_skips > 0:
                skip_info += f" ({spy_skips} SPY)"
            print(f"{len(signals)} signals → {len(trades)} trades ({wins}W/{losses}L), "
                  f"{skip_info}, {earnings_status}")

        except Exception as e:
            print(f"ERROR: {e}")
            errors.append(f"{ticker}: {e}")
            continue

    # Generate output
    print(f"\n{'='*60}")
    print(f"Total trades found: {len(all_trades)}")
    print(f"Total skipped: {len(all_skipped)}")

    # SPY alignment stats
    spy_skips_total = sum(1 for s in all_skipped if "SPY" in s.get("reason", ""))
    if spy_skips_total > 0:
        print(f"  Filtered by SPY alignment: {spy_skips_total}")

    if all_trades:
        filepath = generate_excel(all_trades, all_skipped)
        print(f"Output saved to: {filepath}")

        # Summary stats
        wins = sum(1 for t in all_trades if t["win_loss"] == "Win")
        losses = len(all_trades) - wins
        total_pnl = sum(t["total_profit"] for t in all_trades)
        avg_rr = sum(t["trade_rr"] for t in all_trades) / len(all_trades)
        win_rate = wins / len(all_trades) * 100

        print(f"\n--- Summary ---")
        print(f"Wins: {wins} | Losses: {losses} | Win Rate: {win_rate:.1f}%")
        print(f"Total P&L: ${total_pnl:,.2f}")
        print(f"Average Trade RR: {avg_rr:.2f}")
    else:
        print("No trades found.")

    if errors:
        print(f"\n--- Errors ({len(errors)}) ---")
        for e in errors:
            print(f"  {e}")


if __name__ == "__main__":
    main()
