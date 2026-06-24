"""Phase 4 — Russell 1000 universe sweep.

Three runs on test window:
  P4a: R1K + v1 filters + E2 exit (no VCP)     -- universe effect vs P3c
  P4b: R1K + v1 filters + D3 (E5) exit (no VCP)
  P4c: R1K + v1 filters + LOOSENED VCP + E2 exit
       (tightening 15%, base 90 bars — was 30% / 60)

Pass criteria for advancing R1K to v2:
  - Expectancy > +0.25R on at least one variant
  - Winner MFE median > 1.5R
  - Trade count >= 100
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
from universe_r1k import load_universe as load_r1k
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
    cap_ratios, winner_mfe_rs, all_mfe_rs = [], [], []
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
    er = Counter(t.exit_reason for t in trades)
    s["exit_reasons"] = dict(er)
    return s


def main():
    t0 = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("[1/8] loading Russell 1000 universe...")
    tickers, as_of = load_r1k()
    print(f"  -> {len(tickers)} R1K tickers (IWB as of {as_of})")

    print("[2/8] bulk-fetching daily data...")
    prices_raw = bulk_fetch(tickers + ["SPY"], start=DATA_START)
    spy_raw = prices_raw.pop("SPY", pd.DataFrame())
    # Data-quality bookkeeping
    attempted = len(tickers)
    with_data = len(prices_raw)
    min_bars = 252 + 200
    too_short = {t: len(df) for t, df in prices_raw.items() if len(df) < min_bars}
    prices_raw = {t: df for t, df in prices_raw.items() if len(df) >= min_bars}
    kept = len(prices_raw)
    print(f"  -> attempted={attempted}  with_data={with_data}  kept>={min_bars}bars={kept}  "
          f"dropped_short={len(too_short)}  missing={attempted - with_data}")

    print("[3/8] earnings...")
    earnings_map = {}
    for t in prices_raw:
        try:
            earnings_map[t] = fetch_earnings_dates(t)
        except Exception:
            earnings_map[t] = []

    print("[4/8] indicators + rs_rank + spy + breadth + atr...")
    prices = {t: prepare_ticker_indicators(df) for t, df in prices_raw.items()}
    spy = prepare_spy(spy_raw)
    rs_rank = compute_rs_rank(prices)
    breadth = compute_breadth_above_ma(prices_raw, ma_len=200)
    atrs = {t: compute_atr(df, n=14) for t, df in prices_raw.items()}

    test_start = pd.Timestamp(TEST_START)
    test_end = _resolve_end(TEST_END)

    # ---- P4a: R1K + v1 filters + E2 exit ----
    print("\n[5/8] P4a: R1K + v1 filters + E2 exit (no VCP)...")
    sig_a, skips_a = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end, vol_mult=VOL_MULT, use_breadth=True, use_vcp=False,
    )
    print(f"  -> {len(sig_a)} signals")
    raw_a, _ = run_backtest(
        sig_a, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    entries_a = _positions_from_trades(raw_a)
    p4a_trades = run_variant(entries_a, prices, atrs, earnings_map, test_end, "E2")
    p4a = summarize("P4a_R1K_E2", p4a_trades, prices)
    print(f"  P4a: n={p4a['n_trades']:>4} WR={p4a['win_rate']:.2%} "
          f"exp={p4a['expectancy_r']:+.3f}R DD={p4a['max_dd']:+.2%} "
          f"winner_MFE_med={p4a.get('winner_mfe_r_median', 0):.2f}R "
          f"MFE_cap={p4a.get('mfe_capture_pct_mean', 0):.1f}%")

    # ---- P4b: same entries as P4a, D3 exit ----
    print("\n[6/8] P4b: R1K + v1 filters + D3 exit (no VCP)...")
    p4b_trades = run_variant(entries_a, prices, atrs, earnings_map, test_end, "E5")
    p4b = summarize("P4b_R1K_D3", p4b_trades, prices)
    print(f"  P4b: n={p4b['n_trades']:>4} WR={p4b['win_rate']:.2%} "
          f"exp={p4b['expectancy_r']:+.3f}R DD={p4b['max_dd']:+.2%} "
          f"winner_MFE_med={p4b.get('winner_mfe_r_median', 0):.2f}R "
          f"MFE_cap={p4b.get('mfe_capture_pct_mean', 0):.1f}%")

    # ---- P4c: loosened VCP + E2 ----
    print("\n[7/8] P4c: R1K + loosened VCP (15% tight, 90-bar base) + E2...")
    sig_c, skips_c = generate_signals(
        prices, spy, breadth, rs_rank, earnings_map,
        test_start, test_end,
        vol_mult=VOL_MULT, use_breadth=True, use_vcp=True,
        vcp_base_lookback=90, vcp_tightening_pct=0.15,
    )
    vcp_reasons = Counter()
    for s in skips_c:
        r = s.get("filter_name", "")
        if r.startswith("vcp_"):
            vcp_reasons[r] += 1
    print(f"  -> {len(sig_c)} VCP-filtered signals")
    print("  VCP rejection reasons (loosened):")
    for k, v in vcp_reasons.most_common():
        print(f"     {k:<36s} {v:>6d}")

    raw_c, _ = run_backtest(
        sig_c, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    entries_c = _positions_from_trades(raw_c)
    p4c_trades = run_variant(entries_c, prices, atrs, earnings_map, test_end, "E2")
    p4c = summarize("P4c_R1K_VCPloose_E2", p4c_trades, prices)
    print(f"  P4c: n={p4c['n_trades']:>4} WR={p4c['win_rate']:.2%} "
          f"exp={p4c['expectancy_r']:+.3f}R DD={p4c['max_dd']:+.2%} "
          f"winner_MFE_med={p4c.get('winner_mfe_r_median', 0):.2f}R "
          f"MFE_cap={p4c.get('mfe_capture_pct_mean', 0):.1f}%")

    # ---- Pass criteria ----
    print("\n[8/8] pass-criteria check:")
    PASS_EXP = 0.25
    PASS_N = 100
    PASS_MFE = 1.5
    for lbl, pack in [("P4a", p4a), ("P4b", p4b), ("P4c", p4c)]:
        if pack.get("n_trades", 0) == 0:
            print(f"  {lbl}: FAIL (no trades)")
            continue
        exp_ok = pack["expectancy_r"] > PASS_EXP
        n_ok = pack["n_trades"] >= PASS_N
        mfe_ok = pack.get("winner_mfe_r_median", 0) > PASS_MFE
        status = "PASS" if (exp_ok and n_ok and mfe_ok) else "FAIL"
        reasons = []
        if not exp_ok: reasons.append(f"exp {pack['expectancy_r']:+.3f}R ≤ {PASS_EXP}R")
        if not n_ok: reasons.append(f"n {pack['n_trades']} < {PASS_N}")
        if not mfe_ok: reasons.append(f"winner MFE median {pack.get('winner_mfe_r_median',0):.2f}R ≤ {PASS_MFE}R")
        print(f"  {lbl}: {status}  ({'; '.join(reasons) or 'all gates cleared'})")

    # Filter skip counts from P4a's signal run (v1 filters on R1K, no VCP).
    sig_a_reasons = Counter()
    for s in skips_a:
        r = s.get("filter_name", "")
        sig_a_reasons[r] += 1

    # ---- dump ----
    metrics = {
        "P4a_R1K_E2": p4a,
        "P4b_R1K_D3": p4b,
        "P4c_R1K_VCPloose_E2": p4c,
        "data_quality": {
            "attempted": attempted,
            "with_data": with_data,
            "kept_after_history_filter": kept,
            "missing_or_delisted": attempted - with_data,
            "too_short_history": len(too_short),
            "too_short_examples": dict(list(too_short.items())[:10]),
            "iwb_as_of": as_of,
        },
        "signal_counts": {
            "P4a_no_vcp": len(sig_a),
            "P4c_vcp_loose": len(sig_c),
        },
        "top_skips_no_vcp": dict(sig_a_reasons.most_common(12)),
        "vcp_reasons_loose": dict(vcp_reasons.most_common()),
        "pass_criteria": {
            "expectancy_min": PASS_EXP,
            "n_trades_min": PASS_N,
            "winner_mfe_r_median_min": PASS_MFE,
        },
        "runtime_sec": round(time.time() - t0, 1),
    }
    path = os.path.join(OUTPUT_DIR, "breakout_r1k_sweep_metrics.json")
    with open(path, "w") as f:
        json.dump(metrics, f, indent=2, default=str)
    print(f"\nwrote: {path}")


if __name__ == "__main__":
    main()
