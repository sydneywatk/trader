# Phase 2 — Gap Analysis: SID vs Published Work

_Compares the SID Method as actually implemented in `strategies/sid_method/` against the prior art surveyed in Phase 1._

## What "SID" actually is in this repo

The trader-repo implementation is meaningfully different from Naiman's public discretionary method. Source: `strategies/sid_method/signals.py`, `strategies/sid_method/backtest.py`, `strategies/sid_method/config.py`, plus the shared `shared/indicators.py`.

### Entry conditions (all must hold on the entry bar, which must be after the signal bar)

| Tag | Condition |
|---|---|
| Signal | Daily RSI(14) crossed below 30 (long) / above 70 (short) on a prior bar; running lowest-low / highest-high tracked since |
| A1 | Daily RSI is now rising (long) or falling (short) day-over-day |
| A2 | MACD(12, 26, 9): either MACD-line > signal-line (long) OR histogram positive AND increasing day-over-day. Mirror for short |
| B | Weekly RSI(14) moved **strictly more than +3 points** in trade direction since prior completed week |
| C | Next earnings is ≥14 days away (if data available) |
| D | Daily RSI hasn't already crossed past 50 ("no room to run" gap-skip) |
| E | SPY regime aligned: long → SPY RSI rising AND SPY close > SPY SMA50; short → SPY RSI falling AND SPY close < SPY SMA50 × 1.02 |

### Stop loss

Lowest low between signal bar and entry bar inclusive (long), or highest high (short). Rounded to whole dollar; -1 (or +1 for short) if already whole. Code: `backtest._calc_stop_loss`.

### Exits (priority order)

1. Stop loss (resting price level)
2. Daily RSI reaches 50 — take profit
3. 10 trading days held — time exit
4. Last trading day before next earnings — earnings exit

The 2-day RSI reversal exit Naiman documents is **intentionally disabled** in code (`backtest.py:238–243`); the codebase records that it caught oscillations rather than reversals in daily-bar backtests, with a ~21% WR on the 48% of trades it triggered.

### Sizing

`max_shares = floor(account * RISK_PCT / risk_per_share)` with `ACCOUNT_SIZE = 100_000` and `RISK_PCT = 0.01`.

### Universe

99 hand-curated tickers, ranked by historical WR in the yfinance backtest (config.py:19–40). 81 of these are large-cap S&P 500 names; 12 are sector / leveraged / commodity ETFs; FOX removed after a train/test study found 50% out-of-sample WR.

## Where this overlaps published work, and where it does not

| SID rule | Status in published daily RSI+MACD equity work |
|---|---|
| Daily RSI(14) cross of 30/70 | **Common.** QuantifiedStrategies, Trading Cafe write-up, generic descriptions all use this. |
| MACD(12, 26, 9) confirmation in same direction | **Common but loosely defined.** QS describes "MACD rising above signal"; SID adds an explicit histogram-positive-and-increasing fallback. |
| Multi-timeframe RSI confirmation | **Rare and never quantified.** Trading Cafe mentions "trade in direction of higher-timeframe RSI" but doesn't define what "in direction" means. SID's "weekly RSI moved >3 points in trade direction" is the only quantification I found. |
| SPY regime filter | **Absent.** Closest is the Bubble Algorithm (CAPE-based), which is a fundamentally different regime signal. The Alpha Formula port uses none. |
| "No room to run" RSI<50 gate | **Absent.** No public source declines an entry on the grounds that RSI has already mean-reverted past the midline. |
| Structural lowest-low / highest-high stop with whole-dollar rounding | **Absent.** Public sources use fixed % stops (Alpha Formula -10%) or unspecified stops. |
| Layered exit: RSI=50 / time / earnings / stop | **Absent.** Most public RSI mean-reversion strategies use a single exit (e.g. RSI=opposite-extreme, or filter-reversal). |
| Earnings-proximity filter (≥14 days) | **Rarely cited.** Trading Cafe mentions "never trade within 14 days of earnings"; no public LEAN/QC strategy I found implements this systematically. |
| Hand-curated universe of 99 megacap S&P 500 + sector ETFs | **Absent.** Public sources are single-ETF (XLP), wholesale S&P 500, or index-only. |
| 1%-account-risk sizing tied to a structural stop | **Common in concept** (any structured risk-mgmt strategy uses this), but not paired with a lowest-low stop in any of the surveyed sources. |
| Signal-vs-entry separation across bars | **Absent.** Public RSI+MACD strategies fire on single-bar confluence. |

## Where published implementations would diverge from SID at runtime

Predictions, not measurements — to be validated against the QC port output:

1. **QuantifiedStrategies XLP variant (73% / 235 / -46% MDD)** — would likely produce more trades than SID on the same universe because it has no weekly-RSI gate and no SPY regime. WR would likely be **lower** than SID's gated version because the additional confluence requirements act as confirmation filters. MDD would likely be **higher** without the regime filter blocking long entries in SPY-RSI-falling markets.
2. **Alpha Formula port (63% / 22.8% MDD, S&P 500)** — fewer indicators, more trades, lower WR, similar-or-worse MDD. The -10% fixed stop is much wider than SID's structural lowest-low stop, so single losers cost more in % terms but get hit less often than a tight whole-dollar stop.
3. **Bubble Algorithm with CAPE-based regime** — would trade rarely (CAPE is a slow-moving signal), so trade count would be a fraction of SID's. WR not directly comparable due to small sample.
4. **None of the published implementations would produce SID's claimed 89.3% WR** — the headline gap is the combination of confluence filters; whether the gap survives QC's clean data and IBKR fills is the open question this port is designed to answer.

## What the QC port actually validates

The QC port is intended to disentangle the three plausible explanations for the 89.3% backtest WR:

| Source of optimism | Test |
|---|---|
| Survivorship bias (yfinance gives me only the names that still trade) | QC uses point-in-time-survivorship-corrected data via its Equity universe |
| Unrealistic fills (yfinance close-to-close, no slippage) | QC's IBKR brokerage model simulates fills with realistic spreads, commissions, and partial fills |
| Universe curation (selecting 99 tickers AFTER seeing their backtest WR) | Out of scope for this port — to fully test, run the QC port on a random 99-ticker S&P 500 sample as a follow-up. **The current port matches the curated universe exactly**, so a high WR here still leaves selection-bias as a confound. |

If the QC port returns **70-78% WR** on the curated universe, that is the strongest signal: the strategy edge is real but the yfinance backtest's 89% reflected survivorship + fills, not selection. If the QC port returns **60-70%**, that aligns roughly with the Alpha Formula baseline and would suggest the SID confluence filters are useful but the edge is smaller than the discretionary track record implies. **<60%** would suggest selection bias is dominant and the universe needs to be rebuilt without hindsight.

## What the *novel contribution* of porting SID to QC would be

Two pieces of public knowledge that would be genuinely additive if this port runs cleanly:

1. **The first published QuantConnect / LEAN implementation of the SID Method**, with a documented backtest on a curated universe with IBKR fills. No prior art exists per Phase 1.
2. **The first apples-to-apples comparison of a daily RSI+MACD mean-reversion stack with and without each gating filter** (weekly-RSI, SPY regime, RSI<50 gate, earnings) — by running the parameter sweep harness with each filter toggled off, you can isolate the WR contribution of each layer. This is more rigorous than the trader-repo's current "remove FOX → 88% → 89%" iterative tuning.

A public-facing write-up of those two artefacts would have real signal value to the QC community.

## Bonus 1 — SID-style logic on E-mini futures (Vu paper's asset)

**Short answer: structurally feasible, likely worse risk-adjusted.**

Reasoning:

- The Vu & Bhattacharyya paper backtests mean reversion on **intraday** futures. SID is **daily**. Daily ES (S&P 500 E-mini) mean reversion is plausible — ES is highly liquid, has reliable RSI/MACD behavior, and IBKR supports futures with realistic fill modeling in QC.
- However: ES is a single instrument. SID's edge comes partly from cross-sectional opportunity — having 99 names increases the chance that, on any given day, at least one is offering a fresh RSI<30 setup with the full confluence stack. A single-instrument SID variant would trade rarely (maybe 5-15 times/year on ES).
- The SPY regime filter doesn't make sense on ES — SPY *is* the regime. You'd need to replace it with a higher-timeframe ES filter or remove it entirely.
- Earnings filter doesn't apply.
- Pro: structural stops on ES are well-defined (tick-based). Con: ES is overnight-trades-on-Globex, so the "lowest low of signal-to-entry window" needs to handle 23-hour sessions differently than equity day sessions.

**Recommended bonus path:** if you want to explore this, build it as a separate strategy in the trader repo (e.g. `strategies/sid_es/`) rather than overloading the SID code. The two share method DNA but the operational details diverge enough that a single class would get messy. **Not blocking** for the QC port.

## Bonus 2 — ADF test for universe selection

The Vu paper uses Augmented Dickey-Fuller tests to screen for mean-reverting series before running their strategies. This is gold for SID because the current 99-ticker universe was selected by **historical win rate**, which is itself the metric the QC port is meant to validate — a circular selection process.

**Concrete recommendation, in order of cost/benefit:**

1. **Quick win, low risk.** Add an ADF screen step to `strategies/sid_method/universe.py`: take a broad candidate list (current 99 + S&P 500), compute the ADF test statistic on each ticker's 5-year daily log-returns, keep tickers with p < 0.05 (rejects unit root → series is stationary → mean-reverting). Compare overlap with current 99. Names that flunk ADF but are in the current 99 are suspects for survivorship-bias-driven inclusion; names that pass ADF but aren't in the current 99 are candidates for a future universe expansion.
2. **Medium cost, higher signal.** Once the QC port runs, run the QC backtest on the ADF-screened universe and compare WR / Sharpe / trade count to the current 99-ticker run. If the ADF universe performs comparably with materially less curation, that's strong evidence the current selection is fine. If it underperforms badly, that suggests the current 99 captures something ADF doesn't.
3. **Stretch.** ADF is a unit-root test on the level series; for mean-reversion strategies the relevant question is often whether the series is mean-reverting around a drift, which is what the Hurst exponent or a half-life-of-mean-reversion calc measures. Worth a follow-up but ADF is the right first cut.

**Implementation cost:** ~30 minutes. `statsmodels.tsa.stattools.adfuller` over ~500 candidate tickers using already-cached daily data in `cache/`. Should be a simple `strategies/sid_method/studies/adf_universe.py` script. **Not blocking** for the QC port.

## Summary

SID's combination of confluence filters is novel relative to public daily RSI+MACD work, but several gating rules (weekly RSI quantification, SPY two-leg regime, RSI<50 gate, structural stops with whole-dollar rounding) are repo-specific and bear directly on the 89.3% backtest claim. The QC port matters because no public source independently corroborates that number, and the closest LEAN benchmarks (Alpha Formula 63%, QuantifiedStrategies 73%) suggest a realistic landing zone of **65-78% WR**. Validation success is the strategy landing in that range with comparable trade count (~700) and positive risk-adjusted return; validation failure is materially lower WR with comparable trade count, which would point to survivorship + fills as the dominant inflator and require revisiting universe selection before paper trading.
