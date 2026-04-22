# Trader Knowledge Base

Master index for strategy research, decisions, and operational knowledge across the multi-strategy platform.

## Strategies

| Strategy | Status | Edge | Location |
|---|---|---|---|
| **SID Method** | Validated, live scanning | RSI mean-reversion with 5-filter gate; 88.4% WR across 99 tickers | `strategies/sid_method/` |
| **Supply & Demand (Zone)** | Phase 2 committed | RBR long-only zones on 1h intraday; 39.4% WR, edge persistent out-of-sample | `strategies/supply_demand/` |

### SID Method
Mean-reversion on daily RSI oversold/overbought signals, gated by daily RSI direction + MACD, weekly RSI delta >3pt, earnings >14d, gap check, and SPY regime. Daily-close execution validated as optimal. Email alerts + Google Sheets output. GitHub Actions cron at 6am PDT pre-market and 1:15pm PDT post-close. IBKR paper trading adapter built, pending TWS setup.

### Supply & Demand
Daily zone backtester and 1h intraday variant. Phase 1 (daily): too few trades, tight confirmation rule. Phase 2 (1h): 3,184 trades, +$127k P&L long+short, +$290k long-only. RBR pattern is the dominant edge. Shorts disabled. Per-ticker curation does not generalize out-of-sample.

## Docs

- [Improvement Backlog](IMPROVEMENT_BACKLOG.md) — prioritized next steps by area
- [Decision Log](decisions/DECISION_LOG.md) — key decisions with reasons and reversibility
- [SID Backtest Results](research/sid_method/BACKTEST_RESULTS.md) — run history and final validated metrics
- [S&D Backtest Results](research/supply_demand/BACKTEST_RESULTS.md) — run history, phase comparison, train/test
- [Trading Journal](TRADING_JOURNAL.md) — paper trade log template

## Repo layout reminder

```
shared/          data, indicators, earnings (cross-strategy)
strategies/
  sid_method/    SID backtester, live scanner, Sheets/email output
  supply_demand/ S&D zone backtester (daily + 1h)
execution/       IBKR adapters (paper trading)
docs/            this knowledge base
```
