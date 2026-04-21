"""RSI signal detection — Step 1 of the SID Method."""

import pandas as pd

from config import RSI_OVERSOLD, RSI_OVERBOUGHT


def find_rsi_signals(daily_df: pd.DataFrame) -> list[dict]:
    """Scan daily RSI for crosses below oversold or above overbought.

    Returns a list of dicts with keys:
        - date: signal date (pd.Timestamp)
        - type: "OS" (oversold / long) or "OB" (overbought / short)
        - rsi: RSI value on signal date
    """
    signals = []
    rsi = daily_df["RSI"]

    for i in range(1, len(rsi)):
        if pd.isna(rsi.iloc[i]) or pd.isna(rsi.iloc[i - 1]):
            continue

        prev = rsi.iloc[i - 1]
        curr = rsi.iloc[i]

        # Crossing below oversold threshold
        if prev >= RSI_OVERSOLD and curr < RSI_OVERSOLD:
            signals.append({
                "date": daily_df.index[i],
                "type": "OS",
                "rsi": curr,
            })

        # Crossing above overbought threshold
        if prev <= RSI_OVERBOUGHT and curr > RSI_OVERBOUGHT:
            signals.append({
                "date": daily_df.index[i],
                "type": "OB",
                "rsi": curr,
            })

    return signals
