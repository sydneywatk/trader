# Phase 1 — Prior Art on Daily RSI + MACD Mean Reversion (Equities)

_Survey conducted 2026-05-11 in support of porting the SID Method to QuantConnect/LEAN._

## Search methodology

Surveyed five source classes for daily-timeframe RSI + MACD mean-reversion implementations on equities:

1. **QuantConnect** — Strategy Library, community forum, indexed LEAN GitHub examples. Queries: `RSI MACD mean reversion`, `oversold MACD cross`, `daily mean reversion equities`, `"SID method"`, `"Sid Naiman"`, `RSI(2) Connors strategy library`.
2. **GitHub** — top-ranked repos for daily RSI+MACD equity strategies; READMEs/descriptions from search snippets. WebFetch / `gh` CLI were denied in the research session, so per-repo depth is limited to search snippets — flagged under negative findings.
3. **Quantpedia** — entries for RSI / MACD / cross-sectional mean reversion.
4. **QuantifiedStrategies.com** — full article body was behind a bot-verification wall and could not be fetched; rule/result quotes are from Google-indexed snippets quoting those pages.
5. **SSRN / academic** — targeted query for QuantConnect/MACD/RSI mean-reversion papers.
6. **"SID Method" / "Sid Naiman"** — independent blog / YouTube / Trading Cafe / Scribd / Skool.

Excluded by scope: FX, futures, crypto, intraday. Several otherwise-relevant GitHub hits (GZotin/RSI_MACD_strategy on Binance/BTC, KuanlinBilly on Taiwan 50, XBT3K on EURUSD 1-min) dropped on that basis.

## Implementations found

### 1. QuantifiedStrategies — "MACD and RSI Strategy: 73% Win Rate"
- URL: https://www.quantifiedstrategies.com/macd-and-rsi-strategy/ (article body returned a bot wall; figures from indexed snippets)
- Platform: blog post + paywalled code product. No public LEAN implementation.
- Rules: "Go long if the MACD is rising above the signal line and the RSI is rising after falling into the oversold region." Adds a third "mean reversion filter" indicator; exit when "the mean reversion filter reverses." Daily bars, long-only.
- Reported results: **73% WR over 235 trades**, avg gain 0.88%/trade incl. commissions/slippage. Profit factor 2.3, CAGR 8%, 14% market exposure, MDD **-46%**.
- Universe: single ETF — **XLP** (consumer staples), chosen because "this sector moves a bit independently."
- Distance from SID: **loosely related**. Shares the RSI-oversold + MACD-confirm skeleton on daily bars. Differs on universe (single ETF vs 99-name), direction (long-only), confirmation stack (no weekly RSI, no SPY regime, no earnings), stop (unspecified vs structural), exit (filter reversal vs RSI=50 / time / earnings).
- Key takeaway: closest published apples-to-apples daily RSI+MACD mean-reversion backtest with realistic costs. **73% / 235 / -46% MDD** is the single best public benchmark to compare a QC port of SID against. The thinner gating stack and -46% MDD are what you'd expect when you strip out SID's regime/weekly/earnings filters.

### 2. QuantifiedStrategies — "RSI Trading Strategy (91% Win Rate)"
- URL: https://www.quantifiedstrategies.com/rsi-trading-strategy/
- Rules: short-lookback RSI mean-reversion on stocks/indices; "a 2-day RSI strategy buys when it crosses below 15 and sells when it exceeds 85"; "for stocks, a short lookback period works best."
- Reported results: **91% WR** headline. Trade count, Sharpe, MDD: **[not stated]** in any accessible snippet.
- How close to SID: **loosely related** — RSI-only, RSI(2) not RSI(14), thresholds 15/85 not 30/70.
- Key takeaway: useful as a reminder that 90%+ WR headlines exist in this strategy family with very different parameters and no realistic-cost or sample-size disclosure. Don't anchor on it.

### 3. QuantConnect Forum — "The Alpha Formula" Mean Reversion (CabedoVestment port of Connors/Cain)
- URL: https://www.quantconnect.com/forum/discussion/18219/quot-the-alpha-formula-quot-mean-reversion-strategy-by-cabedovestment/
- Platform: **actual QuantConnect/LEAN Python algorithm**, shared in forum thread.
- Rules: weekly rule-check with daily stop check. Exit: "Sell on close if weekly 2-period RSI is above 80." Stop: "Sell on close if current price is more than 10% below entry price."
- Reported results: **CAGR 12.5%, MDD 22.8%, 63% success rate, 0.64% return per winner.** Period: 1998 to present. Trade count: [not stated].
- Universe: S&P 500.
- How close to SID: **loosely related**. Same broad family on daily/weekly equity bars, similar S&P 500 universe. But: weekly RSI(2) (not daily RSI(14) + weekly RSI(14)), no MACD, no earnings/SPY filters, -10% fixed stop instead of structural lowest-low stop.
- Key takeaway: **the single best public daily-equity LEAN baseline to compare SID against** — 63% WR / 22.8% MDD on S&P 500 mean reversion. Naiman's claimed 70-71% sits above this; the trader-repo's 89.3% sits well above and is the figure most in need of QC validation.

### 4. QuantConnect Forum — "Bubble Algorithm Using CAPE Ratio, MACD, and RSI" (TimCo)
- URL: https://www.quantconnect.com/forum/discussion/418/bubble-algorithm-using-cape-ratio-macd-and-rsi/
- Platform: actual QuantConnect/LEAN algorithm.
- Rules: CAPE + MACD + RSI on S&P "to buy low and sell high in turbulent markets." Tested since 2000.
- Reported results: "2x S&P performance without leverage and a Sharpe ratio 4x better than S&P 15-year Sharpe." WR / MDD / trade count: **[not stated]**.
- **Caveat from the thread itself**: "Users have reported difficulty trying to backtest the model and unable to reproduce results" due to a minute-resolution time-gate bug.
- How close to SID: **loosely related** — index-level rather than multi-name, regime input is CAPE not SPY-vs-SMA50, no weekly RSI, no earnings filter, no structural stop.
- Key takeaway: reproducibility questioned in the thread itself, so treat headline numbers as unverified.

### 5. QuantConnect / LEAN GitHub reference samples
- `MACDTrendAlgorithm.py` — https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/MACDTrendAlgorithm.py
- `IndicatorSuiteAlgorithm.py` — https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/IndicatorSuiteAlgorithm.py
- `DailyAlgorithm.py` — https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/DailyAlgorithm.py
- `MacdAlphaModel.py` — https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py
- `MeanReversionLunchBreakAlpha.py`, `GlobalEquityMeanReversionIBSAlpha.py`
- These are **not strategies** but they instantiate `MACD(12, 26, 9, MovingAverageType.Wilders)` and `RSI(14)` on `Resolution.Daily` — confirming SID's exact indicator parameters are LEAN-idiomatic and don't need custom wrappers.
- Key takeaway: start the SID port from `DailyAlgorithm.py` / `IndicatorSuiteAlgorithm.py` for indicator wiring; `MacdAlphaModel.py` is informative but the framework alpha-model pattern is overkill for SID's tight entry/exit coupling (use classic algorithm structure instead).

### 6. Sid Naiman — "SID Method" original (discretionary methodology)
- Trading Cafe write-up: https://thetrading.cafe/post/rsi-trading-strategy
- Strategy PDF (Scribd, not fetched): https://www.scribd.com/document/912928000/Overbought-Oversold-Strategy-by-Sid-Naiman-Strategy-PDF
- Skool community (paid): https://www.skool.com/@sid-naiman-9156
- YouTube: https://www.youtube.com/watch?v=wJYRnFcD_Pg, https://www.youtube.com/watch?v=p4WyhH3I8Q0
- Public rules (Trading Cafe): RSI<30 → look long, RSI>70 → look short. Then wait for MACD lines to cross in the same direction. Stop: below the low when RSI ≤ 30. TP: RSI=50 "with no exceptions." Earnings: never trade within 14 days. Higher-timeframe RSI direction must align.
- Reported results: "students reporting an average win rate of 76.98%"; "students backtested this method across 13,200 trades with an average 71% win rate." Period / universe / Sharpe / MDD: **[not stated]** publicly.
- Distance from SID-in-code: **identical core** — this is the source method. Trading Cafe omits the SPY-vs-SMA50 regime filter, the RSI<50 "no room to run" gate, the lowest-low / whole-number stop rounding, the 10-day time exit, the >3pt weekly-RSI quantification, and the disabled 2-day RSI reversal rule — those appear to be additions in the trader-repo implementation, not Naiman's public method.
- Key takeaway: **the 89.3% / 739-trade backtest figure is not independently corroborated** in any public source. The only public Naiman numbers are 71% (student-reported, 13,200 trades, no period/universe) and 76.98% ("students reporting").

### 7. SSRN — Vu & Bhattacharyya, "Design and Development of Mean Reversion Strategies on QuantConnect Platform" (2024)
- URL: https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4878676
- Platform: QuantConnect (Python), academic preprint.
- Abstract: tests MACD, RSI, Bollinger Bands intraday on NYSE; explores stop-loss sensitivity and Sharpe.
- Reported results: not stated in abstract; full paper would be needed.
- Distance from SID: **out of scope** for results comparison (intraday, not daily) but useful for the LEAN structural patterns the user already plans to draw from.

## Comparison table

| Source | Daily? | RSI | MACD | Multi-tf RSI? | Regime filter? | Earnings filter? | Stop rule | Exit rule | WR | Trades |
|---|---|---|---|---|---|---|---|---|---|---|
| **SID Method (being ported)** | Yes | RSI(14) <30 / >70 | 12/26/9 align + hist | Yes (weekly RSI, >3pt move) | Yes (SPY RSI + SPY vs SMA50) | Yes (≥14d) | Lowest-low / highest-high, whole-number | RSI=50 / 10-day time / pre-earnings / stop | 89.3% backtest, 71% Naiman live | 739 BT / ~13,200 live |
| QS MACD+RSI 73% (XLP) | Yes | "RSI rising from oversold" | "above signal, rising" | No | No (single ETF) | No | [not stated] | Mean-reversion filter reverses | 73% | 235 |
| QS RSI 91% | Yes | RSI(2) <15 / >85 | No | No | [not stated] | No | [not stated] | RSI >85 | 91% | [not stated] |
| Alpha Formula (Connors/Cain, QC port) | Yes (weekly + daily stop) | Weekly RSI(2) | No | Sort of (weekly only) | No | No | -10% from entry | Weekly RSI(2) > 80 | 63% | [not stated] |
| Bubble Algo (TimCo QC) | Yes | RSI (period not stated) | Yes | No | CAPE, not SPY | No | [not stated] | [not stated] | [not stated] | [not stated] |
| LEAN reference samples | Yes | RSI(14) | 12/26/9 Wilders | No | No | No | None | None | n/a | n/a |
| Vu & Bhattacharyya SSRN | No (intraday) | Yes | Yes | No | No | No | Yes (variant) | [paywalled] | [not stated] | [not stated] |

## What's novel about SID vs all of the above

The following SID rules do **not** appear in any prior-art source located:

1. **Two-step signal/entry separation across calendar days.** Step 1 (RSI crosses 30/70) and Step 2 (MACD direction + weekly-RSI movement + earnings + RSI<50 gate + SPY regime) must hold on different bars. Public RSI+MACD strategies fire on a single bar's confluence.
2. **Daily RSI(14) + weekly RSI(14) confirmation with a quantified ">3 points in trade direction" movement threshold.** Trading Cafe mentions "trade in direction of higher-timeframe RSI" but never quantifies it.
3. **SPY-as-regime, two-leg filter** (SPY RSI direction *and* SPY vs SMA50, with an asymmetric "within 2% of SMA50" tolerance on the short side). No public source uses this exact dual-leg filter.
4. **"No room to run" RSI<50 gate.** Refusing entries after RSI has already mean-reverted past the midline.
5. **Structural lowest-low / highest-high stop between signal bar and entry bar, rounded to whole dollars (with -1/+1 nudge if already whole).** All public sources use either fixed-percent stops (Alpha Formula -10%) or unspecified stops.
6. **Layered exit priority list with RSI=50 take-profit, 10-trading-day time exit, and pre-earnings forced exit.** Public daily RSI+MACD strategies typically have a single exit condition.
7. **The explicit decision that the documented "2-day RSI reversal" exit rule is intentionally disabled because backtests showed it caught oscillations, not reversals.** Internal optimization unique to the trader-repo implementation.
8. **99-name hand-curated universe of mega-cap S&P 500 names plus a few sector/leveraged ETFs (XLU, GDX, NUGT).** Public sources are either single-ETF, S&P 500 wholesale, or index-only.

## Negative findings

- **No QuantConnect Strategy Library entry, public algorithm, or forum thread** for a daily RSI(14)+MACD(12,26,9) mean-reversion equity strategy with weekly-RSI confirmation, SPY regime filter, and earnings filter. Closest QC implementations (Alpha Formula port, Bubble Algorithm) share at most 2 of 5 SID gating concepts.
- **No public QuantConnect or LEAN port of the SID Method specifically.** All search hits for `"SID method" quantconnect`, `"Sid Naiman" algorithm`, `"Sid Naiman" python` return Trading Cafe / Scribd / YouTube / Skool — discretionary teaching content, no code. QC docs that mention "sid" refer to Security Identifiers (a Quantopian-migration concept), unrelated.
- **No GitHub repo found that ports SID or implements an equivalent daily RSI+MACD mean-reversion strategy with weekly-RSI and regime filters on equities.** Top GitHub hits for "RSI MACD" are crypto/Binance, Taiwan stocks (KD+RSI+MACD), generic indicator labs, or FX intraday — all out of scope.
- **Quantpedia has no dedicated "RSI+MACD daily mean reversion equities" entry.** Relevant entries (Short-Term Reversal in Stocks, Cross-Sectional Equity Mean Reversion) are return-ranked cross-sectional reversal strategies, not indicator-confluence single-name strategies.
- **No SSRN paper found that tests SID-style multi-filter daily RSI+MACD mean reversion on US equities with reported results.** Vu & Bhattacharyya is intraday.
- **No independent reproduction of Naiman's 70-71% live-trading number** in any public backtest. The Trading Cafe "13,200 trades / 71%" cites student/community numbers without a published backtest, period, or universe.
- Could not directly fetch `quantifiedstrategies.com/macd-and-rsi-strategy/`, the Scribd SID PDF, or individual GitHub READMEs — figures came from search-result snippets in this session. If higher-fidelity rule quotes from those specific URLs matter, re-run prior-art research with WebFetch permitted on those domains.

## Headline conclusion

SID's combination — **daily RSI(14) + MACD(12,26,9) + weekly RSI delta + SPY two-leg regime + earnings filter + structural lowest-low stop + RSI=50 TP + time exit, on a 99-name hand-curated universe** — does not appear to have been published anywhere. The closest comparable public benchmark on QC is the Alpha Formula port at **63% WR / 22.8% MDD** on S&P 500 mean reversion. The closest published daily RSI+MACD result is QuantifiedStrategies at **73% WR / 235 trades / -46% MDD** on a single ETF. Both lend plausibility to a 70-75% WR landing zone for a faithful QC port of SID; the repo's 89.3% backtest figure is not corroborated by any public source.

## Sources cited

- https://www.quantifiedstrategies.com/macd-and-rsi-strategy/
- https://quantifiedstrategies.substack.com/p/macd-and-rsi-trading-strategy-rules
- https://www.quantifiedstrategies.com/rsi-trading-strategy/
- https://www.quantifiedstrategies.com/rsi-mean-reversion-trading-strategy/
- https://www.quantifiedstrategies.com/mean-reversion-strategies/
- https://www.quantifiedstrategies.com/macd-trading-strategy/
- https://www.quantifiedstrategies.com/rsi-2-strategy/
- https://www.quantconnect.com/forum/discussion/18219/quot-the-alpha-formula-quot-mean-reversion-strategy-by-cabedovestment/
- https://www.quantconnect.com/forum/discussion/418/bubble-algorithm-using-cape-ratio-macd-and-rsi/
- https://www.quantconnect.com/forum/discussion/416/indicator-examples-bb-macd-sma-ema-rsi-atr/
- https://www.quantconnect.com/forum/discussion/8469/how-to-utilize-macd-and-rsi/
- https://www.quantconnect.com/forum/discussion/9081/rsi-and-macd-alpha-questions/
- https://www.quantconnect.com/forum/discussion/3327/enhanced-short-term-mean-reversion-algorithm/
- https://www.quantconnect.com/docs/v2/research-environment/applying-research/mean-reversion
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/MACDTrendAlgorithm.py
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.CSharp/MACDTrendAlgorithm.cs
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.CSharp/IndicatorSuiteAlgorithm.cs
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/IndicatorSuiteAlgorithm.py
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/DailyAlgorithm.py
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Framework/Alphas/MacdAlphaModel.py
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/Alphas/MeanReversionLunchBreakAlpha.py
- https://github.com/QuantConnect/Lean/blob/master/Algorithm.Python/Alphas/GlobalEquityMeanReversionIBSAlpha.py
- https://thetrading.cafe/post/rsi-trading-strategy
- https://www.scribd.com/document/912928000/Overbought-Oversold-Strategy-by-Sid-Naiman-Strategy-PDF
- https://www.skool.com/@sid-naiman-9156
- https://www.youtube.com/watch?v=wJYRnFcD_Pg
- https://www.youtube.com/watch?v=p4WyhH3I8Q0
- https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4878676
- https://www.researchgate.net/publication/381942833_Design_and_Development_of_Mean_Reversion_Strategies_on_QuantConnect_Platform
- https://quantpedia.com/strategies/short-term-reversal-in-stocks
- https://quantpedia.com/quantopian-quantpedia-trading-strategy-series-cross-sectional-equity-mean-rever/
- https://github.com/GZotin/RSI_MACD_strategy
- https://github.com/KuanlinBilly/Backtesting-a-KD-RSI-MACD-trading-strategy-using-Python
- https://github.com/gawd-coder/Backtest-Indicator-Strategies
- https://github.com/SharmaVidhiHaresh/Backtesting-Trading-Strategies-with-Python
