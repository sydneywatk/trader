# Supply & Demand Zone Strategy

Daily (Phase 1) + 1-hour intraday (Phase 2) zone backtester. Detects
institutional supply/demand zones via a base+impulse pattern, filters for
freshness, HTF trend alignment, earnings buffer, and confirmation, exits at
2:1 RR or stop loss. Same 99-ticker watchlist as SID Method.

## Current status — honest findings

Tested on yfinance 730 days of 1h data (2023-05 → 2026-04, 99 tickers).

| Run | Trades | Win Rate | P&L | Avg RR |
|---|---|---|---|---|
| **Full universe long-only (RBR + DBR)** | **1,854** | **39.4%** | **+$290,833** | **+0.157** |
| → RBR (continuation) | 1,049 | 40.4% | +$197,234 | +0.188 |
| → DBR (reversal)     |   805 | 38.0% | +$93,599  | +0.116 |
| Full universe shorts (RBD + DBD) | 1,330 | 30.0% | −$170,385 | — |
| 29-ticker curated subset (train 2023-05 → 2024-12, test 2025-01 → today) | 216 test | 51.9% | +$111,528 test | +0.516 |

**Out-of-sample behavior (most important):**

| Split | Trades | WR | Delta vs train |
|---|---|---|---|
| Full-universe long-only, train | 1,071 | 40.2% | — |
| Full-universe long-only, test  |   782 | **38.2%** | **−2.0pp — edge persistent** |
| 34-ticker *train-selected* subset, test (RBR+DBR) | 251 | 38.2% | −16.4pp vs train (WR 54.6% on train) |
| 34-ticker *train-selected*, test (**RBR only**) | **142** | **41.5%** | positive hold-out edge |

### What this means
- **Full-universe long-only is a real, persistent edge.** The strategy's −2pp train→test delta is well inside the 5pp tolerance — edge holds on held-out data.
- **Per-ticker filtering does NOT generalize.** A 34-ticker subset picked using only train data dropped from 54.6% WR on train to 38.2% on test — no better than trading the whole universe. The 29-ticker "curated" subset looks great because its selection window overlaps the test window (selection bias). **Do not trade with a curated ticker list from this backtest.**
- **RBR (continuation longs) is the honest strongest signal.** Even on the held-out test of the train-only selected universe, RBR returned 41.5% WR and +0.195 avg RR on 142 trades. DBR drops to ~34% WR on the same hold-out.
- **Shorts are disabled.** RBD and DBD both have negative expectancy across every slice tested; this is a bull-market regime signature (2023–2026) and may not hold in a bear market — will reassess if/when the regime flips.

### Realistic $5k account projection
Using full-universe long-only test-period averages (no per-ticker filtering):
- ~50 trades/month, +0.119 avg RR
- Gross ~+$300/mo; net after $2/trade commission ≈ **+$200/mo (~+47%/yr on $5k)**

RBR-only with modest per-ticker filter: ~9 trades/mo, +0.195 RR → ~**+$70/mo net**. Commissions bite hard at this account size; scales favorably at $25k+.

## Algorithm summary

**Zone formation** (ATR-normalized, no lookahead):
- Base: 1–5 consecutive candles with `range ≤ 0.5 × ATR(20)` and `body ≤ 0.7 × range`.
- Impulse: within 3 bars after base, candle with `range ≥ 1.5 × ATR(20)` and body `≥ 0.5 × range` closing in the impulse direction.
- Classified DBR / RBD (reversal, high priority) vs RBR / DBD (continuation, low priority) from a 5-bar pre-trend. Phase 2 data overturns the "reversals > continuations" prior for this market regime — long continuations (RBR) have been the dominant edge 2023–2026.

**Entry trigger** (intraday confirmation rule — `TIMEFRAME='1h'`):
- Price touches proximal edge of an active, tappable, fresh zone.
- Touch bar closes **back on the trade's side of proximal** AND is the right color (bullish for demand, bearish for supply).
- Bonus pattern flag: engulfing/hammer/shooting-star recorded when present (`close+engulfing` etc.) but not required.
- HTF filter: close on the correct side of a 350-bar SMA (≈ 50-day trend proxy).
- Earnings ≥ 7 days away.
- Entry at next 1h bar's open.

**Entry trigger** (daily mode — `TIMEFRAME='1d'`): stricter engulfing/hammer reversal-pattern requirement. Phase 1 diagnosis showed this over-filtered on daily; 1h's close-confirmation version recovers the sample without sacrificing WR.

**Exit priority:** stop loss → take profit → earnings ≤ 3 days → max-hold (20 days daily / 140 bars 1h).

**Sizing:** 1% account risk per trade (`risk_dollars / (entry − stop)` shares).

## Files

| File | Purpose |
|---|---|
| `config.py`        | All parameters + `TIMEFRAME` switch + runtime accessor helpers |
| `zones.py`         | ATR + base/impulse detection + RBR/DBD/DBR/RBD classifier + lifecycle |
| `zone_signals.py`  | Zone touch + confirmation candle (daily or 1h rule) + HTF + earnings |
| `backtest_sd.py`   | Chronological bar loop, exits, sizing, trade record |
| `output_sd.py`     | 3-sheet Excel output (All Trades / By Zone Type / By Ticker) |
| `main_sd.py`       | CLI entry point — default is `--intraday`, flag for `--daily` |
| `RESEARCH.md`      | Multi-source research report that informed the spec |

Shared: `trader/shared/data_intraday.py` — `fetch_hourly()` with Alpaca (primary, needs keys) / yfinance (fallback, 730-day cap).

## Run

```
cd ~/trader/strategies/supply_demand

# Phase 2 intraday (default)
python3 main_sd.py                           # 99-ticker 1h via yfinance
python3 main_sd.py --source alpaca           # use Alpaca (requires keys in .env)
python3 main_sd.py --tickers AAPL,HRL        # subset

# Phase 1 daily
python3 main_sd.py --daily

# Filter toggles (work in both modes)
python3 main_sd.py --no-confirm              # disable confirmation candle
python3 main_sd.py --skip-cont               # only DBR/RBD
python3 main_sd.py --no-htf                  # disable HTF trend filter
python3 main_sd.py --no-excel                # terminal summary only
```

Output: `~/trader/output/sd_method_backtest_YYYYMMDD.xlsx`

## Next steps

1. **Add Alpaca data for longer history.** yfinance caps at 730 days; Alpaca free tier provides 1h bars back to 2016. Adding keys to `.env` and switching `--source alpaca` should extend the sample by ~5×, enabling a multi-regime train/test (pre-COVID, COVID rally, 2022 bear, 2023-26 bull).
2. **Long-only live scanner.** Build `scanner_sd.py` mirroring SID's daily scanner — emit RBR+DBR signals to the signal queue consumed by `execution/ibkr_paper.py`. Keep shorts disabled until a bearish regime is confirmed.
3. **Multi-timeframe HTF.** Current HTF filter uses SMA on the same 1h series (350-bar proxy for 50-day trend). Proper HTF would pull daily SMA and align onto 1h bars — single-line change with existing `fetch_daily`.
4. **Signal-correlation recheck vs SID at 1h sample.** The earlier daily SID/SD correlation was +0.14 with only 41 SD trades. Redo with the 1,854-trade intraday sample for a real read on complementarity.
5. **Regime-flip short re-enable.** Keep RBD/DBD code paths live but gated off in config. If SPY breaks below its 200-day SMA and stays there, re-enable the short side and re-validate.

## Parameter tuning quick reference

| Symptom | Adjustment |
|---|---|
| WR < 40% | Tighten `BASE_RANGE_ATR_MULT` to 0.4; raise `IMPULSE_RANGE_ATR_MULT` to 2.0 |
| Too few trades | Loosen `BASE_RANGE_ATR_MULT` to 0.6; raise `MAX_ZONE_TESTS` to 2 |
| Good WR, low RR | Raise `RR_TARGET` to 2.5; raise `SL_ATR_BUFFER` to 0.75 |
| Too many intraday noise trades | Raise `IMPULSE_BODY_RATIO_MIN` to 0.6; raise confirmation strictness |
