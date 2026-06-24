"""Phase 2 exit-variant sweep.

Re-runs v1 entries on the test window through E1..E5. Same entry list, same
hard stop (7%), same time stop (60), same earnings exit. Differ post-+1R.

Reports per variant:
  - n_trades, WR
  - avg winner R, avg loser R
  - expectancy R
  - CAGR, max DD
  - avg MFE-captured-% on winners (realized_R / MFE_R)
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from shared.config import OUTPUT_DIR
from shared.earnings import fetch_earnings_dates
from shared.breadth import compute_breadth_above_ma

from config import (
    DATA_START, TEST_START, TEST_END,
    VOL_MULT, MAX_CONCURRENT_POSITIONS, PARTIAL_R, TRAIL_MA_LEN,
    ACCOUNT_SIZE, RISK_PCT,
)
from universe import load_universe
from data import bulk_fetch
from signals import prepare_ticker_indicators, prepare_spy, compute_rs_rank, generate_signals
from backtest import run_backtest, Position, Trade
from instrumentation import compute_atr
from exit_variants import run_variant
from output import summarize_trades, equity_curve, drawdown, cagr


def _resolve_end(end_str: str) -> pd.Timestamp:
    if end_str == "today":
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(end_str)


def _positions_from_trades(trades: list[Trade]) -> list[Position]:
    out = []
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


def mfe_captured_stats(trades: list[Trade], prices: dict[str, pd.DataFrame]) -> dict:
    """Mean/median realized_R / MFE_R on winners only.

    MFE_R = (max(High) between entry_date and exit_date - entry_price) / R.
    realized_R = total_pnl / (ACCOUNT_SIZE * RISK_PCT).
    Captured = realized_R / MFE_R.
    Skip trades where MFE_R <= 0 (shouldn't happen for longs but guard anyway).
    """
    risk_dollars = ACCOUNT_SIZE * RISK_PCT
    ratios = []
    mfe_r_all = []
    realized_r_all = []
    for t in trades:
        if t.win_loss != "Win":
            continue
        df = prices.get(t.ticker)
        if df is None or df.empty:
            continue
        sub = df.loc[t.entry_date:t.exit_date]
        if sub.empty:
            continue
        r_per_share = max(t.entry_price - t.stop_price, 1e-9)
        mfe_r = float(sub["High"].max() - t.entry_price) / r_per_share
        if mfe_r <= 0:
            continue
        realized_r = t.total_pnl / risk_dollars
        ratios.append(realized_r / mfe_r)
        mfe_r_all.append(mfe_r)
        realized_r_all.append(realized_r)
    if not ratios:
        return {"n_winners": 0}
    arr = np.array(ratios)
    return {
        "n_winners": int(len(arr)),
        "mean_captured_pct": float(arr.mean() * 100),
        "median_captured_pct": float(np.median(arr) * 100),
        "p25_captured_pct": float(np.percentile(arr, 25) * 100),
        "p75_captured_pct": float(np.percentile(arr, 75) * 100),
        "mean_mfe_r": float(np.mean(mfe_r_all)),
        "mean_realized_r_winners": float(np.mean(realized_r_all)),
    }


def exit_reason_counts(trades: list[Trade]) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in trades:
        out[t.exit_reason] = out.get(t.exit_reason, 0) + 1
    return out


def summarize(label: str, trades: list[Trade], prices: dict[str, pd.DataFrame]) -> dict:
    if not trades:
        return {"label": label, "n_trades": 0}
    s = summarize_trades(trades, label)
    eq = equity_curve(trades)
    s["max_dd"] = drawdown(eq) if len(eq) > 1 else 0.0
    s["cagr"] = cagr(eq)
    s.update({f"mfe_{k}": v for k, v in mfe_captured_stats(trades, prices).items()})
    s["exit_reasons"] = exit_reason_counts(trades)
    return s


def main():
    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[1/5] loading universe + data + earnings + indicators...")
    tickers = load_universe()
    prices_raw = bulk_fetch(tickers + ["SPY"], start=DATA_START)
    spy_raw = prices_raw.pop("SPY", pd.DataFrame())
    min_bars = 252 + 200
    prices_raw = {t: df for t, df in prices_raw.items() if len(df) >= min_bars}

    earnings_map = {}
    for t in prices_raw:
        try:
            earnings_map[t] = fetch_earnings_dates(t)
        except Exception:
            earnings_map[t] = []

    prices = {t: prepare_ticker_indicators(df) for t, df in prices_raw.items()}
    spy = prepare_spy(spy_raw)
    rs_rank = compute_rs_rank(prices)
    breadth = compute_breadth_above_ma(prices_raw, ma_len=200)
    atrs = {t: compute_atr(df, n=14) for t, df in prices_raw.items()}
    print(f"  -> {len(prices)} tickers")

    test_start = pd.Timestamp(TEST_START)
    test_end = _resolve_end(TEST_END)

    print("[2/5] v1 signals + v1 backtest to capture entry list...")
    signals, _ = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end, vol_mult=VOL_MULT, use_breadth=True,
    )
    ctrl_trades, _ = run_backtest(
        signals, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    print(f"  -> {len(ctrl_trades)} v1 trades (control entry list)")
    entries = _positions_from_trades(ctrl_trades)

    results = {"v1_control": summarize("v1_control", ctrl_trades, prices)}

    print("[3/5] running exit variants E1..E5...")
    for variant in ("E1", "E2", "E3", "E4", "E5"):
        trades = run_variant(entries, prices, atrs, earnings_map, test_end, variant)
        r = summarize(variant, trades, prices)
        results[variant] = r
        mfe_mean = r.get("mfe_mean_captured_pct")
        mfe_str = f"MFE-cap={mfe_mean:.1f}%" if mfe_mean is not None else "MFE-cap=--"
        print(f"  {variant}: n={r['n_trades']:>3}  WR={r['win_rate']:.2%}  "
              f"exp={r['expectancy_r']:+.3f}R  DD={r['max_dd']:+.2%}  {mfe_str}")

    print("[4/5] dumping metrics...")
    results["runtime_sec"] = round(time.time() - t0, 1)
    path = os.path.join(OUTPUT_DIR, "breakout_exit_sweep_metrics.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"  wrote: {path}")

    print("[5/5] verdict:")
    exp_by = {k: v["expectancy_r"] for k, v in results.items()
              if isinstance(v, dict) and "expectancy_r" in v}
    best = max(exp_by.items(), key=lambda kv: kv[1])
    d3_exp = exp_by.get("E5", 0.0)
    margin = best[1] - d3_exp
    print(f"  best: {best[0]} @ {best[1]:+.3f}R   D3(E5) @ {d3_exp:+.3f}R   margin={margin:+.3f}R")
    if margin > 0.05:
        print(f"  -> {best[0]} beats D3 by >0.05R. Worth adopting.")
    else:
        print(f"  -> Margin {margin:+.3f}R ≤ 0.05R. Exits at ceiling; move to entry improvements.")


if __name__ == "__main__":
    main()
