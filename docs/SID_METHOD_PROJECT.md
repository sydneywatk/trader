# SID Method — Automation Project (Work in Progress)

**Status:** 🟡 Work in progress · shares pipeline validated, paper-trade + options overlay pending
**Owner:** Sydney Watkins · **Updated:** 2026-06-28

---

## 1. Thesis

Take a **manually-traded, proven** discretionary trading method (the "SID Method,"
an RSI/MACD mean-reversion strategy taught by Sid Naiman) and **automate it
end-to-end** — turning a human checklist into a tested, deployable pipeline using
**Python, QuantConnect, GitHub, and Claude Code** as the build/iteration loop.

The goal is not to invent a strategy — it's to **faithfully reproduce a working
discretionary method in code, validate it honestly, and run it forward without a
human in the loop.**

## 2. The method (what we're automating)

A daily-chart mean-reversion system. Published checklist:

- **Entry signal:** RSI(14) < 30 (oversold → long) or > 70 (overbought → short)
- **Confirmation:** RSI and MACD(12,26,9) align (point/cross) in the trade direction
- **Earnings filter:** no entry within 14 days of earnings
- **Stop loss:** swing low/high between signal and entry, rounded to the whole number
- **Exit:** take profit when RSI returns to 50 (only two exits: stop or RSI-50)

Trader's real-world cadence: **~20–30 setups/month**, entered in the last hour of
the day, ~5–7 trading days per trade.

## 3. Scope decision — universe

We trade **only the ~100 tickers Sid actually trades** (he keeps his watchlist at
"about 50-50 stocks/ETFs"). Concretely we compiled a **deduped list of 92 tickers**
from his two "Stocks List of Profitable Trades" slides — large/liquid names plus
sector/index ETFs and leveraged/inverse ETFs (TQQQ, SQQQ, TNA, TZA, DUST, NUGT…).

> **Why a fixed list is legitimate:** committing in advance to one universe removes
> per-period cherry-picking. The honest caveat (documented below) is that his list
> was *chosen with hindsight* on past winners, so the true unbiased proof is the
> **forward** paper test. We separately rebuilt a survivorship-free version to
> measure how much edge is "the names" vs "the method."

## 4. Stack / architecture

| Layer | Tool |
|---|---|
| Signal + backtest engine | **Python** (local `strategies/sid_method/`) + **QuantConnect/LEAN** (cloud, realistic IBKR fills) |
| Iteration / build loop | **Claude Code** (research, implement, ablate, train/test, re-validate) |
| Version control / docs | **GitHub** (`github.com/sydneywatk/trader`, branch `quantconnect-port`) |
| Execution (planned) | **QuantConnect paper** → later **IBKR**; options overlay after shares |

## 5. What we've done ✅

- [x] **Faithful port** of the published checklist (matches his stop-rounding quirk to the dollar).
- [x] **Parameterized QuantConnect harness** (`quantconnect/sid_quantconnect_experiments.py`) — one compile drives every test (universe / exit / filters / side / single-ticker + date override).
- [x] **Survivorship-free universe** rebuilt from point-in-time ETF holdings; split-adjusted prices to match TradingView.
- [x] **Train (2020–23) / test (2024–26) holdout** discipline.
- [x] **Engine validated against his real trades** — reproduced 14/18 of a student's logged IWM trades; his DIS example to the day.
- [x] **GitHub updated** — code, honest README, pipeline graphic (`docs/pipeline/`).

## 6. Key findings (honest)

- The advertised **~88% win rate is selection bias** — measured on a watchlist curated on past win rate. Removed → **~61%** on a survivorship-free universe.
- The faithful method is **high win rate but breakeven** (small wins, larger losses; profit-factor ~0.6). On *his* watchlist with *his* exit we get **~67%** — close to the students' ~75%; the rest of the gap is their discretion (one "top pick"/day, pattern confirmation, early exits) + small self-logged samples.
- A **trailing-exit, longs-only variant** (our modification) turns the signal into **positive out-of-sample expectancy** — including **+4.5% in the 2022 bear while SPY −18%** (real alpha, not just bull beta). This is the durable result.
- His **real returns come from an options overlay** (selling puts / buying calls) — structurally high win rate, separate from the signal. A shares backtest can't capture it; hence the phased plan below.

## 7. Roadmap (phased)

**Phase 1 — Shares pipeline** *(in progress, nearly done)*
- [x] Faithful signal + survivorship-free validation + trade-validation
- [x] Lock config: `etf_rule` universe (or his 92-ticker list), trailing exit, longs-only, 1% risk
- [ ] **Paper-trade forward on QuantConnect** (final gate before any capital)

**Phase 2 — Options overlay** *(planned, after shares is finished)*
- [ ] Express each signal via options the way he does — **2 months out, ATM or 1-strike OTM**, calls (bullish) / sold puts (high-probability income)
- [ ] Model option chains, premiums, assignment

**Phase 3 — Live** *(planned)*
- [ ] Promote only after a forward edge holds; small size; low-correlation signals

## 8. Open decisions / next steps

1. **Paper-trade venue:** QuantConnect paper brokerage (no credentials, ~$24/mo node) — node caps at 10 assets, so either run a ~10-ETF subset, pay for a bigger node, or do signals-only first.
2. **Universe for production:** his fixed 92-ticker list (faithful to how he trades) vs the rule-based ETF-holdings universe (unbiased). Likely run **both** — his list live, the unbiased one as the honesty benchmark.
3. **Options modeling** scoped as Phase 2.

## 9. Repo map

```
quantconnect/sid_quantconnect_experiments.py   # main parameterized harness
quantconnect/sid_quantconnect_dynamic.py       # broad survivorship-free stress test
quantconnect/sid_quantconnect.py               # faithful fixed-watchlist port
strategies/sid_method/                          # local Python engine + daily scanner
docs/pipeline/sid_method_pipeline.{html,pdf}    # pipeline graphic
docs/SID_METHOD_PROJECT.md                       # this document
```
