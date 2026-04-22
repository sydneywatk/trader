# SID Method — Backtest Results

Run history, validation studies, and final validated metrics.

## Final validated result

- **Universe:** 99-ticker watchlist (expanded from early top-20)
- **Period:** 2020–2026-04
- **Trades:** 739
- **Win rate:** 89.3%
- **P&L:** ~$175k on a $100k account with 1% risk per trade

## Run history

### Initial top-20 watchlist

- **Result:** 100% WR — clearly unrealistic.
- **Diagnosis:** Survivorship bias. The top-20 had been selected with hindsight; every ticker was a known winner.

### Expanded to 100-ticker watchlist

- **Result:** 88.4% WR
- **Interpretation:** First realistic read. The edge survives de-biasing.

### Train/test split

- **Train:** 2020–2023
- **Test:** 2024–2026-04
- **Result:** Current watchlist held up at 88% WR on the out-of-sample test period.
- **Interpretation:** Edge is not a product of curve-fitting to the train window.

### Final run (99 tickers)

- **Result:** 89.3% WR, 739 trades, ~$175k P&L.
- **Output:** Excel workbook with full trade log + skipped signals (signal/blocker diagnostics).

## Intraday execution study (Tests 1a–4b)

Question: does intraday execution beat daily-close execution?

| Test | Variant | Outcome |
|---|---|---|
| 1a/1b | Hourly entry timing | Underperforms daily close |
| 2a/2b | Peak exit inside day | Underperforms daily close |
| 3a/3b | Hourly stop trigger | Underperforms daily close |
| 4a/4b | RSI-50 intraday touch exit | **Loses $33–53k vs baseline** |

Conclusion: daily-close execution is optimal for this strategy. See decision log entry #5.

## Filters (final configuration)

1. Daily RSI direction + MACD alignment
2. Weekly RSI confirmation (>3pt delta vs prior week)
3. Earnings proximity (>14 days away)
4. Gap check (reject on oversized opening gaps)
5. SPY market regime (long-favorable / short-favorable / mixed)

## Outputs

- Formatted Excel workbook: trade log + skipped signals with blocker attribution
- Live scanner output: Google Sheets with 19-column schema including signal type, prices, risk sizing, diagnostics, blocker notes
- Email alerts via Gmail SMTP when actionable setups or open positions exist
- GitHub Actions cron: 6am PDT pre-market and 1:15pm PDT post-close

## Open questions

- Expanding universe beyond 99 tickers — does edge hold on S&P 500 / Russell 1000?
- Options expression for Tier 1 signals — backlog item, not yet spec'd
- Live vs backtest slippage — will be measurable once paper trading begins
