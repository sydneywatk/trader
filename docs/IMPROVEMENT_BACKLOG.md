# Improvement Backlog

Prioritized ideas across both strategies. Items are not yet scheduled — pick from here when starting a new work block.

## Signal Quality

- **Volume confirmation on SID entries** — require above-average volume on the signal bar to filter low-conviction reversals.
- **Relative strength vs SPY** — rank SID longs by RS; prefer names outperforming SPY over the last 20d when SPY regime is mixed.
- **Sector breadth filter** — only take longs in sectors where >50% of constituents are above their 50-day MA.
- **ATR-normalized RSI thresholds for S&D** — the 1h confirmation rule was loosened to color+close-back; test ATR-relative base/impulse thresholds per ticker rather than a single global value.
- **RBR-only mode as a shipping strategy** — RBR contributed +$197k of the +$290k long-only P&L; evaluate running RBR exclusively to simplify and concentrate edge.

## Entry / Exit

- **Limit entries at zone proximal for S&D** — currently enters at confirmation bar close; test resting limits at proximal to capture better fills.
- **Partial profit at 1R** — scale out half at 1R, trail the remainder to reduce variance.
- **Time-stop tuning for S&D** — SID uses day-10; S&D has no explicit time stop. Test 5/10/15-bar caps on 1h.
- **Intraday stop trigger for SID** — backtested; conclusion was daily-close execution wins. Revisit if a cheaper intraday data feed becomes available.
- **Exit on weekly RSI flip** — for SID longs, exit when weekly RSI crosses back below its prior week's reading.

## Risk Management

- **Portfolio-level concurrent trade cap** — limit open positions to N (e.g. 5) to prevent correlated drawdowns on broad-market reversals.
- **Correlation-aware sizing** — reduce size when a new signal correlates >0.7 with an open position.
- **Volatility-scaled risk per trade** — reduce the 1% risk budget when VIX > 25.
- **Per-strategy equity tracking** — separate P&L curves for SID vs S&D so one strategy's drawdown doesn't mask the other's.
- **Daily loss cap** — halt new entries for the day after N% account drawdown.

## Options

- **Tier 1 options expression** — spec out the contract selection for Tier 1 SID signals (strike, DTE, bid/ask filter). Scanner already flags Tier 1 candidates.
- **Defined-risk structures** — vertical spreads vs long calls/puts; decide based on IV rank at signal.
- **Roll rules** — define when to roll vs close on Tier 1 positions.
- **IV filter** — skip options entries when IV percentile > 80 (premium too expensive).

## Universe

- **Expand beyond 99-ticker SID watchlist** — run the full backtest on S&P 500 or Russell 1000 to measure universe sensitivity.
- **Add crypto or futures sleeves** — if the edge is regime-driven, it may extend to other asset classes.
- **Ticker-level blacklist** — names with chronic earnings gap failures or news-driven moves that defy mean reversion.
- **Reconsider per-ticker curation** — honest train/test showed −16.4pp degradation; revisit only with walk-forward or a much larger sample.

## Self-Learning

- **Live vs backtest slippage tracking** — log expected fill vs actual fill per paper trade; build slippage model.
- **Signal outcome database** — every scanner signal (taken or skipped) logged with outcome; use to retune filters quarterly.
- **Blocker attribution** — scanner already outputs why a watching signal was blocked; aggregate to find the most common kill reason and decide whether to relax that filter.
- **Regime-tagged performance** — split results by SPY trend / VIX regime to see where edge concentrates.

## Infrastructure

- **Move from yfinance to a paid data feed** — IBKR Pro or Polygon for 10+ years of intraday history and reliable real-time quotes. Blocked on deciding the data vendor.
- **Centralize config** — both strategies have their own `config.py`; consider a shared base for universe, risk, data paths.
- **Test suite** — zero tests currently; add at minimum a smoke test per entry point and a golden-file test for each backtester.
- **CI caching** — GitHub Actions reruns data pulls on every scanner run; cache yfinance pulls by date to cut runtime.
- **Alerting on scanner failure** — currently email only fires on successful signal generation; also fire on unhandled exceptions in the cron.
- **Database over CSV cache** — 1,674 cache files in repo; move to SQLite or Parquet partitions.

## Validation

- **Walk-forward instead of single train/test split** — more robust estimate of out-of-sample degradation.
- **Monte Carlo on trade sequence** — reshuffle trade order to produce drawdown distribution, not just point estimate.
- **Multi-regime validation for S&D** — 730d of yfinance history covers only the 2024-today regime; Alpaca/IBKR unlocks 2015–present.
- **Correlation between SID and S&D** — measured at +0.14; re-measure as both systems evolve. Low correlation is the main case for running both.
- **Execution study replication on S&D** — SID had a full intraday-entry-timing study; S&D only has daily-close assumption.
