"""Breakout v1 backtest entry point.

Pipeline:
  1) Load universe (S&P 500 current).
  2) Bulk-fetch daily OHLCV back to 2013 (incl. SPY).
  3) Load earnings dates.
  4) Prepare indicators, cross-sectional RS, SPY, breadth.
  5) Generate signals for the requested window(s).
  6) Run baseline (partial + trail) backtest.
  7) Replay entries through 2R ablation exit.
  8) Compute correlation gate vs SID + S&D-long.
  9) Write Excel trade log + skip CSV; print summary.
 10) Dump metrics JSON to cache for the results doc.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

# sys.path bootstrap so `from config`, `from signals`, etc. resolve.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # trader/
sys.path.insert(0, str(Path(__file__).resolve().parent))       # strategies/breakout/

import pandas as pd

from config import (
    DATA_START, TRAIN_START, TRAIN_END, TEST_START, TEST_END,
)
from shared.config import OUTPUT_DIR, CACHE_DIR
from shared.earnings import fetch_earnings_dates
from shared.breadth import compute_breadth_above_ma

from universe import load_universe
from data import bulk_fetch, fetch_one
from signals import (
    prepare_ticker_indicators, prepare_spy, compute_rs_rank,
    generate_signals,
)
from backtest import run_backtest, Position
from exit_ablation import run_ablation
from output import (
    summarize_trades, equity_curve, drawdown, cagr, sharpe,
    skip_counts, correlation_gate, write_excel, write_skip_csv,
    print_summary, train_test_delta,
)


def _resolve_end(end_str: str) -> pd.Timestamp:
    if end_str == "today":
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(end_str)


def _latest_file(prefix: str, directory: str = OUTPUT_DIR) -> str | None:
    if not os.path.isdir(directory):
        return None
    matches = sorted([f for f in os.listdir(directory) if f.startswith(prefix) and f.endswith(".xlsx")])
    return os.path.join(directory, matches[-1]) if matches else None


def _positions_from_trades(trades) -> list[Position]:
    """Rebuild Position dataclasses from Trade records (for ablation replay)."""
    out: list[Position] = []
    for t in trades:
        out.append(Position(
            ticker=t.ticker,
            signal_date=t.signal_date,
            entry_date=t.entry_date,
            entry_price=t.entry_price,
            stop_price=t.stop_price,
            initial_shares=t.initial_shares,
            shares=t.initial_shares,
            r_per_share=t.entry_price - t.stop_price,
            rs_rank_at_entry=t.rs_rank_at_entry,
        ))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh-data", action="store_true",
                    help="Force re-pull of all price data (ignores 24h cache).")
    ap.add_argument("--refresh-universe", action="store_true",
                    help="Re-scrape S&P 500 membership from Wikipedia.")
    ap.add_argument("--skip-earnings", action="store_true",
                    help="Skip earnings-dates pull (diagnostic only).")
    ap.add_argument("--tickers", type=str, default=None,
                    help="Comma-separated subset override (diagnostic).")
    args = ap.parse_args()

    run_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ---- 1) universe ----
    print("[1/10] loading universe...")
    if args.tickers:
        tickers = [t.strip().upper() for t in args.tickers.split(",")]
        print(f"  -> override: {len(tickers)} tickers")
    else:
        tickers = load_universe(refresh=args.refresh_universe)
        print(f"  -> {len(tickers)} tickers from S&P 500")

    # ---- 2) data ----
    print(f"[2/10] fetching price data from {DATA_START}...")
    prices_raw = bulk_fetch(tickers + ["SPY"], start=DATA_START,
                            force_refresh=args.refresh_data)
    print(f"  -> {len(prices_raw)} tickers with data (incl. SPY)")
    spy_raw = prices_raw.pop("SPY", pd.DataFrame())
    if spy_raw.empty:
        print("  !! SPY data missing — aborting")
        sys.exit(2)

    # Drop tickers with too little history for the train window.
    min_bars_needed = 252 + 200  # 52w lookback + 200d SMA
    prices_raw = {t: df for t, df in prices_raw.items() if len(df) >= min_bars_needed}
    print(f"  -> {len(prices_raw)} tickers after history filter (>={min_bars_needed} bars)")

    # ---- 3) earnings ----
    if args.skip_earnings:
        print("[3/10] skipping earnings (diagnostic)")
        earnings_map = {t: [] for t in prices_raw}
    else:
        print(f"[3/10] fetching earnings dates for {len(prices_raw)} tickers...")
        earnings_map = {}
        t0 = time.time()
        for i, t in enumerate(prices_raw, 1):
            try:
                earnings_map[t] = fetch_earnings_dates(t)
            except Exception:
                earnings_map[t] = []
            if i % 50 == 0:
                print(f"     ...{i}/{len(prices_raw)} ({time.time() - t0:.0f}s)")
        print(f"  -> {sum(len(v) > 0 for v in earnings_map.values())} tickers with earnings data")

    # ---- 4) indicators ----
    print("[4/10] preparing indicators...")
    prices = {t: prepare_ticker_indicators(df) for t, df in prices_raw.items()}
    spy = prepare_spy(spy_raw)
    rs_rank = compute_rs_rank(prices)
    print(f"  -> rs_rank shape: {rs_rank.shape}")

    # ---- 5) breadth ----
    print("[5/10] computing breadth_200...")
    breadth = compute_breadth_above_ma(prices_raw, ma_len=200)
    print(f"  -> breadth series: {len(breadth)} days, range "
          f"[{breadth.min():.2f}, {breadth.max():.2f}]")

    # ---- 6) signals & backtest: TRAIN ----
    print(f"[6/10] TRAIN signals: {TRAIN_START} -> {TRAIN_END}")
    train_start = pd.Timestamp(TRAIN_START)
    train_end = pd.Timestamp(TRAIN_END)
    train_signals, train_skips = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        train_start, train_end,
    )
    print(f"  -> {len(train_signals)} train signals, {len(train_skips)} train setup-skips")

    train_trades, train_cap_skips = run_backtest(
        train_signals, prices, spy, earnings_map, train_start, train_end)
    print(f"  -> {len(train_trades)} train trades, {len(train_cap_skips)} concurrent-cap skips")
    train_all_skips = train_skips + train_cap_skips

    # ---- 7) TEST ----
    print(f"[7/10] TEST signals: {TEST_START} -> {TEST_END}")
    test_start = pd.Timestamp(TEST_START)
    test_end = _resolve_end(TEST_END)
    test_signals, test_skips = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end,
    )
    print(f"  -> {len(test_signals)} test signals, {len(test_skips)} test setup-skips")

    test_trades, test_cap_skips = run_backtest(
        test_signals, prices, spy, earnings_map, test_start, test_end)
    print(f"  -> {len(test_trades)} test trades, {len(test_cap_skips)} concurrent-cap skips")
    test_all_skips = test_skips + test_cap_skips

    # ---- 8) ABLATION: 2R on same entries as baseline test ----
    print("[8/10] ablation: 2R fixed exit on test-window entries...")
    entries_for_ablation = _positions_from_trades(test_trades)
    ablation_trades = run_ablation(entries_for_ablation, prices, earnings_map, test_end)
    print(f"  -> {len(ablation_trades)} ablation trades")

    # ---- 9) correlation gate ----
    print("[9/10] correlation gate vs SID + S&D-long...")
    sid_excel = _latest_file("sid_method_backtest_")
    sd_excel = _latest_file("sd_method_backtest_")
    print(f"  SID log: {sid_excel}")
    print(f"  S&D log: {sd_excel}")
    gate = correlation_gate(test_trades, sid_excel, sd_excel)

    # ---- 10) summarize, write outputs ----
    print("[10/10] writing outputs...")
    train_sum = summarize_trades(train_trades, "train")
    test_sum = summarize_trades(test_trades, "test")
    abl_sum = summarize_trades(ablation_trades, "ablation_2r")

    train_eq = equity_curve(train_trades)
    test_eq = equity_curve(test_trades)
    train_dd = drawdown(train_eq) if len(train_eq) > 1 else 0.0
    test_dd = drawdown(test_eq) if len(test_eq) > 1 else 0.0
    train_cagr = cagr(train_eq)
    test_cagr = cagr(test_eq)
    train_sharpe = sharpe(train_eq)
    test_sharpe = sharpe(test_eq)

    train_top = skip_counts(train_all_skips)
    test_top = skip_counts(test_all_skips)

    delta = train_test_delta(train_sum, test_sum)

    # write files
    xlsx_path = write_excel(train_trades + test_trades, ablation_trades, "train_test")
    train_skip_path = write_skip_csv(train_all_skips, "train")
    test_skip_path = write_skip_csv(test_all_skips, "test")

    # dump metrics JSON for the results doc
    metrics = {
        "train": train_sum,
        "test": test_sum,
        "ablation_2r": abl_sum,
        "train_dd": train_dd,
        "test_dd": test_dd,
        "train_cagr": train_cagr,
        "test_cagr": test_cagr,
        "train_sharpe": train_sharpe,
        "test_sharpe": test_sharpe,
        "train_test_delta": delta,
        "correlation_gate": gate,
        "top_skips_train": train_top.head(20).to_dict(orient="records"),
        "top_skips_test": test_top.head(20).to_dict(orient="records"),
        "files": {
            "excel": xlsx_path,
            "train_skip_csv": train_skip_path,
            "test_skip_csv": test_skip_path,
            "sid_excel": sid_excel,
            "sd_excel": sd_excel,
        },
        "universe_size": len(prices),
        "runtime_sec": round(time.time() - run_start, 1),
    }
    metrics_path = os.path.join(OUTPUT_DIR, "breakout_v1_metrics.json")
    with open(metrics_path, "w") as f:
        def _default(o):
            if isinstance(o, (pd.Timestamp,)):
                return o.isoformat()
            raise TypeError
        json.dump(metrics, f, indent=2, default=_default)

    print_summary(train_sum, test_sum, abl_sum, gate,
                  train_dd, test_dd, train_cagr, test_cagr,
                  train_sharpe, test_sharpe,
                  train_top, test_top)
    print(f"\nwrote: {xlsx_path}")
    print(f"wrote: {train_skip_path}")
    print(f"wrote: {test_skip_path}")
    print(f"wrote: {metrics_path}")
    print(f"train->test WR delta: {delta.get('wr_delta_pp')}")
    print(f"runtime: {metrics['runtime_sec']}s")


if __name__ == "__main__":
    main()
