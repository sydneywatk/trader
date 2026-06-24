"""Breakout v1 output: metrics, trade log Excel, skip-reason CSV, correlation gate.

SPEC references: §5 (skip logging), §6 (validation metrics), §6.5 (correlation gate).
"""

import os
from dataclasses import asdict
from datetime import datetime
from typing import Iterable, Optional

import numpy as np
import pandas as pd

from config import (
    ACCOUNT_SIZE, RISK_PCT,
    CORRELATION_GATE_THRESHOLD, CORRELATION_GATE_STRATEGIES,
    TRAIN_TEST_WR_TOLERANCE_PP,
    SKIPLOG_PREFIX, TRADELOG_PREFIX,
)
from shared.config import OUTPUT_DIR
from backtest import Trade


def summarize_trades(trades: list[Trade], label: str) -> dict:
    if not trades:
        return {"label": label, "n_trades": 0}
    rrs = np.array([t.trade_rr for t in trades])
    pnls = np.array([t.total_pnl for t in trades])
    wins_mask = pnls > 0
    losses_mask = pnls < 0
    n = len(trades)
    n_wins = int(wins_mask.sum())
    wr = n_wins / n if n else 0.0
    avg_win_r = float(rrs[wins_mask].mean()) if wins_mask.any() else 0.0
    avg_loss_r = float(rrs[losses_mask].mean()) if losses_mask.any() else 0.0
    avg_win_d = float(pnls[wins_mask].mean()) if wins_mask.any() else 0.0
    avg_loss_d = float(pnls[losses_mask].mean()) if losses_mask.any() else 0.0
    expectancy_r = float(rrs.mean())
    total_pnl = float(pnls.sum())
    avg_duration = float(np.mean([t.duration_days for t in trades]))

    return {
        "label": label,
        "n_trades": n,
        "win_rate": wr,
        "avg_winner_r": avg_win_r,
        "avg_loser_r": avg_loss_r,
        "avg_winner_$": avg_win_d,
        "avg_loser_$": avg_loss_d,
        "expectancy_r": expectancy_r,
        "total_pnl": total_pnl,
        "avg_duration_days": avg_duration,
    }


def equity_curve(trades: list[Trade], starting_equity: float = ACCOUNT_SIZE) -> pd.Series:
    """Build a date-indexed equity curve from sequential trade exits.

    Multiple trades can close on the same day -> aggregate PnL per date
    before running it cumulatively. Returns a Series indexed by unique dates.
    """
    if not trades:
        return pd.Series([starting_equity], name="equity")
    df = pd.DataFrame({
        "date": [pd.Timestamp(t.exit_date).normalize() for t in trades],
        "pnl": [t.total_pnl for t in trades],
    })
    daily = df.groupby("date")["pnl"].sum().sort_index()
    eq = starting_equity + daily.cumsum()
    return eq.rename("equity")


def drawdown(eq: pd.Series) -> float:
    if eq.empty:
        return 0.0
    peak = eq.cummax()
    dd = (eq - peak) / peak
    return float(dd.min())


def cagr(eq: pd.Series, starting: float = ACCOUNT_SIZE) -> float:
    if eq.empty or len(eq) < 2:
        return 0.0
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    if years <= 0:
        return 0.0
    return float((eq.iloc[-1] / starting) ** (1 / years) - 1)


def sharpe(eq: pd.Series) -> float:
    if eq.empty or len(eq) < 3:
        return 0.0
    # Daily return series on equity curve (irregular dates -> forward-fill
    # to business days for a rough Sharpe estimate).
    daily = eq.resample("B").ffill().pct_change().dropna()
    if daily.std() == 0 or daily.empty:
        return 0.0
    return float(daily.mean() / daily.std() * np.sqrt(252))


def attribution(trades: list[Trade]) -> dict:
    """Break down exits by reason."""
    counts: dict[str, int] = {}
    pnl_by: dict[str, float] = {}
    for t in trades:
        counts[t.exit_reason] = counts.get(t.exit_reason, 0) + 1
        pnl_by[t.exit_reason] = pnl_by.get(t.exit_reason, 0.0) + t.total_pnl
    return {"counts": counts, "pnl_by_reason": pnl_by}


def skip_counts(skips: list[dict]) -> pd.DataFrame:
    if not skips:
        return pd.DataFrame(columns=["filter_name", "count"])
    df = pd.DataFrame(skips)
    return (df["filter_name"].value_counts()
              .rename_axis("filter_name").reset_index(name="count"))


# --- Correlation gate (SPEC §6.5) --------------------------------------------

def _parse_sid_entries(path: str) -> set[pd.Timestamp]:
    """SID Excel: col 'Date Entered' as MM/DD/YYYY."""
    try:
        df = pd.read_excel(path, sheet_name=0)
    except Exception:
        return set()
    if "Date Entered" not in df.columns:
        return set()
    dates = pd.to_datetime(df["Date Entered"], errors="coerce").dropna()
    return set(dates.dt.normalize().tolist())


def _parse_sd_entries(path: str, long_only: bool = True) -> set[pd.Timestamp]:
    """S&D Excel: col 'entry_date' ISO. When long_only=True, filter to longs."""
    try:
        df = pd.read_excel(path, sheet_name=0)
    except Exception:
        return set()
    if "entry_date" not in df.columns:
        return set()
    if long_only and "direction" in df.columns:
        df = df[df["direction"] == "long"]
    dates = pd.to_datetime(df["entry_date"], errors="coerce").dropna()
    return set(dates.dt.normalize().tolist())


def correlation_gate(breakout_trades: list[Trade],
                     sid_excel_path: Optional[str],
                     sd_excel_path: Optional[str],
                     threshold: float = CORRELATION_GATE_THRESHOLD) -> dict:
    """Compute same-day entry overlap rate vs SID and S&D-long.

    Returns a dict with:
      overlap_rate, pass, n_breakout_dates, overlap_with_sid, overlap_with_sd,
      strategies_available (which legs actually had logs), threshold.
    """
    if not breakout_trades:
        return {"overlap_rate": 0.0, "pass": True, "n_breakout_dates": 0,
                "threshold": threshold, "strategies_available": [],
                "note": "no breakout trades to evaluate"}

    bo_dates = {pd.Timestamp(t.entry_date).normalize() for t in breakout_trades}

    sid_dates = _parse_sid_entries(sid_excel_path) if sid_excel_path else set()
    sd_dates = _parse_sd_entries(sd_excel_path) if sd_excel_path else set()

    available = []
    if sid_dates: available.append("sid")
    if sd_dates: available.append("sd_long")

    combined = sid_dates | sd_dates
    overlap = bo_dates & combined
    rate = len(overlap) / len(bo_dates) if bo_dates else 0.0

    return {
        "overlap_rate": rate,
        "pass": rate <= threshold,
        "n_breakout_dates": len(bo_dates),
        "n_overlap": len(overlap),
        "overlap_with_sid": len(bo_dates & sid_dates),
        "overlap_with_sd": len(bo_dates & sd_dates),
        "threshold": threshold,
        "strategies_available": available,
    }


# --- File writers ------------------------------------------------------------

def _trades_df(trades: list[Trade]) -> pd.DataFrame:
    if not trades:
        return pd.DataFrame()
    rows = []
    for t in trades:
        d = asdict(t)
        rows.append(d)
    return pd.DataFrame(rows)


def write_excel(trades: list[Trade], ablation_trades: list[Trade] | None,
                label: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"{TRADELOG_PREFIX}_{label}_{stamp}.xlsx")
    with pd.ExcelWriter(path, engine="openpyxl") as w:
        _trades_df(trades).to_excel(w, sheet_name="Baseline Trades", index=False)
        if ablation_trades is not None:
            _trades_df(ablation_trades).to_excel(w, sheet_name="2R Ablation Trades", index=False)
    return path


def write_skip_csv(skips: list[dict], label: str) -> str:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d")
    path = os.path.join(OUTPUT_DIR, f"{SKIPLOG_PREFIX}_{label}_{stamp}.csv")
    if skips:
        pd.DataFrame(skips).to_csv(path, index=False)
    else:
        pd.DataFrame(columns=["date", "ticker", "filter_name", "setup_close", "pivot"]).to_csv(path, index=False)
    return path


def print_summary(train_sum: dict | None, test_sum: dict | None,
                  ablation_sum: dict | None, gate: dict,
                  train_dd: float | None, test_dd: float | None,
                  train_cagr: float | None, test_cagr: float | None,
                  train_sharpe: float | None, test_sharpe: float | None,
                  train_top_skips: pd.DataFrame | None,
                  test_top_skips: pd.DataFrame | None,
                  ) -> None:

    def _p(s):
        print(s)

    _p("=" * 72)
    _p("BREAKOUT v1 — SUMMARY")
    _p("=" * 72)
    for lbl, s, dd, cg, sh in [
        ("TRAIN (2013-01 to 2019-06)", train_sum, train_dd, train_cagr, train_sharpe),
        ("TEST  (2019-07 to today)", test_sum, test_dd, test_cagr, test_sharpe),
        ("ABLATION 2R (test window)", ablation_sum, None, None, None),
    ]:
        if s is None:
            continue
        _p(f"\n{lbl}")
        _p(f"  n_trades        : {s['n_trades']}")
        if s['n_trades'] == 0:
            continue
        _p(f"  win_rate        : {s['win_rate']:.2%}")
        _p(f"  avg_winner      : {s['avg_winner_r']:+.2f}R   (${s['avg_winner_$']:+,.0f})")
        _p(f"  avg_loser       : {s['avg_loser_r']:+.2f}R   (${s['avg_loser_$']:+,.0f})")
        _p(f"  expectancy      : {s['expectancy_r']:+.3f}R per trade")
        _p(f"  total_pnl       : ${s['total_pnl']:+,.0f}")
        _p(f"  avg_duration    : {s['avg_duration_days']:.1f} days")
        if cg is not None:
            _p(f"  CAGR            : {cg:+.2%}")
        if dd is not None:
            _p(f"  max_drawdown    : {dd:+.2%}")
        if sh is not None:
            _p(f"  sharpe          : {sh:+.2f}")

    _p("\n" + "-" * 72)
    _p("CORRELATION GATE (vs SID + S&D long, same-day entry overlap)")
    _p("-" * 72)
    status = "PASS" if gate.get("pass") else "FAIL"
    _p(f"  overlap_rate    : {gate.get('overlap_rate', 0):.2%}")
    _p(f"  threshold       : {gate.get('threshold', CORRELATION_GATE_THRESHOLD):.2%}")
    _p(f"  result          : {status}")
    _p(f"  breakout_dates  : {gate.get('n_breakout_dates', 0)}")
    _p(f"  overlap total   : {gate.get('n_overlap', 0)}")
    _p(f"  with SID        : {gate.get('overlap_with_sid', 0)}")
    _p(f"  with S&D (long) : {gate.get('overlap_with_sd', 0)}")
    if "note" in gate:
        _p(f"  note            : {gate['note']}")

    for lbl, sk in [("TRAIN", train_top_skips), ("TEST", test_top_skips)]:
        if sk is None or sk.empty:
            continue
        _p("\n" + "-" * 72)
        _p(f"TOP SKIP REASONS ({lbl})")
        _p("-" * 72)
        for _, row in sk.head(10).iterrows():
            _p(f"  {row['filter_name']:<36s} {int(row['count']):>8d}")
    _p("=" * 72)


def train_test_delta(train_sum: dict, test_sum: dict) -> dict:
    if not train_sum or not test_sum or train_sum.get("n_trades", 0) == 0 or test_sum.get("n_trades", 0) == 0:
        return {"wr_delta_pp": None, "within_tolerance": None,
                "expectancy_delta_r": None}
    wr_delta_pp = (test_sum["win_rate"] - train_sum["win_rate"]) * 100
    exp_delta = test_sum["expectancy_r"] - train_sum["expectancy_r"]
    return {
        "wr_delta_pp": wr_delta_pp,
        "within_tolerance": abs(wr_delta_pp) <= TRAIN_TEST_WR_TOLERANCE_PP,
        "expectancy_delta_r": exp_delta,
    }
