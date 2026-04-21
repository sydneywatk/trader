"""Configuration constants for the SID Method Backtester."""

import os

_PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

WATCHLIST = [
    # Top 100 from universe scan (2026-04-15), ranked by WR desc.
    # GOOG removed (50% WR underperformer). Stars (★) = Sid's original list.
    "GPN", "NUGT",   # ★ | 100% WR, 12 trades
    "AKAM", "CME", "CMG", "FIS", "HBAN", "MKC", "RF",  # 100% WR, 11 trades
    "APO", "COST", "FANG", "MS", "WM", "XLU",  # ★ | 100% WR, 10 trades
    "PAYX", "TSLA",  # ★ | 92.9% WR, 14 trades
    "BLK", "CMCSA",  # 92.3% WR, 13 trades
    "CDW", "ED", "GDX", "HPE", "HRL", "MRSH", "OKE", "XLV",  # ★ | 91.7%
    "AEP", "CHTR", "CTRA", "ELF", "IP", "KEYS", "SATS", "TLT", "XLC", "ZTS",  # ★ | 90.9%
    "ABNB", "ACGL", "AFL", "APTV", "AVB", "CNP", "CPB", "F",  # 90.0%
    "GM", "GS", "HST", "HUT", "IT", "JBHT", "JCI", "JPM",  # ★ | 90.0%
    "LMT", "MAR", "MTB", "NUE", "PKG", "PPG", "REG", "SO",  # 90.0%
    "TPR", "UNP", "XLI", "XOP",  # ★ | 90.0%
    "BIIB",  # 85.7%, 14 trades
    "AMT", "CSCO", "ROST", "TKO",  # 84.6%, 13 trades
    "DIS", "DXCM", "EMR", "PG",  # ★ | 83.3%, 12 trades
    "ADBE", "ANET", "BALL", "BG", "BMY", "CCI", "CF", "COP",  # 81.8%, 11 trades
    "CTVA", "DDOG", "EFA", "EXEL", "FCX", "GDXJ", "LUV", "LYV",  # ★ | 81.8%
    "MAS", "MRK", "UDR", "USB", "VTR",  # 81.8%, 11 trades
    "AAPL", "DGX", "DOC", "EQIX",  # ★ | 80.0%, 10 trades (FOX removed — 50% test WR)
]

# Backtest date range
START_DATE = "2020-01-01"
END_DATE = "today"  # or specific date like "2025-12-31"

# Account and risk settings
ACCOUNT_SIZE = 100_000
RISK_PCT = 0.01  # 1%

# Indicator settings
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# SID Method thresholds
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_EXIT = 50
EARNINGS_MIN_DAYS = 14
WEEKLY_RSI_MIN_DELTA = 3  # Weekly RSI must move MORE than this many points to qualify as aligned
MAX_TRADE_DAYS = 10  # Force exit if trade open this many trading days without RSI reaching 50

# Ranking thresholds
MIN_QUALIFYING_TRADES = 15

# File paths (absolute, resolved from project root)
CACHE_DIR = os.path.join(_PROJECT_ROOT, "cache")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
