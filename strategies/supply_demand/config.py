"""Supply & Demand Zone strategy configuration (Phase 1 — daily charts)."""

import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
_TRADER_ROOT = _HERE.parents[2]
sys.path.insert(0, str(_TRADER_ROOT))

from strategies.sid_method.config import WATCHLIST  # noqa: E402,F401 — reuse same 99 tickers

# Backtest dates
START_DATE = "2020-01-01"
END_DATE = "today"

# Account & risk
ACCOUNT_SIZE = 100_000
RISK_PCT = 0.01  # 1% per trade

# Zone detection (ATR-normalized, not fixed pips)
BASE_MAX_CANDLES = 5            # max candles in base
BASE_RANGE_ATR_MULT = 0.5       # each base candle range <= 0.5 * ATR(20)
BASE_BODY_RATIO_MAX = 0.7       # body/range <= 0.7 (small-bodied candles)
IMPULSE_RANGE_ATR_MULT = 1.5    # impulse range >= 1.5 * ATR(20)
IMPULSE_BODY_RATIO_MIN = 0.5    # impulse body/range >= 0.5 (not a doji)
IMPULSE_CHECK_BARS = 3          # impulse must occur within 3 bars after base
MIN_MOVE_AWAY_ATR = 1.0         # price must travel >= 1 ATR from proximal before tappable

# Zone classification trend window
TREND_LOOKBACK = 5              # bars before base start to determine preceding trend

# Freshness rules
MAX_ZONE_TESTS = 1              # retire zone after 1 touch (strict Seiden)
ZONE_AGE_MAX_DAYS = 60          # daily zones expire after 60 trading days

# Entry trigger
REQUIRE_CONFIRMATION_CANDLE = True  # engulfing or hammer/shooting star

# Stop loss
SL_ATR_BUFFER = 0.5             # stop = distal edge +/- 0.5 * ATR(20)

# Take profit
RR_TARGET = 2.0                 # fixed 2:1 risk:reward

# Max trade duration (trading days)
MAX_TRADE_DAYS = 20             # force exit if no target/stop hit

# ---------------------------------------------------------------------------
# Timeframe mode — selects which of {_DAYS, _BARS} caps apply at runtime.
#   '1d' → daily bars (original Phase 1 behavior)
#   '1h' → 1-hour bars (Phase 2 intraday)
# main_sd.py flips this via USE_INTRADAY / --intraday.
# ---------------------------------------------------------------------------
TIMEFRAME = "1d"

# 1-hour timeframe parameters (used when TIMEFRAME == '1h')
BARS_PER_DAY = 7                # 09:30, 10:30, ..., 15:30 ET = 7 hourly opens
ZONE_AGE_MAX_BARS = 140         # 20 trading days × 7 bars (research: 1H zone life ~20d)
MAX_TRADE_BARS = 140            # max hold time in 1h bars
MIN_MOVE_AWAY_BARS = 3          # zone must move >= MIN_MOVE_AWAY_ATR within this many bars

# HTF trend filter (simplified: rolling mean instead of multi-TF)
HTF_TREND_SMA = 50              # daily: 50-day SMA
HTF_TREND_SMA_INTRADAY = 350    # 1h: 350 bars ≈ 50 trading days — daily-trend proxy
REQUIRE_HTF_ALIGNMENT = True

# Earnings filter
EARNINGS_MIN_DAYS = 7           # days before earnings to avoid entry
EARNINGS_EXIT_DAYS = 3          # exit trade if earnings within this many days

# Zone priority filter
SKIP_CONTINUATION_ZONES = False  # True = only trade DBR/RBD (high priority)

# Output paths (resolved absolutely from trader/ root)
OUTPUT_DIR = str(_TRADER_ROOT / "output")
CACHE_DIR = str(_TRADER_ROOT / "cache")


# ---------------------------------------------------------------------------
# Runtime accessors — read TIMEFRAME *now*, so flipping it mid-run works.
# zones.py and backtest_sd.py call these instead of reading module-level
# constants captured at import time.
# ---------------------------------------------------------------------------
def get_zone_age_cap() -> int:
    """Max bars a zone stays active after formation."""
    return ZONE_AGE_MAX_BARS if TIMEFRAME == "1h" else ZONE_AGE_MAX_DAYS


def get_max_trade_period() -> int:
    """Max bars to hold a trade before forced time-exit."""
    return MAX_TRADE_BARS if TIMEFRAME == "1h" else MAX_TRADE_DAYS


def get_move_away_window() -> int:
    """Bars after formation by which a zone must clear MIN_MOVE_AWAY_ATR
    from the proximal edge. On daily, impulse candles satisfy this on bar 0,
    so 1 is effectively a no-op. On 1h we allow a few bars for follow-through.
    """
    return MIN_MOVE_AWAY_BARS if TIMEFRAME == "1h" else 1
