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

## 7. Breakout v1: technical only, no CANSLIM fundamentals

- **Date:** 2026-04-21
- **Decision:** Breakout strategy v1 is pure-technical. No EPS growth, sales acceleration, ROE, or institutional-sponsorship filters.
- **Reason:** yfinance fundamentals are unreliable and stale; a proper fundamentals feed (FMP / EODHD / IEX) costs $20–50/mo and is non-trivial to integrate. Pay for data only after the simpler version clears expectancy.
- **Data:** Research report §7 — yfinance fundamentals quality; §6 — Innovator FFTY ETF (real-money CANSLIM) trailed SPY by ~67pp over 2015–2022, suggesting the fundamental leg adds less than marketing claims. Stripping to the technical core loses little of the documented edge.
- **Reversible?** Yes. Fundamentals can be added as a v1.1 filter layer without restructuring entries or exits.

## 8. Breakout v1 setup: 52-week-high closing break only

- **Date:** 2026-04-21
- **Decision:** The sole setup detector in v1.0 is a close above the prior 252-bar high. No flat base, no cup-with-handle, no double bottom, no VCP.
- **Reason:** The 52-week-high factor is the one peer-reviewed signal in the breakout space. Adding additional base detectors increases implementation surface and introduces discretion (cup curvature, "proper handle", VCP contraction chains) before we have any evidence the core signal works on this stack.
- **Data:** Research report §6 — George & Hwang (2004) *Journal of Finance* documents 0.60–0.94% monthly long-short return on 52-week-high proximity vs 0.45% market baseline; replicated in 18/20 international markets (Liu, Liu, Ma 2011). Research §2 — flat base and cup-with-handle detectors are noisier and less evidence-backed.
- **Reversible?** Yes. Flat-base detector is an explicit v1.1 candidate if signal count runs low.

## 9. Breakout v1 exit: partial-at-1R + trail 10-day MA (baseline), 2R fixed (ablation)

- **Date:** 2026-04-21
- **Decision:** Baseline exit is sell-half at 1R, trail the remainder on a 10-day MA close. Also run a 2R fixed-target backtest on the same entry list as an ablation; final exit choice follows the comparison table.
- **Reason:** Partial+trail is closer to how Minervini actually trades and preserves right-tail winners. 2R fixed is cleaner to backtest and lower-variance — we want both numbers before committing.
- **Data:** Research §5 — "let winners run" captures Minervini's audited 334% (2021) and 220% (1997) but mechanical trailing stops routinely give up 30–50% of paper edge to noise. Running both exits on identical entries isolates the exit-logic contribution.
- **Reversible?** Yes. Exit logic is parameter-driven; the ablation run exists specifically to support reversal.

## 10. Breakout v1 universe: S&P 500 current members

- **Date:** 2026-04-21 (user delegated decision to assistant)
- **Decision:** Backtest on S&P 500 current members. Russell 1000/3000 deferred to v1.1.
- **Reason:** yfinance delivers clean, continuous OHLCV for S&P 500 names. Russell 1000 has gaps on small-caps and yfinance does not return delisted tickers, making true point-in-time backtesting impossible without paid data. Shipping v1 on clean S&P data first, upgrading universe in v1.1 once edge is proven.
- **Data:** Research §7 — yfinance universe quality comparison. Bulkowski 1990–2024 failure-rate study (research §3) shows breakout failure rates have roughly doubled since the 1990s, making survivorship bias a real concern — hence the explicit 1–2pp/year CAGR haircut documented in the spec.
- **Reversible?** Yes. `universe.py` is a single-file abstraction; swapping to Russell or Norgate is isolated.

## 11. Breakout v1 time stop: 60 days (not 20)

- **Date:** 2026-04-21
- **Decision:** Force-exit open breakouts at the close of trading day 60. Supersedes the 20-day stop proposed in the research report.
- **Reason:** Breakout winners can run for months (the whole point of the strategy is the right-tail). 20 days would cut legitimate trends short, especially when paired with a partial-at-1R + trail exit where a stagnant trade rarely reaches 60 days without triggering trail anyway. 60 days is a circuit-breaker for stuck positions, not an active rule.
- **Data:** Research §5 — trailing stops capture tail returns when given room; short time-stops converge to target-based exits and lose the "let winners run" edge. SID's 10-day stop is tuned for mean reversion, which has a different holding-period profile.
- **Reversible?** Yes. Single config constant.

## 12. Breakout v1 adds market-breadth filter (% S&P 500 > 200-day MA ≥ 40%)

- **Date:** 2026-04-21
- **Decision:** Add a new filter layered on top of the SPY > 200-day regime gate: no new entries unless ≥ 40% of S&P 500 constituents are above their own 200-day MA.
- **Reason:** SPY > 200-day can be satisfied by a narrow market (a handful of mega-caps dragging the index up while most stocks decline). Breakouts typically fail in narrow tapes. Breadth is the direct measure of "are a lot of stocks in uptrends?" — the actual condition a breakout strategy needs.
- **Data:** Research §4 — Weinstein Stage 2 overlay and §3 — false-breakout rates jump in narrow regimes. Not a research-novel idea; standard breadth-filter practice in trend-following. Also generalizable to SID (added as a shared-module candidate).
- **Reversible?** Yes. Threshold is a config value; the filter is skippable via `--no-breadth` in the eventual CLI.

## 13. Breakout v1 requires per-filter skip-reason logging

- **Date:** 2026-04-21
- **Decision:** Every setup rejected by a filter is logged with its `filter_name` and context to `breakout_v1_skiplog_YYYYMMDD.csv`. Aggregated counts printed at run end.
- **Reason:** We need to see which filters actually add edge vs which are redundant or harmful. Without per-filter attribution, tuning is guesswork. SID's scanner already logs blocker reasons to Sheets (backlog Self-Learning section notes "Blocker attribution" as an improvement item); baking it in from v1 avoids retrofitting.
- **Data:** Research §3 — false-breakout handling requires measuring *which* filter saved vs killed trades, which is impossible without per-reject logs. Precedent: SID scanner's 19-column Sheets schema includes blocker attribution.
- **Reversible?** N/A — this is instrumentation, not strategy logic. Keep on permanently.

## 14. Breakout v1 has a correlation pass/fail gate vs SID and S&D

- **Date:** 2026-04-21
- **Decision:** During validation, compute the fraction of breakout entry dates that coincide with a SID or S&D entry date (same calendar day). If overlap rate > 40% on the test window, the strategy **fails** and goes back to filter design. Pass/fail line, not a reporting line.
- **Reason:** A breakout system that only fires on days SID and S&D also fire is redundant for portfolio purposes even if per-trade outcomes are uncorrelated. The goal of breakout is to add low-correlation signal flow; the gate enforces that at spec level rather than hoping to notice it in post-hoc analysis.
- **Data:** Research §9 — low-correlation rationale for running multiple strategies. Prior SID ↔ S&D trade-outcome correlation was +0.14 (low); entry-date overlap is a stricter, portfolio-relevant measure.
- **Reversible?** Yes. Threshold is config. But lowering it to accept a failed strategy defeats the purpose — reversal should come from new filter design, not moving the goalpost.
