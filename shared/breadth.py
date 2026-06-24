"""Cross-strategy breadth calculations.

compute_breadth_above_ma: % of a ticker list above a given SMA, per bar.
Used by breakout v1 (breadth_200 >= 0.40 gate); reusable by other strategies.
"""

from typing import Mapping

import numpy as np
import pandas as pd


def compute_breadth_above_ma(prices: Mapping[str, pd.DataFrame],
                              ma_len: int = 200,
                              price_col: str = "Close") -> pd.Series:
    """Return a date-indexed Series: fraction of tickers with close > SMA(ma_len).

    For each date, the denominator is the number of tickers with a finite
    SMA(ma_len) and close on that date. Tickers not yet listed (NaN rows) are
    excluded from that date's ratio — this is the honest definition.
    """
    frames = []
    for ticker, df in prices.items():
        if df is None or df.empty or price_col not in df.columns:
            continue
        close = df[price_col]
        sma = close.rolling(ma_len, min_periods=ma_len).mean()
        # 1.0 if close > sma; 0.0 if close <= sma; NaN if sma undefined.
        above = close.gt(sma).astype(float)
        above = above.where(sma.notna(), np.nan)
        frames.append(above.rename(ticker))

    if not frames:
        return pd.Series(dtype=float)

    wide = pd.concat(frames, axis=1)
    denom = wide.notna().sum(axis=1).astype(float)
    numer = wide.sum(axis=1, skipna=True).astype(float)
    ratio = np.where(denom > 0, numer / denom, np.nan)
    return pd.Series(ratio, index=wide.index, name=f"breadth_{ma_len}")
