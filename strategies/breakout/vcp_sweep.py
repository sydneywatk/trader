"""Phase 3 — VCP entry-filter sweep.

Three runs on test window:
  P3a: v1 filters + VCP + E2 exit
  P3b: v1 filters + VCP + D3 (E5) exit
  P3c: v1 filters + NO VCP + E2 exit (isolates VCP effect)

Metrics per run: WR, expectancy, CAGR, max DD, MFE-capture %, winner MFE
median, trade count. Plus VCP skip-reason aggregation.

Pass criteria for advancing VCP to v2:
  - Expectancy > +0.25R on at least one VCP variant
  - Trade count >= 50
  - Median MFE on winners > 1.5R
"""

import json
import os
import sys
import time
from collections import Counter
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


def _mfe_stats(trades: list[Trade], prices: dict[str, pd.DataFrame]) -> dict:
    risk_dollars = ACCOUNT_SIZE * RISK_PCT
    cap_ratios = []
    winner_mfe_rs = []
    all_mfe_rs = []
    for t in trades:
        df = prices.get(t.ticker)
        if df is None or df.empty:
            continue
        sub = df.loc[t.entry_date:t.exit_date]
        if sub.empty:
            continue
        r_per_share = max(t.entry_price - t.stop_price, 1e-9)
        mfe_r = float(sub["High"].max() - t.entry_price) / r_per_share
        all_mfe_rs.append(mfe_r)
        if t.win_loss == "Win" and mfe_r > 0:
            cap_ratios.append((t.total_pnl / risk_dollars) / mfe_r)
            winner_mfe_rs.append(mfe_r)
    res = {}
    if cap_ratios:
        arr = np.array(cap_ratios)
        res["mfe_capture_pct_mean"] = float(arr.mean() * 100)
        res["mfe_capture_pct_median"] = float(np.median(arr) * 100)
    if winner_mfe_rs:
        w = np.array(winner_mfe_rs)
        res["winner_mfe_r_median"] = float(np.median(w))
        res["winner_mfe_r_mean"] = float(w.mean())
        res["winner_mfe_r_p90"] = float(np.percentile(w, 90))
    if all_mfe_rs:
        res["all_mfe_r_median"] = float(np.median(all_mfe_rs))
    return res


def summarize(label: str, trades: list[Trade], prices: dict[str, pd.DataFrame]) -> dict:
    if not trades:
        return {"label": label, "n_trades": 0}
    s = summarize_trades(trades, label)
    eq = equity_curve(trades)
    s["max_dd"] = drawdown(eq) if len(eq) > 1 else 0.0
    s["cagr"] = cagr(eq)
    s.update(_mfe_stats(trades, prices))
    # exit reasons
    er: Counter = Counter(t.exit_reason for t in trades)
    s["exit_reasons"] = dict(er)
    return s


def main():
    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[1/6] loading universe + data + earnings + indicators...")
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

    # ---- P3c: no-VCP + E2 exit (control — isolates VCP effect) -----------
    print("\n[2/6] P3c control: v1 filters + NO VCP + E2 exit...")
    ctrl_signals, ctrl_skips = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end,
        vol_mult=VOL_MULT, use_breadth=True, use_vcp=False,
    )
    print(f"  -> {len(ctrl_signals)} signals (no VCP)")
    ctrl_raw, _ = run_backtest(
        ctrl_signals, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    print(f"  -> {len(ctrl_raw)} entries captured from v1 exit")
    ctrl_entries = _positions_from_trades(ctrl_raw)
    p3c_trades = run_variant(ctrl_entries, prices, atrs, earnings_map, test_end, "E2")
    p3c = summarize("P3c_noVCP_E2", p3c_trades, prices)
    print(f"  P3c: n={p3c['n_trades']:>3} WR={p3c['win_rate']:.2%} "
          f"exp={p3c['expectancy_r']:+.3f}R DD={p3c['max_dd']:+.2%} "
          f"MFE_cap={p3c.get('mfe_capture_pct_mean', 0):.1f}%")

    # ---- VCP-filtered signals -----------
    print("\n[3/6] generating signals with VCP enabled...")
    vcp_signals, vcp_skips = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end,
        vol_mult=VOL_MULT, use_breadth=True, use_vcp=True,
    )
    print(f"  -> {len(vcp_signals)} signals (VCP-filtered)")

    # VCP-specific skip counts
    vcp_reason_counts = Counter()
    for s in vcp_skips:
        r = s.get("filter_name", "")
        if r.startswith("vcp_"):
            vcp_reason_counts[r] += 1
    print("  VCP rejection reasons:")
    for k, v in vcp_reason_counts.most_common():
        print(f"     {k:<36s} {v:>6d}")

    # ---- P3a: VCP + E2 -----------
    print("\n[4/6] P3a: VCP + E2 exit...")
    vcp_raw, _ = run_backtest(
        vcp_signals, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    print(f"  -> {len(vcp_raw)} VCP entries")
    vcp_entries = _positions_from_trades(vcp_raw)
    p3a_trades = run_variant(vcp_entries, prices, atrs, earnings_map, test_end, "E2")
    p3a = summarize("P3a_VCP_E2", p3a_trades, prices)
    print(f"  P3a: n={p3a['n_trades']:>3} WR={p3a['win_rate']:.2%} "
          f"exp={p3a['expectancy_r']:+.3f}R DD={p3a['max_dd']:+.2%} "
          f"MFE_cap={p3a.get('mfe_capture_pct_mean', 0):.1f}% "
          f"winner_MFE_med={p3a.get('winner_mfe_r_median', 0):.2f}R")

    # ---- P3b: VCP + E5 (D3) -----------
    print("\n[5/6] P3b: VCP + D3 (E5) exit...")
    p3b_trades = run_variant(vcp_entries, prices, atrs, earnings_map, test_end, "E5")
    p3b = summarize("P3b_VCP_E5", p3b_trades, prices)
    print(f"  P3b: n={p3b['n_trades']:>3} WR={p3b['win_rate']:.2%} "
          f"exp={p3b['expectancy_r']:+.3f}R DD={p3b['max_dd']:+.2%} "
          f"MFE_cap={p3b.get('mfe_capture_pct_mean', 0):.1f}% "
          f"winner_MFE_med={p3b.get('winner_mfe_r_median', 0):.2f}R")

    # ---- Pass criteria check -----------
    print("\n[6/6] pass-criteria check:")
    PASS_EXP = 0.25
    PASS_N = 50
    PASS_MFE = 1.5

    def _check(pack):
        if pack.get("n_trades", 0) == 0:
            return False, "no trades"
        exp_ok = pack["expectancy_r"] > PASS_EXP
        n_ok = pack["n_trades"] >= PASS_N
        mfe_ok = pack.get("winner_mfe_r_median", 0) > PASS_MFE
        reasons = []
        if not exp_ok:
            reasons.append(f"exp {pack['expectancy_r']:+.3f}R ≤ {PASS_EXP}R")
        if not n_ok:
            reasons.append(f"n_trades {pack['n_trades']} < {PASS_N}")
        if not mfe_ok:
            reasons.append(f"median winner MFE {pack.get('winner_mfe_r_median', 0):.2f}R ≤ {PASS_MFE}R")
        return (exp_ok and n_ok and mfe_ok), "; ".join(reasons)

    for lbl, pack in [("P3a", p3a), ("P3b", p3b)]:
        ok, why = _check(pack)
        status = "PASS" if ok else "FAIL"
        print(f"  {lbl}: {status}  ({why if not ok else 'all gates cleared'})")

    # ---- dump -----------
    metrics = {
        "P3a_VCP_E2": p3a,
        "P3b_VCP_E5": p3b,
        "P3c_noVCP_E2": p3c,
        "vcp_filter_counts": dict(vcp_reason_counts),
        "signal_counts": {
            "with_vcp": len(vcp_signals),
            "without_vcp": len(ctrl_signals),
        },
        "n_vcp_entries": len(vcp_entries),
        "n_ctrl_entries": len(ctrl_entries),
        "pass_criteria": {
            "expectancy_min": PASS_EXP,
            "n_trades_min": PASS_N,
            "winner_mfe_r_median_min": PASS_MFE,
        },
        "runtime_sec": round(time.time() - t0, 1),
    }
    path = os.path.join(OUTPUT_DIR, "breakout_vcp_sweep_metrics.json")
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"\nwrote: {path}")


if __name__ == "__main__":
    main()
