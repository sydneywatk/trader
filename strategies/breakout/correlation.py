"""Daily-P&L correlation between breakout, SID, and S&D-long equity curves.

SPEC v1.1 ablation request: supersede the v1 date-overlap gate with a
Pearson correlation on daily realized P&L. If |r| < 0.5 pairwise on the
intersection window, the strategies ARE diversifying.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np
import pandas as pd

from shared.config import OUTPUT_DIR


def _latest(prefix: str) -> str | None:
    if not os.path.isdir(OUTPUT_DIR):
        return None
    matches = sorted(f for f in os.listdir(OUTPUT_DIR)
                     if f.startswith(prefix) and f.endswith(".xlsx"))
    return os.path.join(OUTPUT_DIR, matches[-1]) if matches else None


def sid_daily_pnl(path: str | None) -> pd.Series:
    if path is None:
        return pd.Series(dtype=float, name="sid")
    df = pd.read_excel(path, sheet_name=0)
    if "Date Exit" not in df.columns or "$ Total Profit" not in df.columns:
        return pd.Series(dtype=float, name="sid")
    d = pd.to_datetime(df["Date Exit"], errors="coerce")
    pnl = pd.to_numeric(df["$ Total Profit"], errors="coerce")
    ser = pd.DataFrame({"date": d.dt.normalize(), "pnl": pnl}).dropna()
    return ser.groupby("date")["pnl"].sum().rename("sid")


def sd_long_daily_pnl(path: str | None) -> pd.Series:
    if path is None:
        return pd.Series(dtype=float, name="sd_long")
    df = pd.read_excel(path, sheet_name=0)
    if "exit_date" not in df.columns or "gain_loss_dollars" not in df.columns:
        return pd.Series(dtype=float, name="sd_long")
    if "direction" in df.columns:
        df = df[df["direction"] == "long"]
    d = pd.to_datetime(df["exit_date"], errors="coerce")
    pnl = pd.to_numeric(df["gain_loss_dollars"], errors="coerce")
    ser = pd.DataFrame({"date": d.dt.normalize(), "pnl": pnl}).dropna()
    return ser.groupby("date")["pnl"].sum().rename("sd_long")


def breakout_daily_pnl(trade_rows: list[dict]) -> pd.Series:
    if not trade_rows:
        return pd.Series(dtype=float, name="breakout")
    df = pd.DataFrame(trade_rows)
    d = pd.to_datetime(df["exit_date"], errors="coerce").dt.normalize()
    pnl = pd.to_numeric(df["total_pnl"], errors="coerce")
    ser = pd.DataFrame({"date": d, "pnl": pnl}).dropna()
    return ser.groupby("date")["pnl"].sum().rename("breakout")


def align_intersection(series_map: dict[str, pd.Series]) -> pd.DataFrame:
    """Align on the intersection of each series' min/max trading date.

    Missing days within that window are filled with 0 (no P&L that day).
    Business-day index so zero-PnL days show up once, not once per strategy.
    """
    valid = {k: s for k, s in series_map.items() if len(s) > 0}
    if len(valid) < 2:
        return pd.DataFrame()
    start = max(s.index.min() for s in valid.values())
    end = min(s.index.max() for s in valid.values())
    if start >= end:
        return pd.DataFrame()
    idx = pd.bdate_range(start, end)
    out = pd.DataFrame(index=idx)
    for k, s in valid.items():
        out[k] = s.reindex(idx).fillna(0.0)
    return out


def pearson_matrix(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame()
    return df.corr(method="pearson")


def summarize(corr: pd.DataFrame, threshold: float = 0.5) -> dict:
    """Off-diagonal max |r|. If all < threshold -> PASS (diversified)."""
    if corr.empty:
        return {"n_strategies": 0, "max_abs_offdiag": None,
                "pass": None, "threshold": threshold}
    n = corr.shape[0]
    mask = ~np.eye(n, dtype=bool)
    offdiag = corr.values[mask]
    max_abs = float(np.abs(offdiag).max()) if len(offdiag) else 0.0
    return {
        "n_strategies": n,
        "max_abs_offdiag": max_abs,
        "pass": max_abs < threshold,
        "threshold": threshold,
        "pairs": {
            f"{a}~{b}": float(corr.loc[a, b])
            for a in corr.index for b in corr.columns if a < b
        },
    }


def run(trade_rows: list[dict], threshold: float = 0.5) -> dict:
    sid_path = _latest("sid_method_backtest_")
    sd_path = _latest("sd_method_backtest_")
    series = {
        "breakout": breakout_daily_pnl(trade_rows),
        "sid": sid_daily_pnl(sid_path),
        "sd_long": sd_long_daily_pnl(sd_path),
    }
    aligned = align_intersection(series)
    if aligned.empty:
        return {
            "available": [k for k, v in series.items() if len(v) > 0],
            "summary": {"pass": None, "note": "no intersection window"},
        }
    corr = pearson_matrix(aligned)
    out = summarize(corr, threshold=threshold)
    out.update({
        "window_start": aligned.index.min().isoformat(),
        "window_end": aligned.index.max().isoformat(),
        "n_days": len(aligned),
        "sid_trade_days": int((aligned["sid"] != 0).sum()) if "sid" in aligned else 0,
        "sd_trade_days": int((aligned["sd_long"] != 0).sum()) if "sd_long" in aligned else 0,
        "breakout_trade_days": int((aligned["breakout"] != 0).sum()) if "breakout" in aligned else 0,
        "corr_matrix": corr.to_dict(),
        "sid_excel": sid_path,
        "sd_excel": sd_path,
    })
    return out
