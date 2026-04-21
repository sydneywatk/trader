"""Supply & Demand Zone Backtester — daily or 1-hour intraday.

Usage:
    python3 main_sd.py                 # defaults to --intraday (Phase 2)
    python3 main_sd.py --daily         # force daily mode (Phase 1 behavior)
    python3 main_sd.py --tickers AAPL,MSFT,HRL
    python3 main_sd.py --no-confirm    # disable confirmation-candle filter
    python3 main_sd.py --skip-cont     # skip RBR/DBD continuation zones
    python3 main_sd.py --no-htf        # disable HTF SMA trend filter
    python3 main_sd.py --no-excel      # terminal summary only
    python3 main_sd.py --source yfinance|alpaca|auto   # intraday data source

Output:  ../../output/sd_method_backtest_YYYYMMDD.xlsx
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import warnings
from pathlib import Path

_HERE = Path(__file__).resolve()
_TRADER_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_TRADER_ROOT))
sys.path.insert(0, str(_HERE.parent))

import pandas as pd  # noqa: E402

import config  # noqa: E402
from shared.data import fetch_daily  # noqa: E402
from shared.data_intraday import fetch_hourly  # noqa: E402
from shared.earnings import fetch_earnings_dates  # noqa: E402
from zones import calculate_atr  # noqa: E402
from backtest_sd import run_backtest  # noqa: E402
from output_sd import generate_excel  # noqa: E402

# Phase 2 default — flip to False (or use --daily) to run the Phase 1 daily backtest.
USE_INTRADAY = True


def _load_env() -> None:
    """Pick up Alpaca keys from trader/.env if python-dotenv is available."""
    try:
        from dotenv import load_dotenv

        load_dotenv(_TRADER_ROOT / ".env")
    except ImportError:
        pass


def _add_htf_sma(df: pd.DataFrame) -> pd.DataFrame:
    """Add the SMA column used by the HTF trend filter (zone_signals looks for 'SMA50')."""
    period = (
        config.HTF_TREND_SMA_INTRADAY if config.TIMEFRAME == "1h" else config.HTF_TREND_SMA
    )
    df = df.copy()
    df["SMA50"] = df["Close"].rolling(window=period).mean()
    return df


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="S&D zone backtest (daily or 1h)")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--intraday", action="store_true", help="use 1-hour bars (default)")
    mode.add_argument("--daily", action="store_true", help="use daily bars")
    p.add_argument("--tickers", type=str, default="", help="comma-separated subset")
    p.add_argument("--no-confirm", action="store_true", help="disable confirmation-candle filter")
    p.add_argument("--skip-cont", action="store_true", help="skip RBR/DBD continuation zones")
    p.add_argument("--no-htf", action="store_true", help="disable HTF trend filter")
    p.add_argument("--no-excel", action="store_true", help="skip Excel output")
    p.add_argument(
        "--source",
        type=str,
        default="yfinance",
        choices=["auto", "alpaca", "yfinance"],
        help="intraday data source (Phase 2 initial test defaults to yfinance)",
    )
    return p.parse_args()


def main() -> dict:
    _load_env()
    args = _parse_args()

    use_intraday = USE_INTRADAY
    if args.daily:
        use_intraday = False
    elif args.intraday:
        use_intraday = True
    config.TIMEFRAME = "1h" if use_intraday else "1d"

    if args.no_confirm:
        config.REQUIRE_CONFIRMATION_CANDLE = False
    if args.skip_cont:
        config.SKIP_CONTINUATION_ZONES = True
    if args.no_htf:
        config.REQUIRE_HTF_ALIGNMENT = False

    tickers = (
        [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
        if args.tickers
        else list(config.WATCHLIST)
    )

    start = time.time()
    all_trades: list[dict] = []
    errors: list[tuple[str, str]] = []

    print()
    print("=" * 55)
    tf_label = "1-hour intraday" if use_intraday else "daily"
    print(f"S&D Zone Backtest ({tf_label}) — {len(tickers)} tickers")
    print("=" * 55)
    print(f"Timeframe:           {config.TIMEFRAME}")
    if use_intraday:
        print(f"Data source:         {args.source}")
        print(f"Zone age cap:        {config.ZONE_AGE_MAX_BARS} bars (~{config.ZONE_AGE_MAX_BARS // config.BARS_PER_DAY}d)")
        print(f"Max trade duration:  {config.MAX_TRADE_BARS} bars (~{config.MAX_TRADE_BARS // config.BARS_PER_DAY}d)")
        print(f"HTF SMA period:      {config.HTF_TREND_SMA_INTRADAY} bars")
    else:
        print(f"Zone age cap:        {config.ZONE_AGE_MAX_DAYS} bars (daily)")
        print(f"Max trade duration:  {config.MAX_TRADE_DAYS} bars (daily)")
        print(f"HTF SMA period:      {config.HTF_TREND_SMA} bars")
    print(f"Base:   range<= {config.BASE_RANGE_ATR_MULT}xATR, body<= {config.BASE_BODY_RATIO_MAX} of range")
    print(f"Impulse: range>={config.IMPULSE_RANGE_ATR_MULT}xATR within {config.IMPULSE_CHECK_BARS} bars")
    print(f"Confirm candle:      {config.REQUIRE_CONFIRMATION_CANDLE}"
          f"{'  (close-confirmation rule)' if use_intraday else '  (engulfing/hammer rule)'}")
    print(f"HTF filter:          {config.REQUIRE_HTF_ALIGNMENT}")
    print(f"Freshness cap:       {config.MAX_ZONE_TESTS} test(s)")
    print(f"Skip RBR/DBD:        {config.SKIP_CONTINUATION_ZONES}")
    print(f"RR target:           {config.RR_TARGET}")
    print()

    for i, ticker in enumerate(tickers, 1):
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                if use_intraday:
                    df = fetch_hourly(ticker, source=args.source)
                else:
                    df = fetch_daily(ticker)
            if df is None or df.empty or len(df) < 100:
                errors.append((ticker, f"insufficient data ({0 if df is None else len(df)} bars)"))
                continue

            df = _add_htf_sma(df)
            atr = calculate_atr(df, 20)
            earnings = fetch_earnings_dates(ticker)
            trades = run_backtest(ticker, df, atr, earnings)
            all_trades.extend(trades)

            wins = sum(1 for t in trades if t["win_loss"] == "Win")
            wr = (wins / len(trades) * 100.0) if trades else 0.0
            print(f"  [{i:>3}/{len(tickers)}] {ticker:<6}  {len(df):>5} bars  {len(trades):>3} trades  WR {wr:5.1f}%")
        except Exception as e:
            errors.append((ticker, str(e)))
            print(f"  [{i:>3}/{len(tickers)}] {ticker:<6}  ERROR: {e}")

    elapsed = time.time() - start
    _print_summary(all_trades, tickers, elapsed, errors, use_intraday)

    excel_path = None
    if not args.no_excel and all_trades:
        excel_path = generate_excel(all_trades, config.OUTPUT_DIR)
        print(f"Output: {excel_path}")
    print("=" * 55)

    return {
        "trades": all_trades,
        "tickers_scanned": len(tickers),
        "errors": errors,
        "excel_path": excel_path,
        "timeframe": config.TIMEFRAME,
    }


def _print_summary(
    trades: list[dict],
    tickers: list[str],
    elapsed: float,
    errors: list,
    use_intraday: bool,
) -> None:
    print()
    print("=" * 55)
    tf_label = "1-hour intraday" if use_intraday else "Daily"
    print(f"S&D ZONE BACKTEST COMPLETE — {tf_label}")
    print("=" * 55)
    print(f"Tickers scanned:     {len(tickers)}")
    print(f"Errors:              {len(errors)}")
    print(f"Elapsed:             {elapsed:.0f}s")
    print(f"Total trades:        {len(trades)}")

    if not trades:
        print("No trades generated.")
        return

    wins = sum(1 for t in trades if t["win_loss"] == "Win")
    wr = wins / len(trades) * 100.0
    total_pl = sum(t["gain_loss_dollars"] for t in trades)
    avg_rr = sum(t["trade_rr"] for t in trades) / len(trades)

    if use_intraday:
        avg_bars = sum(t.get("trade_duration_bars", 0) for t in trades) / len(trades)
        avg_hours = avg_bars  # 1 bar = 1 hour
        dur_line = f"Avg duration:        {avg_hours:.1f} hours ({avg_bars / config.BARS_PER_DAY:.1f} trading days)"
    else:
        avg_dur = sum(t["trade_duration"] for t in trades) / len(trades)
        dur_line = f"Avg duration:        {avg_dur:.1f} days"

    print(f"Win rate:            {wr:.1f}%")
    print(f"Total P&L ($100k):   ${total_pl:,.2f}")
    print(f"Avg RR:              {avg_rr:.2f}")
    print(dur_line)

    # Confirmation-candle breakdown (intraday uses 'close'/'close+engulfing' etc.)
    if use_intraday:
        pat_counts: dict[str, int] = {}
        for t in trades:
            pat_counts[t["confirmation_candle"]] = pat_counts.get(t["confirmation_candle"], 0) + 1
        if pat_counts:
            print()
            print("Confirmation pattern mix (pass-through of close filter):")
            for k in sorted(pat_counts, key=lambda x: -pat_counts[x]):
                print(f"  {k:<22} {pat_counts[k]:>4}")

    print()
    print("By zone type:")
    for zt, label in (
        ("DBR", "demand reversal "),
        ("RBD", "supply reversal "),
        ("RBR", "demand cont.    "),
        ("DBD", "supply cont.    "),
    ):
        bucket = [t for t in trades if t["zone_type"] == zt]
        marker = " ← highest priority" if zt in ("DBR", "RBD") else ""
        if not bucket:
            print(f"  {zt} ({label}):      0 trades{marker}")
            continue
        b_wins = sum(1 for t in bucket if t["win_loss"] == "Win")
        b_wr = b_wins / len(bucket) * 100.0
        b_pl = sum(t["gain_loss_dollars"] for t in bucket)
        print(f"  {zt} ({label}): {len(bucket):>4} trades, {b_wr:5.1f}% WR, ${b_pl:>10,.0f}{marker}")

    # Top/bottom tickers (min 5)
    by_ticker: dict[str, list[dict]] = {}
    for t in trades:
        by_ticker.setdefault(t["ticker"], []).append(t)
    ranked = []
    for tk, ts in by_ticker.items():
        if len(ts) < 5:
            continue
        w = sum(1 for t in ts if t["win_loss"] == "Win")
        ranked.append((tk, len(ts), w / len(ts) * 100.0))
    ranked.sort(key=lambda x: x[2], reverse=True)
    if ranked:
        top = ", ".join(f"{tk} ({wr:.0f}%)" for tk, _, wr in ranked[:5])
        bot = ", ".join(f"{tk} ({wr:.0f}%)" for tk, _, wr in ranked[-5:])
        print()
        print(f"Top 5 tickers: {top}")
        print(f"Bottom 5:      {bot}")

    print()
    print("Expected: 45–55% overall WR. DBR/RBD should outperform RBR/DBD.")


if __name__ == "__main__":
    main()
