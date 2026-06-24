"""Breakout v1 instrumentation — test window.

Pure measurement. No strategy rule changes.

Adds:
  1. Per-trade MFE / MAE / bars-to-MFE (post-hoc from price data).
  2. Time-stop autopsy: for trades that exited on 60-day time_stop, simulate
     phantom exits (a) close < SMA200, (b) 2*ATR trailing stop. Report deltas.

Re-runs v1 on the test window (2019-07 → today) and writes:
  - output/breakout_v1_instrumented_YYYYMMDD.xlsx (full trade log + MFE/MAE)
  - output/breakout_v1_instrumentation.json (metrics dump for results doc)
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
    VOL_MULT, MAX_CONCURRENT_POSITIONS,
    PARTIAL_R, TRAIL_MA_LEN,
)
from universe import load_universe
from data import bulk_fetch
from signals import (
    prepare_ticker_indicators, prepare_spy, compute_rs_rank,
    generate_signals,
)
from backtest import run_backtest, Trade


def _resolve_end(end_str: str) -> pd.Timestamp:
    if end_str == "today":
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(end_str)


def compute_atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """True-range-based ATR(n). Vectorized, no look-ahead."""
    prev_close = df["Close"].shift(1)
    tr = pd.concat([
        (df["High"] - df["Low"]).abs(),
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(n, min_periods=n).mean()


def _r_per_share(t: Trade) -> float:
    return max(t.entry_price - t.stop_price, 1e-9)


def compute_excursions(trades: list[Trade],
                        prices: dict[str, pd.DataFrame]) -> list[dict]:
    """MFE / MAE / bars-to-MFE per trade, post-hoc from OHLC."""
    rows = []
    for t in trades:
        df = prices.get(t.ticker)
        if df is None or df.empty:
            continue
        sub = df.loc[t.entry_date:t.exit_date]
        if sub.empty:
            continue
        mfe_price = float(sub["High"].max())
        mae_price = float(sub["Low"].min())
        r = _r_per_share(t)
        mfe_r = (mfe_price - t.entry_price) / r
        mae_r = (t.entry_price - mae_price) / r
        # Bars from entry to MFE peak. idxmax returns first occurrence.
        mfe_date = sub["High"].idxmax()
        bars_to_mfe = int(sub.index.get_loc(mfe_date))
        rows.append({
            "ticker": t.ticker,
            "entry_date": pd.Timestamp(t.entry_date).normalize().isoformat(),
            "exit_date": pd.Timestamp(t.exit_date).normalize().isoformat(),
            "exit_reason": t.exit_reason,
            "entry_price": round(t.entry_price, 4),
            "stop_price": round(t.stop_price, 4),
            "exit_price": round(t.exit_price, 4),
            "mfe_price": round(mfe_price, 4),
            "mae_price": round(mae_price, 4),
            "mfe_r": round(mfe_r, 4),
            "mae_r": round(mae_r, 4),
            "bars_to_mfe": bars_to_mfe,
            "duration_days": t.duration_days,
            "trade_rr": round(t.trade_rr, 4),
            "total_pnl": round(t.total_pnl, 2),
            "win_loss": t.win_loss,
        })
    return rows


def phantom_sma200(df: pd.DataFrame, entry_date: pd.Timestamp,
                    entry_price: float, stop_price: float,
                    shares: int) -> dict:
    """Simulate: hold from entry, exit on first close < sma_200 or hard stop."""
    sub = df.loc[entry_date:]
    if sub.empty:
        return {"exit_reason": "no_data", "exit_date": None,
                "exit_price": entry_price, "pnl": 0.0, "duration_days": 0}
    for date, row in sub.iterrows():
        if row["Low"] <= stop_price:
            return {
                "exit_reason": "phantom_hard_stop",
                "exit_date": pd.Timestamp(date).normalize().isoformat(),
                "exit_price": round(float(stop_price), 4),
                "pnl": round(shares * (stop_price - entry_price), 2),
                "duration_days": int((date - entry_date).days),
            }
        s200 = row.get("sma_200", float("nan"))
        if not pd.isna(s200) and row["Close"] < s200:
            return {
                "exit_reason": "phantom_below_sma200",
                "exit_date": pd.Timestamp(date).normalize().isoformat(),
                "exit_price": round(float(row["Close"]), 4),
                "pnl": round(shares * (row["Close"] - entry_price), 2),
                "duration_days": int((date - entry_date).days),
            }
    # Ran off the end of data
    last = sub.iloc[-1]
    last_date = sub.index[-1]
    return {
        "exit_reason": "phantom_data_end",
        "exit_date": pd.Timestamp(last_date).normalize().isoformat(),
        "exit_price": round(float(last["Close"]), 4),
        "pnl": round(shares * (last["Close"] - entry_price), 2),
        "duration_days": int((last_date - entry_date).days),
    }


def phantom_atr_trail(df: pd.DataFrame, atr: pd.Series,
                       entry_date: pd.Timestamp,
                       entry_price: float, stop_price: float,
                       shares: int, atr_mult: float = 2.0) -> dict:
    """Simulate: 2*ATR trailing stop from entry. Hard stop as absolute floor.

    Trail starts at max(hard_stop, entry - atr_mult*ATR_at_entry). Each bar:
      1. If low <= trail -> exit at trail.
      2. Ratchet: trail = max(trail, close - atr_mult*ATR).
    """
    sub = df.loc[entry_date:]
    atr_sub = atr.reindex(sub.index)
    if sub.empty or atr_sub.empty or pd.isna(atr_sub.iloc[0]):
        return {"exit_reason": "no_atr", "exit_date": None,
                "exit_price": entry_price, "pnl": 0.0, "duration_days": 0,
                "trail_final": None}
    trail = max(stop_price, entry_price - atr_mult * float(atr_sub.iloc[0]))
    for i, (date, row) in enumerate(sub.iterrows()):
        if row["Low"] <= trail:
            return {
                "exit_reason": "phantom_atr_stop",
                "exit_date": pd.Timestamp(date).normalize().isoformat(),
                "exit_price": round(float(trail), 4),
                "pnl": round(shares * (trail - entry_price), 2),
                "duration_days": int((date - entry_date).days),
                "trail_final": round(float(trail), 4),
            }
        a = atr_sub.iloc[i]
        if not pd.isna(a):
            new_trail = float(row["Close"]) - atr_mult * float(a)
            if new_trail > trail:
                trail = new_trail
    last = sub.iloc[-1]
    last_date = sub.index[-1]
    return {
        "exit_reason": "phantom_data_end",
        "exit_date": pd.Timestamp(last_date).normalize().isoformat(),
        "exit_price": round(float(last["Close"]), 4),
        "pnl": round(shares * (last["Close"] - entry_price), 2),
        "duration_days": int((last_date - entry_date).days),
        "trail_final": round(float(trail), 4),
    }


def time_stop_autopsy(trades: list[Trade],
                      prices: dict[str, pd.DataFrame],
                      atrs: dict[str, pd.Series]) -> list[dict]:
    """For every time-stop exit, compute phantom (a) SMA200-cross and (b) 2*ATR trail."""
    out = []
    for t in trades:
        if t.exit_reason != "time_stop":
            continue
        df = prices.get(t.ticker)
        atr = atrs.get(t.ticker)
        if df is None or atr is None:
            continue
        pa = phantom_sma200(df, t.entry_date, t.entry_price, t.stop_price, t.initial_shares)
        pb = phantom_atr_trail(df, atr, t.entry_date, t.entry_price, t.stop_price,
                                t.initial_shares, atr_mult=2.0)
        r_per_share = _r_per_share(t)
        risk_dollars = t.initial_shares * r_per_share
        out.append({
            "ticker": t.ticker,
            "entry_date": pd.Timestamp(t.entry_date).normalize().isoformat(),
            "actual_exit_date": pd.Timestamp(t.exit_date).normalize().isoformat(),
            "actual_exit_price": round(t.exit_price, 4),
            "actual_pnl": round(t.total_pnl, 2),
            "actual_rr": round(t.total_pnl / risk_dollars, 3) if risk_dollars else 0,
            "actual_duration_days": t.duration_days,
            "phantom_a_sma200_exit_date": pa["exit_date"],
            "phantom_a_sma200_exit_price": pa["exit_price"],
            "phantom_a_sma200_pnl": pa["pnl"],
            "phantom_a_sma200_rr": round(pa["pnl"] / risk_dollars, 3) if risk_dollars else 0,
            "phantom_a_sma200_duration": pa["duration_days"],
            "phantom_a_sma200_reason": pa["exit_reason"],
            "phantom_b_atr_exit_date": pb["exit_date"],
            "phantom_b_atr_exit_price": pb["exit_price"],
            "phantom_b_atr_pnl": pb["pnl"],
            "phantom_b_atr_rr": round(pb["pnl"] / risk_dollars, 3) if risk_dollars else 0,
            "phantom_b_atr_duration": pb["duration_days"],
            "phantom_b_atr_reason": pb["exit_reason"],
        })
    return out


def histogram_counts(values: list[float], bins: list[float]) -> list[tuple[str, int]]:
    """Return [(label, count)] for the given values binned by edges."""
    if not values:
        return []
    counts = [0] * (len(bins) - 1)
    for v in values:
        if v < bins[0]:
            counts[0] += 1
            continue
        if v >= bins[-1]:
            counts[-1] += 1
            continue
        for i in range(len(bins) - 1):
            if bins[i] <= v < bins[i + 1]:
                counts[i] += 1
                break
    labels = []
    for i in range(len(bins) - 1):
        lo = "-∞" if i == 0 and bins[0] == float("-inf") else f"{bins[i]:>4.1f}"
        hi = "∞" if bins[i + 1] == float("inf") else f"{bins[i + 1]:>4.1f}"
        labels.append(f"[{lo}, {hi})")
    return list(zip(labels, counts))


def main():
    run_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[1/7] loading universe + data...")
    tickers = load_universe()
    prices_raw = bulk_fetch(tickers + ["SPY"], start=DATA_START)
    spy_raw = prices_raw.pop("SPY", pd.DataFrame())
    min_bars = 252 + 200
    prices_raw = {t: df for t, df in prices_raw.items() if len(df) >= min_bars}
    print(f"  -> {len(prices_raw)} tickers")

    print("[2/7] earnings...")
    earnings_map = {t: [] for t in prices_raw}
    for t in prices_raw:
        try:
            earnings_map[t] = fetch_earnings_dates(t)
        except Exception:
            pass

    print("[3/7] indicators, rs_rank, spy, breadth, atr...")
    prices = {t: prepare_ticker_indicators(df) for t, df in prices_raw.items()}
    spy = prepare_spy(spy_raw)
    rs_rank = compute_rs_rank(prices)
    breadth = compute_breadth_above_ma(prices_raw, ma_len=200)
    # Per-ticker ATR(14) computed on raw OHLC (same source as backtest logic).
    atrs = {t: compute_atr(df, n=14) for t, df in prices_raw.items()}

    test_start = pd.Timestamp(TEST_START)
    test_end = _resolve_end(TEST_END)
    print(f"  test window: {test_start.date()} -> {test_end.date()}")

    print("[4/7] generating v1 signals...")
    signals, skips = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end,
        vol_mult=VOL_MULT, use_breadth=True,
    )
    print(f"  -> {len(signals)} signals")

    print("[5/7] running v1 backtest (unchanged rules)...")
    trades, cap_skips = run_backtest(
        signals, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    print(f"  -> {len(trades)} trades")

    print("[6/7] computing MFE/MAE/bars-to-MFE per trade...")
    excursions = compute_excursions(trades, prices)

    print("[7/7] time-stop autopsy...")
    autopsy = time_stop_autopsy(trades, prices, atrs)
    print(f"  -> {len(autopsy)} time-stop trades examined")

    # ---- Aggregates ----
    winners_mfe = [r["mfe_r"] for r in excursions if r["win_loss"] == "Win"]
    losers_mfe = [r["mfe_r"] for r in excursions if r["win_loss"] == "Loss"]
    winners_mae = [r["mae_r"] for r in excursions if r["win_loss"] == "Win"]
    losers_mae = [r["mae_r"] for r in excursions if r["win_loss"] == "Loss"]

    mfe_bins = [0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 7.0, float("inf")]
    mae_bins = [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0, float("inf")]
    bars_to_mfe_all = [r["bars_to_mfe"] for r in excursions]

    def _summary(vals):
        if not vals:
            return {}
        arr = np.array(vals)
        return {
            "n": int(len(arr)),
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "p25": float(np.percentile(arr, 25)),
            "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90)),
            "max": float(arr.max()),
            "min": float(arr.min()),
        }

    # ---- Write Excel ----
    ts = pd.Timestamp.now().strftime("%Y%m%d")
    xlsx = os.path.join(OUTPUT_DIR, f"breakout_v1_instrumented_{ts}.xlsx")
    with pd.ExcelWriter(xlsx, engine="openpyxl") as w:
        pd.DataFrame(excursions).to_excel(w, sheet_name="Trades MFE MAE", index=False)
        if autopsy:
            pd.DataFrame(autopsy).to_excel(w, sheet_name="Time-stop Autopsy", index=False)
    print(f"wrote: {xlsx}")

    # ---- Autopsy aggregates ----
    def _autopsy_agg(key: str) -> dict:
        vals = [r[key] for r in autopsy]
        if not vals:
            return {}
        arr = np.array(vals, dtype=float)
        return {
            "n": int(len(arr)),
            "sum": float(arr.sum()),
            "mean": float(arr.mean()),
            "median": float(np.median(arr)),
            "min": float(arr.min()),
            "max": float(arr.max()),
        }

    # ---- Dump JSON ----
    metrics = {
        "universe_size": len(prices),
        "test_window": {"start": test_start.isoformat(), "end": test_end.isoformat()},
        "n_trades": len(trades),
        "winners": int(sum(1 for r in excursions if r["win_loss"] == "Win")),
        "losers": int(sum(1 for r in excursions if r["win_loss"] == "Loss")),
        "mfe_summary": {
            "winners": _summary(winners_mfe),
            "losers": _summary(losers_mfe),
            "all": _summary([r["mfe_r"] for r in excursions]),
        },
        "mae_summary": {
            "winners": _summary(winners_mae),
            "losers": _summary(losers_mae),
            "all": _summary([r["mae_r"] for r in excursions]),
        },
        "bars_to_mfe_summary": _summary(bars_to_mfe_all),
        "mfe_hist_winners": histogram_counts(winners_mfe, mfe_bins),
        "mfe_hist_losers": histogram_counts(losers_mfe, mfe_bins),
        "mae_hist_winners": histogram_counts(winners_mae, mae_bins),
        "mae_hist_losers": histogram_counts(losers_mae, mae_bins),
        "n_time_stops": len(autopsy),
        "autopsy": autopsy,
        "autopsy_agg": {
            "actual_pnl": _autopsy_agg("actual_pnl"),
            "phantom_a_sma200_pnl": _autopsy_agg("phantom_a_sma200_pnl"),
            "phantom_b_atr_pnl": _autopsy_agg("phantom_b_atr_pnl"),
            "actual_rr": _autopsy_agg("actual_rr"),
            "phantom_a_sma200_rr": _autopsy_agg("phantom_a_sma200_rr"),
            "phantom_b_atr_rr": _autopsy_agg("phantom_b_atr_rr"),
        },
        "exit_reason_counts": {},
        "xlsx_path": xlsx,
        "runtime_sec": round(time.time() - run_start, 1),
    }
    for t in trades:
        metrics["exit_reason_counts"][t.exit_reason] = metrics["exit_reason_counts"].get(t.exit_reason, 0) + 1

    json_path = os.path.join(OUTPUT_DIR, "breakout_v1_instrumentation.json")
    with open(json_path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"wrote: {json_path}")
    print(f"runtime: {metrics['runtime_sec']}s")

    # Console summary
    print("\n" + "=" * 72)
    print("INSTRUMENTATION SUMMARY — v1 test window")
    print("=" * 72)
    print(f"trades: {len(trades)}   winners: {metrics['winners']}   losers: {metrics['losers']}")
    print(f"time-stop exits: {len(autopsy)}")
    mfe_w = metrics["mfe_summary"]["winners"]
    mfe_l = metrics["mfe_summary"]["losers"]
    if mfe_w:
        print(f"winners MFE_R: median={mfe_w['median']:.2f}, mean={mfe_w['mean']:.2f}, p90={mfe_w['p90']:.2f}")
    if mfe_l:
        print(f"losers  MFE_R: median={mfe_l['median']:.2f}, mean={mfe_l['mean']:.2f}, p90={mfe_l['p90']:.2f}")
    if autopsy:
        ag = metrics["autopsy_agg"]
        print(f"autopsy: actual sum ${ag['actual_pnl']['sum']:+,.0f}   "
              f"phantom_a ${ag['phantom_a_sma200_pnl']['sum']:+,.0f}   "
              f"phantom_b ${ag['phantom_b_atr_pnl']['sum']:+,.0f}")


if __name__ == "__main__":
    main()
