# SUMMARY — SID Method QuantConnect Port

_Three-phase research + implementation deliverable. Branch: `quantconnect-port`. See companion docs for detail._

## TL;DR

- **Phase 1 (prior art):** No public LEAN/QC implementation of the SID Method exists. The closest public benchmarks are the Alpha Formula port at **63% WR / 22.8% MDD** on S&P 500 mean reversion and QuantifiedStrategies' MACD+RSI daily strategy at **73% WR / 235 trades / -46% MDD** on a single ETF. SID's trader-repo 89.3% / 739 trades figure is **not corroborated** by any public source — that's why the QC port matters.
- **Phase 2 (gap analysis):** SID's confluence stack (weekly-RSI quantified delta, SPY two-leg regime, RSI<50 "no room to run" gate, structural lowest-low stop with whole-dollar rounding, layered exit priority) is novel. Several of those filters are repo-specific additions on top of Naiman's discretionary method — they're the most likely contributors to the high backtest WR, and the QC port preserves all of them.
- **Phase 3 (port):** `quantconnect/sid_quantconnect.py` is a ready-to-paste single-file LEAN classic-algorithm port. `quantconnect/sid_parameter_sweep.py` is a parameterized variant for QC's Optimizer. Both use `BrokerageModel.INTERACTIVE_BROKERS_BROKERAGE` for realistic fills.

## Key findings from prior art

1. **No public SID port exists.** Searches across QuantConnect Strategy Library, forum, GitHub, Quantpedia, SSRN returned zero matches for the SID stack.
2. **LEAN reference samples confirm SID's exact indicator parameters work natively.** `DailyAlgorithm.py` and `IndicatorSuiteAlgorithm.py` in the LEAN repo both use `MACD(12, 26, 9, MovingAverageType.Wilders)` and `RSI(14)` on `Resolution.Daily` — no custom wrappers needed.
3. **The closest comparable public LEAN benchmark is 63% WR / 22.8% MDD** (Alpha Formula port on S&P 500 mean reversion). This is the most credible single number to anchor expectations against — Naiman's 70-71% live and the trader-repo's 89% sit above it.
4. **No published source independently corroborates the 89.3% backtest WR.** The only Naiman numbers in the wild are 71% student-trade and 76.98% "students reporting," both without published period/universe/Sharpe.
5. **The SID confluence filters that don't appear in any prior art** are: quantified weekly-RSI delta (>3pt), SPY dual-leg regime, RSI<50 gate, structural lowest-low stop with whole-dollar rounding, four-tier exit priority. Any of these may be carrying material WR; the parameter sweep harness lets you ablate them.

## What to expect from the QC backtest

| WR range | Trade count range | Interpretation |
|---|---|---|
| 65–78% | 600–800 | **Validation success.** Edge survives clean data + IBKR fills. Proceed to paper trading. |
| 58–65% | 600–800 | **Partial validation.** Edge is real but smaller than trader-repo shows. Survivorship + fills were inflating the yfinance backtest. Acceptable to paper trade but recalibrate Kelly and position sizing based on the lower WR. |
| <58% | 600–800 | **Validation failure (selection bias).** The 99-ticker universe was selected with hindsight; the edge doesn't generalize. Rebuild universe via the ADF screen (Phase 2 bonus 2) before paper trading. |
| Any WR | <300 | **Trade count too low.** Indicates the MACD-histogram-increasing fallback is missing (see QUESTIONS_FOR_SYDNEY.md #2) or the weekly-RSI tracker is misaligned (#4). Patch and re-run. |
| >85% | 600–800 | **Port bug.** Most likely the signal-vs-entry separation guard isn't firing — check `state.signal_bar == self.time` in `_scan_for_signal_and_entry`. |

**Sharpe expectations:**
- Alpha Formula port: not publicly stated but inferable as ~0.7-0.9 from its CAGR/MDD ratio.
- QS MACD+RSI: 14% market exposure, profit factor 2.3 → Sharpe ~0.8-1.0.
- A faithful SID port with 70%+ WR and ~700 trades over 6 years should land at **Sharpe 1.0-1.8**. Above 2.0 is implausibly high for a daily-equity strategy with this much trading; treat as suspicious.

## Validation success — explicit criteria

The QC port has validated the SID strategy if **all** of these hold:

1. Win rate ≥ 65% on the 99-ticker universe over 2020-01-01 to 2026-04-30.
2. Trade count between 500 and 900 (matches trader-repo 739 ±25%).
3. Sharpe ratio > 1.0.
4. Profit factor > 1.5.
5. Max drawdown < 25% (the Alpha Formula's 22.8% MDD on a thinner filter stack is a useful upper bound; SID's tighter filters should produce lower MDD).
6. Long-side WR within 5 percentage points of short-side WR (both directions working, not just one).

If 4 of 6 hold, the port is "mostly validated" and worth paper trading at half size while investigating the divergent metric. If <4 of 6 hold, do not paper trade until the cause is understood.

## Validation failure — what it would mean

- **WR < 58%:** the trader-repo's 89% was largely survivorship + fills + universe curation. The actionable response is to run the ADF universe screen (Phase 2 bonus 2) and rerun the QC port on the ADF-screened universe before any paper trading.
- **Trade count < 300:** the MACD-histogram fallback or weekly-RSI tracker is misbehaving. Fix per QUESTIONS_FOR_SYDNEY.md items #2 and #4.
- **Negative or near-zero Sharpe:** the strategy's risk-adjusted return doesn't survive IBKR fills. Could be the structural lowest-low stop is too tight in QC's fill model — investigate by relaxing the stop to lowest-low * 0.99 in the sweep and comparing.
- **Long-side WR >> short-side WR:** the SPY-falls-below-SMA50 short condition may be too rare. Loosen the short-side filter to "SPY below SMA50 OR SPY RSI falling" instead of AND.

## Next steps after the QC backtest

Sequenced by dependency:

1. **Paste `sid_quantconnect.py` into a new QC Python project** and click Backtest. All blocking questions have been resolved (2026-05-11): end-date is `2026-04-30` and earnings come from the free EODHD dataset. Read the trade log and the QC stats panel; map results to the table above.
2. **If WR < 65%**: run the parameter sweep on a narrow single-axis grid (e.g., RSI threshold 25/28/30/32/35 holding everything else constant) to see whether the trader-repo's specific parameter choices were load-bearing.
3. **If WR ≥ 65%**: skip the sweep for now and proceed to ablation. Toggle `USE_SPY_FILTER = False` and rerun; toggle `USE_EARNINGS_FILTER = False` and rerun. Each ablation tells you how much each filter is worth. This is the novel contribution called out in Phase 2.
4. **Run the ADF universe screen** (Phase 2 bonus 2) as a follow-up project. Compare WR on ADF-screened universe vs current 99-ticker universe. The delta tells you how much of the edge is universe-driven vs strategy-driven.
5. **Decision gate to paper trade**: validation criteria met → proceed to IBKR paper trading via the existing `execution/ibkr_paper.py`. Validation failed → loop back to universe reconstruction.
6. **(Stretch)** Public-facing write-up of (a) the first published SID port to QC, (b) the per-filter ablation results, (c) the ADF screen result. This would be a genuinely additive contribution to the QC community — there's no prior art.

## Files delivered on this branch

```
quantconnect/
  sid_quantconnect.py            -- main LEAN algorithm (paste into QC IDE)
  sid_parameter_sweep.py         -- parameterized variant for QC Optimizer
docs/research/
  phase1_prior_art.md            -- public RSI+MACD mean-reversion landscape
  phase2_gap_analysis.md         -- SID vs published work; bonus ADF + futures notes
  phase3_implementation_notes.md -- QC setup, port deviations, expected output
  QUESTIONS_FOR_SYDNEY.md        -- 9 flagged items, 1 blocking (date range)
  SUMMARY.md                     -- this file
```
