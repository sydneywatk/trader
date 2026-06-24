"""Breakout v1 strategy configuration.

All parameters locked by SPEC §8. Do not tune without updating SPEC + DECISION_LOG.
"""

from shared.config import CACHE_DIR, OUTPUT_DIR  # noqa: F401 — re-exported

# --- Universe & data ---------------------------------------------------------
UNIVERSE = "sp500_current"
DATA_START = "2013-01-01"
TRAIN_START = "2013-01-01"
TRAIN_END = "2019-06-30"
TEST_START = "2019-07-01"
TEST_END = "today"

# --- Account & risk ----------------------------------------------------------
ACCOUNT_SIZE = 100_000
RISK_PCT = 0.01  # 1% account risk per trade

# --- Trend Template (all 8 must pass — SPEC §4.2) ----------------------------
SMA_50 = 50
SMA_150 = 150
SMA_200 = 200
SMA_200_RISING_LOOKBACK_DAYS = 22  # ~1 month
LOW_52W_BUFFER = 1.30              # price >= 1.30 * 52w low
HIGH_52W_BUFFER = 0.75             # price >= 0.75 * 52w high
RS_PERCENTILE_MIN = 70             # cross-sectional 63d-return rank >= 70th pct
RS_LOOKBACK = 63

# --- Volume confirmation -----------------------------------------------------
VOL_AVG_LEN = 50
VOL_MULT = 1.5                     # today's volume >= 1.5 * mean(vol_50)

# --- Pivot extension ---------------------------------------------------------
PIVOT_EXTENSION_MAX = 1.05         # entry <= pivot * 1.05

# --- Market regime (SPY) -----------------------------------------------------
REGIME_SPY_MA = 200
WEAK_TAPE_SPY_MA = 50              # used to choose tight stop

# --- Market breadth ----------------------------------------------------------
BREADTH_MA = 200
BREADTH_THRESHOLD = 0.40           # >= 40% of S&P 500 above 200d MA

# --- Earnings ----------------------------------------------------------------
EARNINGS_BLACKOUT_DAYS = 5

# --- Stops -------------------------------------------------------------------
STOP_PCT_NORMAL = 0.07
STOP_PCT_WEAK_TAPE = 0.05

# --- Exits -------------------------------------------------------------------
EXIT_MODE_BASELINE = "partial_trail"
EXIT_MODE_ABLATION = "fixed_2r"
PARTIAL_R = 1.0                    # scale half at 1R
TRAIL_MA_LEN = 10                  # trail on 10-day SMA after partial
FIXED_TARGET_R = 2.0               # ablation target
TIME_STOP_DAYS = 60
EARNINGS_EXIT_DAYS = 3             # close position if earnings <= 3 days away

# --- Position management -----------------------------------------------------
MAX_CONCURRENT_POSITIONS = 5

# --- VCP (Volatility Contraction Pattern) — Phase 3 entry filter -------------
VCP_ENABLED = True                 # master toggle (ablation runs may override)
VCP_BASE_LOOKBACK = 60             # bars before signal bar scanned for the base
VCP_MIN_CONTRACTIONS = 3           # number of successive H→L contractions required
VCP_TIGHTENING_PCT = 0.30          # each contraction >= 30% tighter than previous
VCP_C1_MAX_PCT = 0.35              # first contraction range <= 35% of price
VCP_VOLUME_DRYUP_REQUIRED = True   # avg vol last 20 bars < avg vol first 20 bars
VCP_SWING_WINDOW = 2               # ±N bars for centered swing pivot detection (5-bar total)

# --- Validation gates --------------------------------------------------------
CORRELATION_GATE_THRESHOLD = 0.40  # same-day entry overlap vs SID+S&D
CORRELATION_GATE_STRATEGIES = ("sid", "sd_long")
TRAIN_TEST_WR_TOLERANCE_PP = 5     # max allowable WR drop train -> test

# --- Paths -------------------------------------------------------------------
UNIVERSE_CACHE = "sp500_constituents.csv"
SKIPLOG_PREFIX = "breakout_v1_skiplog"
TRADELOG_PREFIX = "breakout_v1_backtest"
