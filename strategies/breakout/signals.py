"""Breakout v1 signal generation — SPEC §4.1–4.7.

Setup: 52-week-high closing break (§4.1).
Gates applied in order: Trend Template (§4.2), volume (§4.3), pivot extension
(§4.4), SPY regime (§4.5), market breadth (§4.6), earnings blackout (§4.7).
Concurrent-position cap (§4.8) and entry (§4.9) are handled in backtest.py
because they depend on runtime state.

Skip logging (SPEC §5): every rejected setup is stamped with the first-failing
filter name. Pre-setup bars (no 52W break) are NOT logged per row — their
absence is counted aggregately to keep the skip log tractable. This is noted
in the results doc.
"""

from datetime import datetime
from dataclasses import dataclass

import numpy as np
import pandas as pd

from config import (
    SMA_50, SMA_150, SMA_200, SMA_200_RISING_LOOKBACK_DAYS,
    LOW_52W_BUFFER, HIGH_52W_BUFFER,
    RS_PERCENTILE_MIN, RS_LOOKBACK,
    VOL_AVG_LEN, VOL_MULT,
    PIVOT_EXTENSION_MAX,
    REGIME_SPY_MA,
    BREADTH_THRESHOLD,
    EARNINGS_BLACKOUT_DAYS,
    VCP_BASE_LOOKBACK, VCP_MIN_CONTRACTIONS, VCP_TIGHTENING_PCT,
    VCP_C1_MAX_PCT, VCP_VOLUME_DRYUP_REQUIRED, VCP_SWING_WINDOW,
)
from shared.earnings import next_earnings_date


@dataclass
class Signal:
    ticker: str
    signal_date: pd.Timestamp
    pivot: float
    close: float
    volume: float
    vol_avg_50: float
    vol_ratio: float
    rs_63_rank: float
    spy_close: float
    spy_sma_50: float
    spy_sma_200: float


def prepare_ticker_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add SMA_50/150/200, vol_avg_50, high_52w (excl today), low_52w, rs_63.

    high_52w[t] = max(close[t-252 : t])  -- exclusive of today by design.
    rs_63[t]    = close[t] / close[t-63] - 1
    """
    out = df.copy()
    close = out["Close"]
    vol = out["Volume"]

    out["sma_50"] = close.rolling(SMA_50).mean()
    out["sma_150"] = close.rolling(SMA_150).mean()
    out["sma_200"] = close.rolling(SMA_200).mean()
    out["sma_200_back"] = out["sma_200"].shift(SMA_200_RISING_LOOKBACK_DAYS)

    out["vol_avg_50"] = vol.rolling(VOL_AVG_LEN).mean()
    out["vol_prev"] = vol.shift(1)

    # 52-week high/low EXCLUDING today's bar (avoid look-ahead at the setup check).
    close_shifted = close.shift(1)
    out["high_52w"] = close_shifted.rolling(252, min_periods=60).max()
    out["low_52w"] = close_shifted.rolling(252, min_periods=60).min()

    out["rs_63"] = close / close.shift(RS_LOOKBACK) - 1.0

    # Trailing-MA variants for exit logic — 10d (v1 default) and 20d (D3 ablation).
    out["sma_10"] = close.rolling(10).mean()
    out["sma_20"] = close.rolling(20).mean()
    return out


def compute_rs_rank(prices: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Cross-sectional percentile rank of rs_63 per bar, across the universe.

    Returns a wide DataFrame (date x ticker) of percentile ranks in [0, 100].
    Tickers without rs_63 on a given date contribute NaN and are excluded.
    """
    frames = []
    for t, df in prices.items():
        if "rs_63" not in df.columns:
            continue
        frames.append(df["rs_63"].rename(t))
    if not frames:
        return pd.DataFrame()
    wide = pd.concat(frames, axis=1)
    # Per-row percentile rank (0-100). `pct=True` gives [0, 1]; multiply.
    ranks = wide.rank(axis=1, pct=True) * 100.0
    return ranks


def prepare_spy(spy_df: pd.DataFrame) -> pd.DataFrame:
    out = spy_df.copy()
    out["spy_sma_50"] = out["Close"].rolling(50).mean()
    out["spy_sma_200"] = out["Close"].rolling(REGIME_SPY_MA).mean()
    return out


# --- Gate checks -------------------------------------------------------------
# Each returns (ok: bool, skip_reason: Optional[str]).

def _gate_trend_template(row: pd.Series, sma_200_back: float,
                         rs_rank_val: float) -> tuple[bool, str | None]:
    c = row["Close"]
    s50, s150, s200 = row["sma_50"], row["sma_150"], row["sma_200"]
    lo, hi = row["low_52w"], row["high_52w"]

    if pd.isna(s150) or pd.isna(s200):
        return False, "tt_insufficient_history"
    if not (c > s150 and c > s200):
        return False, "tt_price_below_mas"
    if not (s150 > s200):
        return False, "tt_150_below_200"
    if pd.isna(sma_200_back) or not (s200 > sma_200_back):
        return False, "tt_200_not_rising"
    if pd.isna(s50) or not (s50 > s150 > s200):
        return False, "tt_ma_stack"
    if not (c > s50):
        return False, "tt_price_below_50"
    if pd.isna(lo) or not (c >= lo * LOW_52W_BUFFER):
        return False, "tt_too_close_to_low"
    if pd.isna(hi) or not (c >= hi * HIGH_52W_BUFFER):
        return False, "tt_too_far_below_high"
    if pd.isna(rs_rank_val) or rs_rank_val < RS_PERCENTILE_MIN:
        return False, "tt_rs_below_70"
    return True, None


def _gate_volume(row: pd.Series, vol_mult: float = VOL_MULT) -> tuple[bool, str | None]:
    v, va, vp = row["Volume"], row["vol_avg_50"], row["vol_prev"]
    if pd.isna(va) or pd.isna(vp):
        return False, "vol_insufficient_history"
    if not (v >= vol_mult * va and v > vp):
        return False, "vol_insufficient"
    return True, None


def _gate_extension(close: float, pivot: float) -> tuple[bool, str | None]:
    if close > pivot * PIVOT_EXTENSION_MAX:
        return False, "extended_past_pivot"
    return True, None


def _gate_regime(spy_row: pd.Series) -> tuple[bool, str | None]:
    c, s200 = spy_row["Close"], spy_row["spy_sma_200"]
    if pd.isna(s200):
        return False, "regime_insufficient_spy_history"
    if not (c > s200):
        return False, "regime_spy_below_200"
    return True, None


def _gate_breadth(breadth_val: float, use_breadth: bool = True) -> tuple[bool, str | None]:
    if not use_breadth:
        return True, None
    if pd.isna(breadth_val):
        return False, "breadth_insufficient_history"
    if breadth_val < BREADTH_THRESHOLD:
        return False, "breadth_below_40pct"
    return True, None


def _gate_earnings(earnings_dates: list[datetime],
                   date: pd.Timestamp) -> tuple[bool, str | None]:
    if not earnings_dates:
        # No earnings data -- pass (documented in shared/earnings.py behavior)
        return True, None
    nxt = next_earnings_date(earnings_dates, date.to_pydatetime())
    if nxt is not None:
        days_to = (nxt.date() - date.date()).days
        if 0 <= days_to < EARNINGS_BLACKOUT_DAYS:
            return False, "earnings_blackout"
    # days_since_last >= 5 is a soft rule (not checked here -- SPEC §4.7 says
    # "days_since_last_earnings(ticker, t) < 5" is a miss). Implement it:
    for ed in reversed(earnings_dates):
        if ed.date() <= date.date():
            days_since = (date.date() - ed.date()).days
            if days_since < EARNINGS_BLACKOUT_DAYS:
                return False, "earnings_post_blackout"
            break
    return True, None


# --- VCP (Volatility Contraction Pattern) — Phase 3 --------------------------

def _zigzag_pivots(high: np.ndarray, low: np.ndarray, win: int = 2) -> list[tuple[int, str, float]]:
    """Centered-window swing pivots on a 2w+1 bar window.

    Returns list of (idx, 'H'|'L', price) alternating in time, with adjacent
    same-type pivots collapsed (keep highest H, lowest L).
    """
    n = len(high)
    raw: list[tuple[int, str, float]] = []
    for i in range(win, n - win):
        window_h = high[i - win : i + win + 1]
        window_l = low[i - win : i + win + 1]
        if high[i] >= window_h.max():
            raw.append((i, "H", float(high[i])))
        if low[i] <= window_l.min():
            raw.append((i, "L", float(low[i])))
    # Sort stably by index; when a bar is both H and L, keep both (rare).
    raw.sort(key=lambda x: (x[0], 0 if x[1] == "H" else 1))

    zig: list[tuple[int, str, float]] = []
    for p in raw:
        if zig and zig[-1][1] == p[1]:
            # Same-type adjacent: keep the more extreme one.
            if p[1] == "H":
                if p[2] > zig[-1][2]:
                    zig[-1] = p
            else:
                if p[2] < zig[-1][2]:
                    zig[-1] = p
        else:
            zig.append(p)
    return zig


def _gate_vcp(base: pd.DataFrame,
              min_contractions: int = VCP_MIN_CONTRACTIONS,
              tightening_pct: float = VCP_TIGHTENING_PCT,
              c1_max_pct: float = VCP_C1_MAX_PCT,
              volume_dryup_required: bool = VCP_VOLUME_DRYUP_REQUIRED,
              swing_window: int = VCP_SWING_WINDOW) -> tuple[bool, str | None, dict]:
    """Check whether the given base window contains the VCP pattern.

    Parameters (default to SPEC Phase 3 config values):
      - min_contractions: N successive H→L contractions required (default 3)
      - tightening_pct: each C_{k+1} <= C_k * (1 - tightening_pct)
      - c1_max_pct: C1 range cap as fraction of swing-low price
      - volume_dryup_required: avg(last 20) < avg(first 20) of base
      - swing_window: ±N bars for centered pivot detection (5-bar total when =2)
    """
    if base is None or len(base) < 40:
        return False, "vcp_insufficient_base_bars", {}
    high = base["High"].to_numpy(dtype=float)
    low = base["Low"].to_numpy(dtype=float)
    vol = base["Volume"].to_numpy(dtype=float)

    zig = _zigzag_pivots(high, low, win=swing_window)

    # Build chronological H→L pairs. Start at the first 'H' in zig.
    pairs: list[tuple[tuple[int, str, float], tuple[int, str, float]]] = []
    i = 0
    while i < len(zig) - 1:
        if zig[i][1] == "H" and zig[i + 1][1] == "L":
            pairs.append((zig[i], zig[i + 1]))
            i += 2
        else:
            i += 1

    if len(pairs) < min_contractions:
        return False, "vcp_insufficient_pivots", {"n_pairs": len(pairs)}

    # Take the most recent N pairs.
    recent = pairs[-min_contractions:]
    ranges = []
    for (sh, sl) in recent:
        sh_price, sl_price = sh[2], sl[2]
        if sl_price <= 0:
            return False, "vcp_bad_price", {}
        ranges.append((sh_price - sl_price) / sl_price)

    # C1 cap
    if ranges[0] > c1_max_pct:
        return False, "vcp_c1_too_wide", {"c1_pct": round(ranges[0], 4)}

    # Each subsequent contraction must be >= tightening_pct tighter.
    tight_threshold = 1.0 - tightening_pct
    for k in range(1, len(ranges)):
        if ranges[k] > ranges[k - 1] * tight_threshold:
            return False, f"vcp_c{k+1}_not_tighter", {
                f"c{k}_pct": round(ranges[k - 1], 4),
                f"c{k+1}_pct": round(ranges[k], 4),
            }

    # Volume dryup
    if volume_dryup_required and len(base) >= 40:
        vf = float(vol[:20].mean())
        vl = float(vol[-20:].mean())
        if not (vl < vf):
            return False, "vcp_volume_did_not_dryup", {
                "vol_first20": round(vf, 0), "vol_last20": round(vl, 0),
            }
        extras = {"vol_first20": round(vf, 0), "vol_last20": round(vl, 0)}
    else:
        extras = {}

    ctx = {f"c{i+1}_pct": round(r, 4) for i, r in enumerate(ranges)}
    ctx.update(extras)
    return True, None, ctx


# --- Main entry --------------------------------------------------------------

def generate_signals(prices: dict[str, pd.DataFrame],
                     spy: pd.DataFrame,
                     breadth: pd.Series,
                     rs_rank: pd.DataFrame,
                     earnings_map: dict[str, list[datetime]],
                     window_start: pd.Timestamp,
                     window_end: pd.Timestamp,
                     vol_mult: float = VOL_MULT,
                     use_breadth: bool = True,
                     use_vcp: bool = False,
                     vcp_base_lookback: int = VCP_BASE_LOOKBACK,
                     vcp_min_contractions: int = VCP_MIN_CONTRACTIONS,
                     vcp_tightening_pct: float = VCP_TIGHTENING_PCT,
                     vcp_c1_max_pct: float = VCP_C1_MAX_PCT,
                     vcp_volume_dryup: bool = VCP_VOLUME_DRYUP_REQUIRED,
                     vcp_swing_window: int = VCP_SWING_WINDOW) -> tuple[list[Signal], list[dict]]:
    """Scan the universe across [window_start, window_end] and emit signals + skip log."""
    signals: list[Signal] = []
    skips: list[dict] = []

    spy_reindexed = spy.reindex(spy.index.union(breadth.index)).sort_index()

    for ticker, df in prices.items():
        if df.empty or "high_52w" not in df.columns:
            continue
        mask = (df.index >= window_start) & (df.index <= window_end)
        sub = df[mask]
        if sub.empty:
            continue

        rs_series = rs_rank[ticker].reindex(sub.index) if ticker in rs_rank.columns else pd.Series(index=sub.index, dtype=float)
        earn = earnings_map.get(ticker, [])

        for date, row in sub.iterrows():
            c, pivot_ref = row["Close"], row["high_52w"]

            # §4.1 — setup: close strictly above prior 252-bar high.
            if pd.isna(pivot_ref) or not (c > pivot_ref):
                continue  # not logged per-row (see module docstring).

            pivot = pivot_ref
            ctx = {
                "date": date, "ticker": ticker,
                "setup_close": c, "pivot": pivot,
            }

            # §4.1b — VCP (Phase 3 entry filter, optional; parameterizable)
            if use_vcp:
                row_pos = df.index.get_loc(date)
                if row_pos < vcp_base_lookback + 5:
                    skips.append({**ctx, "filter_name": "vcp_insufficient_base_bars"})
                    continue
                base = df.iloc[row_pos - vcp_base_lookback : row_pos]
                ok_vcp, why_vcp, vcp_ctx = _gate_vcp(
                    base,
                    min_contractions=vcp_min_contractions,
                    tightening_pct=vcp_tightening_pct,
                    c1_max_pct=vcp_c1_max_pct,
                    volume_dryup_required=vcp_volume_dryup,
                    swing_window=vcp_swing_window,
                )
                if not ok_vcp:
                    skips.append({**ctx, "filter_name": why_vcp, **vcp_ctx})
                    continue

            # §4.2 — Trend Template (all 8)
            ok, why = _gate_trend_template(row, row["sma_200_back"], rs_series.loc[date] if date in rs_series.index else float("nan"))
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # §4.3 — Volume
            ok, why = _gate_volume(row, vol_mult=vol_mult)
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # §4.4 — Pivot extension gate
            ok, why = _gate_extension(c, pivot)
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # §4.5 — SPY regime
            if date not in spy.index:
                skips.append({**ctx, "filter_name": "regime_no_spy_bar"})
                continue
            spy_row = spy.loc[date]
            ok, why = _gate_regime(spy_row)
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # §4.6 — Market breadth
            b = breadth.loc[date] if date in breadth.index else float("nan")
            ok, why = _gate_breadth(b, use_breadth=use_breadth)
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # §4.7 — Earnings blackout
            ok, why = _gate_earnings(earn, date)
            if not ok:
                skips.append({**ctx, "filter_name": why})
                continue

            # PASS — emit candidate signal. Concurrent cap is applied in backtest.
            rs_val = rs_series.loc[date] if date in rs_series.index else float("nan")
            signals.append(Signal(
                ticker=ticker,
                signal_date=date,
                pivot=float(pivot),
                close=float(c),
                volume=float(row["Volume"]),
                vol_avg_50=float(row["vol_avg_50"]),
                vol_ratio=float(row["Volume"] / row["vol_avg_50"]) if row["vol_avg_50"] else float("nan"),
                rs_63_rank=float(rs_val) if not pd.isna(rs_val) else float("nan"),
                spy_close=float(spy_row["Close"]),
                spy_sma_50=float(spy_row["spy_sma_50"]),
                spy_sma_200=float(spy_row["spy_sma_200"]),
            ))

    return signals, skips
