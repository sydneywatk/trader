"""Train/test validation study for the SID Method watchlist.

TRAIN: 2020-01-01 — 2023-12-31
TEST:  2024-01-01 — today
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd
from datetime import datetime

from config import WATCHLIST
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from earnings import fetch_earnings_dates
from backtest import run_backtest_for_ticker


TRAIN_START = pd.Timestamp("2020-01-01")
TRAIN_END   = pd.Timestamp("2023-12-31")
TEST_START  = pd.Timestamp("2024-01-01")
TEST_END    = pd.Timestamp(datetime.now().date())

UNIVERSE_FILE = "output/sid_universe_ranked_20260415.xlsx"
MIN_TRAIN_TRADES = 8


def load_universe() -> list[str]:
    df = pd.read_excel(UNIVERSE_FILE)
    return df["Ticker"].tolist()


def backtest_in_window(ticker: str, spy_daily: pd.DataFrame,
                       start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    """Run full backtest, return trades whose entry_date falls in [start, end]."""
    try:
        d = fetch_daily(ticker)
        w = fetch_weekly(ticker)
        if d.empty or w.empty:
            return []
        d = add_daily_indicators(d)
        w = add_weekly_rsi(w)
        sigs = find_rsi_signals(d)
        earn = fetch_earnings_dates(ticker)
        trades, _ = run_backtest_for_ticker(ticker, d, w, sigs, earn, spy_daily)
        return [t for t in trades if start <= t["entry_date"] <= end]
    except Exception as e:
        print(f"  {ticker}: ERROR {e}")
        return []


def stats(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["win_loss"] == "Win")
    pnl = sum(t["total_profit"] for t in trades)
    return {"n": n, "wins": wins, "wr": (wins / n * 100) if n else 0.0, "pnl": pnl}


def per_ticker_stats(trades: list[dict]) -> dict[str, dict]:
    out = {}
    for t in trades:
        tk = t["ticker"]
        out.setdefault(tk, []).append(t)
    return {tk: stats(v) for tk, v in out.items()}


def main():
    print("=" * 70)
    print("  TRAIN/TEST VALIDATION STUDY")
    print("  Train: 2020-01-01 → 2023-12-31   Test: 2024-01-01 → today")
    print("=" * 70)

    print("\nLoading SPY...", flush=True)
    spy = add_daily_indicators(fetch_daily("SPY"))

    universe = load_universe()
    print(f"Universe: {len(universe)} tickers loaded from {UNIVERSE_FILE}")

    # ── Phase 1: TRAIN ──
    print(f"\nPhase 1: Backtest on universe over TRAIN window")
    print("-" * 70)
    train_trades_all = []
    train_by_ticker = {}

    for i, tk in enumerate(universe, 1):
        trades = backtest_in_window(tk, spy, TRAIN_START, TRAIN_END)
        train_trades_all.extend(trades)
        train_by_ticker[tk] = trades
        if i % 25 == 0 or i == len(universe):
            print(f"  [{i:3d}/{len(universe)}] processed", flush=True)

    # Rank by WR with minimum trade threshold
    ranked = []
    for tk, trs in train_by_ticker.items():
        s = stats(trs)
        if s["n"] >= MIN_TRAIN_TRADES:
            ranked.append((tk, s))
    ranked.sort(key=lambda x: (x[1]["wr"], x[1]["n"]), reverse=True)
    train_top_100 = [tk for tk, _ in ranked[:100]]

    # Restrict training stats to the train_top_100 subset trades
    train_top_trades = [t for t in train_trades_all if t["ticker"] in train_top_100]
    s_train = stats(train_top_trades)

    print(f"\n  Qualifying tickers (≥{MIN_TRAIN_TRADES} train trades): {len(ranked)}")
    print(f"  TRAIN_TOP_100 selected: {len(train_top_100)}")

    # ── Phase 2: TEST on TRAIN_TOP_100 ──
    print(f"\nPhase 2: Test on TRAIN_TOP_100 over TEST window")
    print("-" * 70)
    test_trades_train_top = []
    test_by_ticker_train_top = {}
    for i, tk in enumerate(train_top_100, 1):
        trades = backtest_in_window(tk, spy, TEST_START, TEST_END)
        test_trades_train_top.extend(trades)
        test_by_ticker_train_top[tk] = trades
        if i % 25 == 0 or i == len(train_top_100):
            print(f"  [{i:3d}/{len(train_top_100)}] processed", flush=True)

    s_test_trainTop = stats(test_trades_train_top)

    # ── Phase 3: TEST on CURRENT watchlist ──
    print(f"\nPhase 3: Test on CURRENT watchlist over TEST window")
    print("-" * 70)
    test_trades_current = []
    test_by_ticker_current = {}
    for i, tk in enumerate(WATCHLIST, 1):
        trades = backtest_in_window(tk, spy, TEST_START, TEST_END)
        test_trades_current.extend(trades)
        test_by_ticker_current[tk] = trades
        if i % 25 == 0 or i == len(WATCHLIST):
            print(f"  [{i:3d}/{len(WATCHLIST)}] processed", flush=True)

    s_test_curr = stats(test_trades_current)

    # ── Report ──
    def row(label, s):
        return (f"│ {label:<27s} │ {s['n']:>7d} │ {s['wr']:>5.1f}% │ "
                f"${s['pnl']:>9,.0f} │")

    print("\n" + "=" * 70)
    print("  RESULTS")
    print("=" * 70)
    print("┌─────────────────────────────┬─────────┬────────┬────────────┐")
    print("│ Scenario                    │ Trades  │ WR     │ P&L        │")
    print("├─────────────────────────────┼─────────┼────────┼────────────┤")
    print(row("Train (2020-2023) top 100",  s_train))
    print("├─────────────────────────────┼─────────┼────────┼────────────┤")
    print(row("Test (2024-today) TRAIN100", s_test_trainTop))
    print("├─────────────────────────────┼─────────┼────────┼────────────┤")
    print(row("Test (2024-today) CURRENT",  s_test_curr))
    print("└─────────────────────────────┴─────────┴────────┴────────────┘")

    # Overlap
    overlap = set(train_top_100) & set(WATCHLIST)
    print(f"\nOverlap between TRAIN_TOP_100 and CURRENT watchlist: "
          f"{len(overlap)}/{len(train_top_100)} ({len(overlap)}%)")

    # Top 10 / Bottom 10 by test period WR (min 3 test trades to be meaningful)
    # Use TRAIN_TOP_100 evaluation on test set
    per = {tk: stats(trs) for tk, trs in test_by_ticker_train_top.items() if trs}
    eligible = [(tk, s) for tk, s in per.items() if s["n"] >= 3]
    eligible.sort(key=lambda x: (x[1]["wr"], x[1]["n"]), reverse=True)

    print(f"\nTop 10 tickers by TEST-period WR (TRAIN_TOP_100, ≥3 trades):")
    for tk, s in eligible[:10]:
        print(f"  {tk:6s}  {s['n']:>2d} trades  WR {s['wr']:>5.1f}%  "
              f"P&L ${s['pnl']:>8,.0f}")

    print(f"\nBottom 10 tickers by TEST-period WR (TRAIN_TOP_100, ≥3 trades):")
    for tk, s in eligible[-10:]:
        print(f"  {tk:6s}  {s['n']:>2d} trades  WR {s['wr']:>5.1f}%  "
              f"P&L ${s['pnl']:>8,.0f}")

    # Flag CURRENT watchlist tickers with test WR < 65%
    print(f"\nCURRENT watchlist tickers with test-period WR < 65% (≥3 trades):")
    flagged = []
    for tk in WATCHLIST:
        trs = test_by_ticker_current.get(tk, [])
        s = stats(trs)
        if s["n"] >= 3 and s["wr"] < 65:
            flagged.append((tk, s))
    flagged.sort(key=lambda x: x[1]["wr"])
    if not flagged:
        print("  None — all current watchlist tickers ≥65% WR on test period.")
    else:
        for tk, s in flagged:
            print(f"  {tk:6s}  {s['n']:>2d} trades  WR {s['wr']:>5.1f}%  "
                  f"P&L ${s['pnl']:>8,.0f}  ← consider removing")

    print("\n" + "=" * 70)


if __name__ == "__main__":
    main()
