"""Breakout v1 ablation sweep — test window only.

Sets:
  A (volume gate)   : vol_mult in {1.0, 1.25, 1.5 (control), 2.0}
  B (position cap)  : max_concurrent in {5 (control), 10, 20, None (unlimited)}
  C (breadth filter): {True (control), False}
  D (exit logic)    : {partial+trail-10d (v1), 2R fixed, partial-2R+trail-20d, 3R fixed}

For A / B / C each row re-runs the filter-affected pipeline stages.
For D each row replays the v1 entries through a different exit rule.
No parameter tuning inside a single run; each row is one config.
"""

import json
import os
import sys
import time
from pathlib import Path

# sys.path bootstrap
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))   # trader/
sys.path.insert(0, str(Path(__file__).resolve().parent))       # strategies/breakout/

import pandas as pd

from shared.config import OUTPUT_DIR
from shared.earnings import fetch_earnings_dates
from shared.breadth import compute_breadth_above_ma

from config import (
    DATA_START, TEST_START, TEST_END,
    VOL_MULT, MAX_CONCURRENT_POSITIONS,
    PARTIAL_R, TRAIL_MA_LEN, FIXED_TARGET_R,
)
from universe import load_universe
from data import bulk_fetch
from signals import (
    prepare_ticker_indicators, prepare_spy, compute_rs_rank,
    generate_signals,
)
from backtest import run_backtest, Position
from exit_ablation import run_ablation
from output import summarize_trades, equity_curve, drawdown, cagr
import correlation as corr_mod


def _resolve_end(end_str: str) -> pd.Timestamp:
    if end_str == "today":
        return pd.Timestamp.today().normalize()
    return pd.Timestamp(end_str)


def _positions_from_trades(trades) -> list[Position]:
    """Reconstruct Positions at entry time from a Trade list (for exit replay)."""
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


def _pack(label: str, trades, universe_size: int) -> dict:
    s = summarize_trades(trades, label)
    eq = equity_curve(trades)
    s["max_dd"] = drawdown(eq) if len(eq) > 1 else 0.0
    s["cagr"] = cagr(eq)
    s["n_entries"] = len(trades)  # alias
    s["universe_size"] = universe_size
    # Expose the entry-date list (for correlation module downstream).
    s["entry_dates"] = sorted({pd.Timestamp(t.entry_date).normalize().isoformat() for t in trades})
    # Per-trade series for daily PnL reconstruction.
    s["trade_rows"] = [
        {
            "ticker": t.ticker,
            "entry_date": pd.Timestamp(t.entry_date).normalize().isoformat(),
            "exit_date": pd.Timestamp(t.exit_date).normalize().isoformat(),
            "total_pnl": t.total_pnl,
        } for t in trades
    ]
    return s


def main():
    run_start = time.time()
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    # ---- load once ----
    print("[load] universe...")
    tickers = load_universe()
    print(f"  -> {len(tickers)} tickers")

    print("[load] price data...")
    prices_raw = bulk_fetch(tickers + ["SPY"], start=DATA_START)
    spy_raw = prices_raw.pop("SPY", pd.DataFrame())
    min_bars = 252 + 200
    prices_raw = {t: df for t, df in prices_raw.items() if len(df) >= min_bars}
    print(f"  -> {len(prices_raw)} tickers after history filter")

    print("[load] earnings...")
    earnings_map = {}
    for t in prices_raw:
        try:
            earnings_map[t] = fetch_earnings_dates(t)
        except Exception:
            earnings_map[t] = []

    print("[load] indicators, rs_rank, spy, breadth...")
    prices = {t: prepare_ticker_indicators(df) for t, df in prices_raw.items()}
    spy = prepare_spy(spy_raw)
    rs_rank = compute_rs_rank(prices)
    breadth = compute_breadth_above_ma(prices_raw, ma_len=200)

    test_start = pd.Timestamp(TEST_START)
    test_end = _resolve_end(TEST_END)
    print(f"  test window: {test_start.date()} .. {test_end.date()}")

    results = {}

    # ---- signal cache: unique (vol_mult, use_breadth) combos ----
    signal_cache: dict[tuple, list] = {}

    def _signals_for(vol_mult: float, use_breadth: bool):
        key = (round(vol_mult, 3), use_breadth)
        if key not in signal_cache:
            print(f"  [signals] vol_mult={vol_mult}, use_breadth={use_breadth}")
            sigs, _ = generate_signals(
                prices, spy, breadth, rs_rank, earnings_map,
                test_start, test_end,
                vol_mult=vol_mult, use_breadth=use_breadth,
            )
            signal_cache[key] = sigs
            print(f"    -> {len(sigs)} signals")
        return signal_cache[key]

    # ---- control (v1) ----
    print("\n[control] re-running v1 baseline...")
    ctrl_sigs = _signals_for(VOL_MULT, use_breadth=True)
    ctrl_trades, _ = run_backtest(
        ctrl_sigs, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=MAX_CONCURRENT_POSITIONS,
        exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
    )
    results["v1_control"] = _pack("v1_control", ctrl_trades, len(prices))
    print(f"  -> {len(ctrl_trades)} trades, WR={results['v1_control']['win_rate']:.2%}, "
          f"exp={results['v1_control']['expectancy_r']:+.3f}R")

    # ---- Set A: volume gate ----
    print("\n[set A] volume gate sensitivity...")
    for label, vm in [("A1", 1.0), ("A2", 1.25), ("A3", 1.5), ("A4", 2.0)]:
        sigs = _signals_for(vm, use_breadth=True)
        trades, _ = run_backtest(
            sigs, prices, spy, earnings_map, test_start, test_end,
            max_concurrent=MAX_CONCURRENT_POSITIONS,
            exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
        )
        pack = _pack(f"{label}_vol{vm}", trades, len(prices))
        pack["vol_mult"] = vm
        results[label] = pack
        print(f"  {label} (vol={vm:>4}): {len(trades):>4} trades, WR={pack['win_rate']:.2%}, "
              f"exp={pack['expectancy_r']:+.3f}R, DD={pack['max_dd']:+.2%}")

    # ---- Set B: position cap ----
    print("\n[set B] position cap sensitivity...")
    v1_sigs = _signals_for(VOL_MULT, use_breadth=True)
    for label, cap in [("B1", 5), ("B2", 10), ("B3", 20), ("B4", None)]:
        trades, _ = run_backtest(
            v1_sigs, prices, spy, earnings_map, test_start, test_end,
            max_concurrent=cap,
            exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
        )
        pack = _pack(f"{label}_cap{cap}", trades, len(prices))
        pack["max_concurrent"] = cap if cap is not None else "unlimited"
        results[label] = pack
        cap_disp = "∞" if cap is None else str(cap)
        print(f"  {label} (cap={cap_disp:>3}): {len(trades):>4} trades, WR={pack['win_rate']:.2%}, "
              f"exp={pack['expectancy_r']:+.3f}R, DD={pack['max_dd']:+.2%}")

    # ---- Set C: breadth filter ----
    print("\n[set C] breadth filter on/off...")
    for label, use_b in [("C1", True), ("C2", False)]:
        sigs = _signals_for(VOL_MULT, use_breadth=use_b)
        trades, _ = run_backtest(
            sigs, prices, spy, earnings_map, test_start, test_end,
            max_concurrent=MAX_CONCURRENT_POSITIONS,
            exit_mode="partial_trail", partial_r=PARTIAL_R, trail_ma_len=TRAIL_MA_LEN,
        )
        pack = _pack(f"{label}_breadth{'on' if use_b else 'off'}", trades, len(prices))
        pack["use_breadth"] = use_b
        results[label] = pack
        print(f"  {label} (breadth={'ON' if use_b else 'OFF'}): {len(trades):>4} trades, "
              f"WR={pack['win_rate']:.2%}, exp={pack['expectancy_r']:+.3f}R, DD={pack['max_dd']:+.2%}")

    # ---- Set D: exit logic (replay v1 entries) ----
    print("\n[set D] exit logic (replay v1 entries)...")
    v1_entries = _positions_from_trades(ctrl_trades)
    d_configs = [
        ("D1", "partial_trail", dict(partial_r=1.0, trail_ma_len=10)),
        ("D2", "fixed",         dict(target_r=2.0)),
        ("D3", "partial_trail", dict(partial_r=2.0, trail_ma_len=20)),
        ("D4", "fixed",         dict(target_r=3.0)),
    ]
    for label, mode, kw in d_configs:
        trades = run_ablation(
            v1_entries, prices, earnings_map, test_end,
            exit_mode=mode, **kw,
        )
        pack = _pack(f"{label}_{mode}_{list(kw.values())}", trades, len(prices))
        pack["exit_mode"] = mode
        pack.update({f"exit_{k}": v for k, v in kw.items()})
        results[label] = pack
        kw_disp = ", ".join(f"{k}={v}" for k, v in kw.items())
        print(f"  {label} ({mode}, {kw_disp}): {len(trades):>3} trades, "
              f"WR={pack['win_rate']:.2%}, exp={pack['expectancy_r']:+.3f}R")

    # ---- pick winners per set (by expectancy_r) ----
    def _best(keys: list[str]) -> tuple[str, dict]:
        kv = [(k, results[k]) for k in keys if k in results and results[k].get("n_trades", 0) > 0]
        if not kv:
            return ("", {})
        return max(kv, key=lambda kv: kv[1]["expectancy_r"])

    best_A, pA = _best(["A1", "A2", "A3", "A4"])
    best_B, pB = _best(["B1", "B2", "B3", "B4"])
    best_C, pC = _best(["C1", "C2"])
    best_D, pD = _best(["D1", "D2", "D3", "D4"])

    print("\n[winners by expectancy]")
    for lbl, pack in [(best_A, pA), (best_B, pB), (best_C, pC), (best_D, pD)]:
        if not pack: continue
        print(f"  {lbl}: exp={pack['expectancy_r']:+.3f}R  WR={pack['win_rate']:.2%}  n={pack['n_trades']}")

    # Resolve winning params.
    win_vol = pA.get("vol_mult", VOL_MULT)
    win_cap = pB.get("max_concurrent", MAX_CONCURRENT_POSITIONS)
    if win_cap == "unlimited": win_cap = None
    win_breadth = pC.get("use_breadth", True)
    win_exit_mode = pD.get("exit_mode", "partial_trail")
    win_exit_kw = {}
    for k in ("exit_target_r", "exit_partial_r", "exit_trail_ma_len"):
        if k in pD:
            win_exit_kw[k.replace("exit_", "")] = pD[k]

    print(f"\n[v1.1 combined] vol={win_vol}, cap={win_cap}, breadth={win_breadth}, "
          f"exit={win_exit_mode} {win_exit_kw}")

    # ---- run v1.1 combined ----
    v11_sigs = _signals_for(win_vol, use_breadth=win_breadth)
    v11_trades, _ = run_backtest(
        v11_sigs, prices, spy, earnings_map, test_start, test_end,
        max_concurrent=win_cap,
        exit_mode=win_exit_mode,
        partial_r=win_exit_kw.get("partial_r", PARTIAL_R),
        trail_ma_len=win_exit_kw.get("trail_ma_len", TRAIL_MA_LEN),
        target_r=win_exit_kw.get("target_r", FIXED_TARGET_R),
    )
    v11_pack = _pack("v1.1_combined", v11_trades, len(prices))
    v11_pack.update({
        "vol_mult": win_vol,
        "max_concurrent": win_cap if win_cap is not None else "unlimited",
        "use_breadth": win_breadth,
        "exit_mode": win_exit_mode,
        **{f"exit_{k}": v for k, v in win_exit_kw.items()},
    })
    results["v1.1_combined"] = v11_pack
    print(f"  -> {len(v11_trades)} trades, WR={v11_pack['win_rate']:.2%}, "
          f"exp={v11_pack['expectancy_r']:+.3f}R, DD={v11_pack['max_dd']:+.2%}, "
          f"CAGR={v11_pack['cagr']:+.2%}")

    # ---- daily-PnL correlation: v1 control and v1.1 combined ----
    print("\n[correlation] daily P&L vs SID + S&D-long (intersection window)...")
    v1_corr = corr_mod.run(results["v1_control"]["trade_rows"], threshold=0.5)
    v11_corr = corr_mod.run(results["v1.1_combined"]["trade_rows"], threshold=0.5)
    results["v1_control_correlation"] = v1_corr
    results["v1.1_correlation"] = v11_corr
    for label, c in [("v1", v1_corr), ("v1.1", v11_corr)]:
        if c.get("max_abs_offdiag") is None:
            print(f"  {label}: no intersection window")
            continue
        print(f"  {label}: max |r| off-diag = {c['max_abs_offdiag']:.3f}  "
              f"({'PASS' if c['pass'] else 'FAIL'} @ {c['threshold']})")
        for pair, v in c.get("pairs", {}).items():
            print(f"     {pair}: r = {v:+.3f}")

    # Slim trade_rows out of ablation metrics dump (retain only v1_control + v1.1
    # since those are used for correlation; others just bloat the file).
    for k, v in results.items():
        if isinstance(v, dict) and "trade_rows" in v and k not in ("v1_control", "v1.1_combined"):
            v.pop("trade_rows", None)

    # ---- dump ----
    results["runtime_sec"] = round(time.time() - run_start, 1)
    path = os.path.join(OUTPUT_DIR, "breakout_ablation_metrics.json")
    with open(path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nwrote: {path}")
    print(f"runtime: {results['runtime_sec']}s")


if __name__ == "__main__":
    main()
