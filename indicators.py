"""Technical indicator calculations using pure pandas."""

import pandas as pd

from config import RSI_PERIOD, MACD_FAST, MACD_SLOW, MACD_SIGNAL


def _rsi(series: pd.Series, period: int) -> pd.Series:
    """Calculate RSI using exponential moving average (Wilder's smoothing)."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, min_periods=period, adjust=False).mean()

    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def _macd(series: pd.Series, fast: int, slow: int, signal: int) -> pd.DataFrame:
    """Calculate MACD line, histogram, and signal line."""
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return pd.DataFrame({
        "MACD": macd_line,
        "MACD_hist": histogram,
        "MACD_signal": signal_line,
    }, index=series.index)


def add_daily_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI, MACD, and SMA50 columns to a daily DataFrame."""
    df = df.copy()
    df["RSI"] = _rsi(df["Close"], RSI_PERIOD)

    macd_df = _macd(df["Close"], MACD_FAST, MACD_SLOW, MACD_SIGNAL)
    df["MACD"] = macd_df["MACD"]
    df["MACD_hist"] = macd_df["MACD_hist"]
    df["MACD_signal"] = macd_df["MACD_signal"]

    df["SMA50"] = df["Close"].rolling(window=50).mean()

    return df


def add_weekly_rsi(df: pd.DataFrame) -> pd.DataFrame:
    """Add RSI column to a weekly DataFrame."""
    df = df.copy()
    df["RSI"] = _rsi(df["Close"], RSI_PERIOD)
    return df
