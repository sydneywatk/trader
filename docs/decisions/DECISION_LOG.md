# Decision Log

Key decisions with date, reason, supporting data, and reversibility. Append new entries at the bottom.

---

## 1. Disabled 2-day RSI reversal exit (bug fix)

- **Date:** 2026-04 (SID development)
- **Decision:** Removed the 2-day RSI reversal exit rule from SID.
- **Reason:** Logic bug — the rule was exiting winners prematurely on intra-trend RSI noise, not on genuine reversals.
- **Data:** Baseline WR improved materially after removal; this was part of the path to the validated 88–89% WR on 99 tickers.
- **Reversible?** Yes — the code path can be re-enabled from config if a corrected version of the rule is designed.

## 2. Added weekly RSI >3pt threshold

- **Date:** 2026-04 (SID filter design)
- **Decision:** Require weekly RSI to have moved >3 points in the signal direction vs the prior week before taking a SID entry.
- **Reason:** Filter out weak higher-timeframe alignment; a tiny weekly move often meant no real trend support for the daily signal.
- **Data:** Contributed to the lift from earlier backtest iterations to the final 88–89% WR. Shows up in the scanner diagnostic output as a common blocker for low-quality signals.
- **Reversible?** Yes — threshold is a single config value; can be loosened to 1–2pt or removed.

## 3. Added 10-day time exit

- **Date:** 2026-04 (SID risk management)
- **Decision:** Force-exit SID trades after 10 trading days if neither target nor stop has been hit.
- **Reason:** Mean-reversion trades that haven't resolved in 10 days are usually dead trades — capital was sitting idle and drawdown variance was rising.
- **Data:** Validated in backtest; removed the long tail of stagnant trades that degraded the Sharpe.
- **Reversible?** Yes — cap is a config constant in `strategies/sid_method/config.py`.

## 4. Disabled short trades in S&D

- **Date:** 2026-04 (S&D Phase 2 analysis)
- **Decision:** Run S&D long-only; suppress DBD (drop-base-drop) short zones in live consideration.
- **Reason:** Long-only 1h run flipped P&L from +$127k (both directions, 35.5% WR) to +$290k (long-only, 39.4% WR). RBR alone contributed +$197k. The short side was a net drag across the test window.
- **Data:** 1h intraday run, 99 tickers, 730d yfinance. Train/test on long-only showed −2.0pp delta → edge persistent. Per-ticker curation failed out-of-sample (−16.4pp), so the long-only decision is universe-wide, not ticker-specific.
- **Reversible?** Yes — config flag. Worth revisiting in a bear regime or once 10+ years of intraday history is available, since 730d only covers a single (bullish) regime.

## 5. Chose daily close over intraday exit

- **Date:** 2026-04 (SID execution study)
- **Decision:** Execute SID entries and exits on daily close; do not use intraday stop or intraday RSI-50 touch exits.
- **Reason:** Full intraday execution study (Tests 1a–4b) measured hourly entry timing, peak exit, hourly stop, and RSI-50 touch exit. All intraday variants underperformed; RSI-50 touch exit alone lost $33–53k vs baseline.
- **Data:** Studies in `strategies/sid_method/studies/`. Effect size was large and consistent across variants.
- **Reversible?** Yes, but the evidence bar to revisit is high. Would need a new thesis (e.g., different exit logic, different timeframe) rather than retrying the same rules.

## 6. Chose yfinance over Alpaca (IBKR Pro pending)

- **Date:** 2026-04 (S&D Phase 2 infrastructure)
- **Decision:** Use yfinance as the primary intraday data source for S&D backtesting, with Alpaca keys as fallback and an eventual move to IBKR Pro.
- **Reason:** yfinance is free and sufficient for the 730d window we needed to validate Phase 2. Alpaca would unlock ~10 years of 1h history for multi-regime validation but wasn't needed to prove the edge exists. IBKR Pro is the long-term target once live execution is approved.
- **Data:** Phase 2 run on yfinance produced clean, reproducible results (3,184 trades, +$127k). Cache layer in `shared/data_intraday.py` makes the vendor swap isolated.
- **Reversible?** Yes — `shared/data_intraday.py` abstracts the vendor. Switching to Alpaca or IBKR is a one-file change. This is the next infra decision on the backlog before paper execution.
