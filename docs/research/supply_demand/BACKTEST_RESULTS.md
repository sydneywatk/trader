# Supply & Demand (Zone) — Backtest Results

Phase 1 (daily) and Phase 2 (1h intraday) results, plus train/test and per-ticker analyses.

## Headline

- **Long-only 1h:** 1,854 trades, **39.4% WR, +$290,833 P&L**
- **RBR pattern alone:** +$197k of the long-only P&L (dominant edge)
- **Train/test long-only:** −2.0pp delta — edge is persistent out-of-sample
- **Shorts disabled** — DBD side was a net drag (see decision log #4)

## Phase 1 — daily zone backtester

Built `config.py`, `zones.py`, `zone_signals.py`, `backtest_sd.py`, `output_sd.py`, `main_sd.py`.

### First run at spec defaults (99 tickers)

- **Trades:** 74
- **Win rate:** 37.8%
- **P&L:** −$8,630

### Diagnostic funnel

- Base/impulse ATR thresholds too tight for daily stocks. 0.5× ATR sits at the 13th percentile of daily ranges; 1.5× impulse at the 95th. Almost no zones formed.
- **Confirmation candle** (strict engulfing/hammer) killed 83% of touched zones. Primary bottleneck.

### Focused tests on daily data

| Test | Result |
|---|---|
| Long-only | P&L flipped to +$2,195 — but only 41 trades |
| Train/test | Inconclusive (sample too small) |
| Per-ticker | Meaningless at that sample size |
| SID/S&D correlation | **+0.14** — low; strategies complementary |

Conclusion: daily timeframe is too sparse. Move to intraday.

## Phase 2 — 1h intraday

Built `shared/data_intraday.py` (Alpaca primary + yfinance fallback, market-hours filter, 24h CSV cache). Extended config with `TIMEFRAME` switch + runtime accessor helpers. Rewrote confirmation rule for 1h: **close back above proximal + right color**, with engulfing/hammer as bonus flag only.

### 99-ticker 1h run (yfinance 730d)

- **Trades:** 3,184
- **Win rate:** 35.5%
- **P&L:** +$127,441

### Funnel improvement

| Metric | Phase 1 (daily) | Phase 2 (1h) |
|---|---|---|
| Confirmation pass rate | 13.7% | **49.2%** |
| Zone count | 818 | **13,828** (17× more) |

Hypothesis validated: confirmation rule was the bottleneck, not zone detection.

## Phase 2 analyses (pre-commit)

### Long-only 1h

- **Trades:** 1,854
- **Win rate:** 39.4%
- **P&L:** **+$290,833**
- **RBR alone:** +$197k

### Train/test on full-universe long-only

- **Delta:** −2.0pp
- **Read:** Edge is persistent out-of-sample.

### Per-ticker "curated 29" subset

- **Delta:** +0.1pp on full data (too good to be true — data-snooped)
- **Honest version** (pick from train only, validate on test): **−16.4pp delta**
- **Read:** Per-ticker selection does **not** generalize. Don't ship a curated subset.

### $5k account projection

- Realistic **~+$200/mo net** without per-ticker filtering
- ~+$70/mo with RBR + filter

## Key findings

1. **RBR is the edge.** Long-only RBR drives most of the P&L.
2. **Shorts fail.** DBD side kills aggregate P&L. Disabled.
3. **Per-ticker curation doesn't generalize.** Universe-wide rules only.
4. **Single regime covered.** 730d yfinance history is bullish-regime only. Multi-regime validation is blocked on 10-year intraday data (Alpaca / IBKR).

## Open questions

- Does the edge survive in bear / choppy regimes? Requires 2015–present 1h data.
- Is there a zone-entry refinement (limit at proximal vs confirmation close) that improves fills?
- Can shorts be salvaged with a regime filter (e.g. only short when SPY < 200d MA)?
