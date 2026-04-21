"""Shared configuration constants usable across strategies."""

import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Backtest date range (default — individual strategies may override)
START_DATE = "2020-01-01"
END_DATE = "today"

# Standard indicator periods
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9

# Cross-strategy earnings buffer
EARNINGS_MIN_DAYS = 14

# Platform-level paths (resolved from trader/ root)
CACHE_DIR = os.path.join(_PROJECT_ROOT, "cache")
OUTPUT_DIR = os.path.join(_PROJECT_ROOT, "output")
