# Supply & Demand Zone Trading — Research Report

## 1. GitHub Repo Analysis

### rbhatia46/Demand-Supply-Identification-Python
- **What it is:** A single Jupyter notebook, ~55KB. Fetches NIFTY index via yfinance, plots horizontal lines.
- **Algorithm:** Classic 5-bar fractal pivot detection. A pivot low qualifies when `low[i] < low[i-1] < low[i-2]` AND `low[i] < low[i+1] < low[i+2]` (symmetric). Pivots within one mean-candle-range of an existing level are discarded to dedupe.
- **Output:** Single-price **horizontal lines** — not zones (no top/bottom band).
- **Win rate:** None. No trade logic, no backtest.
- **Data format:** yfinance daily OHLC.
- **Quality:** Educational demo, not a trading system. The underlying fractal is useful as a *component*, but this repo will not give you an S&D strategy.

### efitzkiwi/NT8SupplyDemandDTBot
- **What it is:** NinjaTrader 8 C# bot for TSLA 1-minute intraday. ~220KB across `AdvancedSRZones.cs` (80KB indicator) and `SupplyDemand.cs` (140KB strategy). **Archived Aug 2021** — moved to a private repo, the public version is stale.
- **Algorithm (from source comments, which are effectively a design doc):**
  - Zone creation = candlestick patterns (hammers, engulfing) at swing points + volume profile
  - Dynamic flip: a support zone becomes resistance once price closes below it and stays
  - Zone strength tracked via bounces (gains) vs penetrations (loses); merges nearby "skinny" zones below avg height ± stdev
  - Consolidation (base) defined as flat zone ≤ ~0.15% of price
  - Fallback "virtual zones" at psychological round numbers
- **Win rate:** Not stated anywhere in repo.
- **Data format:** NinjaTrader tick/minute feed; requires NT8 license and a data subscription.
- **Quality:** This is the most *thoughtful* design of the three — the comments double as the best spec I found for zone detection. But it is **not reusable code** for your Python stack (C#/.NET, NinjaScript APIs, heavy GUI drawing logic). Useful as a *design reference*, not a dependency.

### IshaanLabs/Demand-Supply-Analysis
- **Not relevant to trading.** This repo is about cab/ride-sharing demand vs driver supply — it uses `rides.csv` (drivers-per-hour, riders-per-hour, completed rides) and is a business-analytics tutorial in Jupyter. Do not spend more time on it.

## 2. Additional Python Implementations Worth Knowing

| Repo | Stars | License | Relevance |
|---|---|---|---|
| **joshyattridge/smart-money-concepts** | 1,503 | MIT | **Strongest candidate.** Active (updated 2026-04-20), on PyPI as `smartmoneyconcepts`. Provides `ob()` (order blocks), `fvg()` (fair value gaps), `swing_highs_lows()`, `bos_choch()`, `liquidity()`, `sessions()`, `retracements()`. Accepts lowercase-column OHLCV DataFrame — trivially compatible with your `data.py`. No built-in backtest (users roll their own). |
| aaronlwan/supply-demand-deep-learning | 4 | None | Experimental CNN on TPO market-profile images. Cites IEEE paper 9693504. Small, unmaintained — interesting idea, not production. |
| smtlab/smartmoneyconcepts, DACILAE1777/smart-money-concepts-1, tpwilo/smc | — | — | Forks of joshyattridge. Skip unless you need a specific PR. |

## 3. Academic / Quantitative Reality Check

- **No peer-reviewed studies on S&D zone effectiveness.** One IEEE paper (9693504) on TPO + CNN exists but is not focused on S&D per se.
- **QuantifiedStrategies.com explicitly says** a meaningful backtest of S&D "cannot be made" objectively because zone definition is subjective — most "backtests" are manual chart reviews with hindsight bias.
- **"94% win rate" claims in blogs/courses are marketing**, not data. Ignore them.
- **Realistic algorithmic win rates** (from practitioner reports where trading rules *are* codified): **45–60%** with filters; **35–50%** naive. Because zone trades typically target 1.5–3R with stops just beyond the distal line, the system can be profitable at ≤50% WR if R:R is disciplined.
- **Primary failure modes** (consistent across sources):
  1. **Zone freshness decay** — 1st test strongest; 3rd test dangerous; 4th rarely works
  2. **False breakouts / stop runs** — price pokes through, triggers stops, reverses ("spring")
  3. **Continuation vs reversal confusion** — Seiden's RBR/DBD (continuation) zones have notably weaker edge than DBR/RBD (reversal) zones
  4. **No trend filter** — zones against the prevailing trend fail far more often
  5. **Subjective base definition** — without a strict programmatic definition of "small base + strong impulse", detection is noisy

## 4. Compatibility with Your Existing Stack

Reviewed `data.py`, `indicators.py`, `backtest.py`:

| Component | Reusable? | Notes |
|---|---|---|
| `data.py` (yfinance daily/weekly + caching) | **Yes, as-is** for daily/weekly zones. | yfinance limits intraday to 7 days (1m) / 60 days (5m) — if you want intraday zones, swap in Polygon or Alpaca. |
| `indicators.py` (RSI/MACD/SMA) | **Yes** — reuse as zone confluence filters (e.g., only take demand zones where daily RSI < 40). |
| `backtest.py` entry→stop→exit→sizing framework | **Yes** — the structure (find signal → find entry → compute stop → size by RISK_PCT → track exit) maps cleanly to zone trading. Replace `_check_entry_conditions` with a "price touched fresh zone" check and `_calc_stop_loss` with "distal zone edge ± buffer". |
| `earnings.py` + SPY alignment | **Yes** — both directly applicable as filters. |
| `scanner_universe.py` + watchlist | **Yes** — 99-ticker universe works fine. |

**New modules needed:**
1. `zones.py` — detect + classify zones (RBR/DBD/DBR/RBD), track freshness/test count, age-decay
2. `zone_signals.py` — emit "zone-touch" signals analogous to your current RSI OS/OB signals
3. One small change in `backtest.py` entry logic to consume zone signals

## 5. Research Report — Recommendations

### Best zone-detection algorithm (synthesis)
Use a hybrid: **joshyattridge's `smc` library for the primitives** (swing points, order blocks, FVG) + a **Sam-Seiden-style base classifier layered on top**:

1. Swing points via `smc.swing_highs_lows(ohlc, swing_length=10)` (10 for daily, tune per TF).
2. Define **base candles**: 1–5 consecutive candles where `body/range < 0.5` and `range < 0.5 * ATR(20)`.
3. Define **impulse**: adjacent candle with `range > 1.5 * ATR(20)` and body `> 0.65 * range`, closing in the direction of the move.
4. Classify using the 4 Seiden patterns:
   - **DBR** (drop→base→rally) → **demand zone** (strongest)
   - **RBD** (rally→base→drop) → **supply zone** (strongest)
   - **RBR** / **DBD** → continuation zones (lower priority)
5. **Zone boundaries** = `[min(base.low, impulse.open), max(base.high, impulse.open)]` — tighter than using wick extremes.
6. Track **freshness**: increment test count each time price re-enters; retire at 3 tests.
7. Flip type on confirmed close through the zone (broken demand → supply).

### Recommended entry criteria
- Price enters zone (`low ≤ zone.proximal` for demand / `high ≥ zone.proximal` for supply).
- **Confirmation candle** (required — skip "set and forget" limit orders): bullish engulfing or hammer at demand; bearish engulfing or shooting star at supply. This single filter is the biggest edge improvement vs naive limit entries.
- Align with existing SPY filter (reuse `_check_spy_alignment`): long only when SPY trend up, short only when SPY trend down.
- Skip if zone is not fresh (test_count ≥ 2) or older than ~60 trading days.
- Earnings check via existing `earnings.py`.

### Realistic win-rate expectations
- **45–55% WR** with above filters, targeting 2R exits → positive expectancy (~0.3–0.5R per trade).
- Budget for **35–45% WR** in your first backtest; anything above that is encouraging.
- Be suspicious of any >70% WR result — almost always look-ahead bias or zone-redefinition leakage.

### Data requirements
- **Primary: yfinance daily** — free, already wired, sufficient for daily-zone swing trades (hold 2–15 days). Matches your current SID workflow.
- **Optional: Polygon (paid, ~$30/mo stocks starter) or Alpaca (free w/ account)** if you want intraday zones (15-min, 1-H). Avoid yfinance for intraday past 60 days.
- **Execution: IBKR paper** via your existing `ibkr_paper.py`.

### Build complexity estimate
| Milestone | Estimate |
|---|---|
| Daily-zone MVP: SMC library + Seiden classifier + simple touch entry, hooked into existing backtest framework | **3–5 days** |
| Add freshness tracking, confirmation candle, trend/earnings filters, tier scoring | **+3–5 days** |
| Full train/test validation on your 99-ticker universe (mirror SID methodology) | **+3–5 days** |
| Total to production-validated daily zone system | **~2–3 weeks** |
| Extension to multi-timeframe (weekly zone + daily entry) | **+1 week** |
| Extension to intraday zones (minute bars, new data source) | **+2–3 weeks** |

### Recommended approach for your stack
1. `pip install smartmoneyconcepts` (MIT, 1.5k stars, active today) — don't reinvent swing/OB primitives.
2. Build `zones.py` as the Seiden classifier + freshness tracker on top of SMC primitives.
3. Reuse `data.py`, `indicators.py`, `earnings.py`, SPY alignment, risk sizing, IBKR paper — all directly applicable.
4. Add a `zones` signal source parallel to your current RSI signals in `signals.py`; let `backtest.py` consume either.
5. Validate identically to SID: train on one half, held-out test on the other, report WR *and* expectancy (not just WR — zone trading's edge is in R:R, not hit rate).
6. **Do not plan to beat SID's 88% WR** — that bar is unrealistic for zone trading. A 50% WR at 2R is a great outcome and complements SID (they'll fire on different setups).

### Go / no-go take
Worth building as a **second strategy alongside SID**, not a replacement. The joshyattridge library de-risks the hardest part (swing/OB detection), your existing backtest/sizing/filter infrastructure is reusable, and EOD daily zones are tractable with data you already pull. Realistic expected edge: 50% WR × 2R ≈ **0.5R expectancy per trade**, meaningfully additive to SID if signal correlation is low.

## Sources
- rbhatia46/Demand-Supply-Identification-Python — https://github.com/rbhatia46/Demand-Supply-Identification-Python
- efitzkiwi/NT8SupplyDemandDTBot — https://github.com/efitzkiwi/NT8SupplyDemandDTBot
- IshaanLabs/Demand-Supply-Analysis (not applicable) — https://github.com/IshaanLabs/Demand-Supply-Analysis
- joshyattridge/smart-money-concepts — https://github.com/joshyattridge/smart-money-concepts
- aaronlwan/supply-demand-deep-learning — https://github.com/aaronlwan/supply-demand-deep-learning
- Sam Seiden's Two Types of S&D Zones — https://priceactionninja.com/sam-seiden-supply-and-demand/
- S&D backtest difficulties (QuantifiedStrategies) — https://www.quantifiedstrategies.com/supply-and-demand-trading-strategy/
- Anatomy of a valid order block (SMC) — https://liquidityfinder.com/news/anatomy-of-a-valid-order-block-in-smart-money-concepts-67221
- Zone freshness + failure modes (Tradeciety) — https://tradeciety.com/the-6-golden-rules-of-trading-supply-and-demand
- Backtesting SMC limitations (HorizonAI) — https://www.horizontrading.ai/learn/backtesting-smart-money-concepts
