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
| Signal + backtest engine | **QuantConnect/LEAN** (cloud, realistic IBKR fill model); a local Python engine lives in the companion `ibkr-trader` repo |
| Iteration / build loop | **Claude Code** (research, implement, ablate, train/test, re-validate) |
| Version control / docs | **GitHub** — QuantConnect pipeline in [`trader`](https://github.com/sydneywatk/trader); local/IBKR engine split out to [`ibkr-trader`](https://github.com/sydneywatk/ibkr-trader) |
| Execution (planned) | **QuantConnect paper** → later live; options overlay after shares |

## 5. What we've done ✅

- [x] **Faithful port** of the published checklist (matches his stop-rounding quirk to the dollar).
- [x] **Parameterized QuantConnect harness** (`quantconnect/sid_quantconnect_experiments.py`) — one compile drives every test (universe / exit / filters / side / single-ticker + date override).
- [x] **Survivorship-free universe** rebuilt from point-in-time ETF holdings; split-adjusted prices to match TradingView.
- [x] **Train (2020–23) / test (2024–26) holdout** discipline.
- [x] **Engine cross-checked against his real trades** — reproduces his DIS example to the day; more of his logged trades to validate next.
- [x] **GitHub** — code, honest README, pipeline graphic + one-pager (`docs/`).

## 6. Target & current status

- **Target: ~76% win rate** — the rate he reports on shares; the bar the automated
  pipeline aims to reproduce on his ~100-ticker watchlist.
- **Current (automated, faithful):** his watchlist, both sides, every signal taken
  mechanically → **~55–58% WR at ~25 trades/month** (cadence matches his). The gap
  to 76% is the **discretion** he applies by hand — one daily "top pick," chart
  confirmation, early exits — not a coding error (the engine reproduces his trades).
- **Key finding — the short side has no edge.** On the survivorship-free universe,
  flipping the identical strategy from long-only to both-sides drops it from
  **+70.5% to −16.0%** (max drawdown 12% → 40%, Sharpe 0.30 → −0.21). Shorting
  overbought equities has negative expectancy — a documented mean-reversion
  asymmetry — so the **deployable config is long-only**. A finding, not a filter.
- **Headline (deployable: long-only, survivorship-free, 2020–2026):** Net **+70.5%**,
  CAGR 8.8%, max DD 12.4%, Sharpe 0.30 — and +4.5% in the 2022 bear vs SPY −18%.
- **Honesty benchmark:** the survivorship-free universe separates "the method" from
  "the names" — how much edge is real before risking capital.
- His **real returns come from an options overlay** (selling puts / buying calls),
  which a shares backtest can't capture — hence Phase 2.

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

## 8. Decisions & open items

- **Universe — decided:** trade **his ~100 tickers only** (`universe=watchlist`,
  the 92-ticker list). The survivorship-free universe stays as the honesty
  benchmark, not the live config.
- **Side — decided:** the **deployable** config is **long-only** — testing both
  sides showed the short leg has negative expectancy (−16% vs +70% on the same
  universe). The faithful both-sides run is kept for fidelity/cadence checks, not
  for deployment.
- **Exit — open:** his RSI-50 take-profit is faithful and sets the ~25 trades/month
  cadence; the ATR trailing exit has better out-of-sample economics and is the
  current deployable choice.
- **Open — paper-trade venue:** QuantConnect paper brokerage (no credentials,
  ~$24/mo node) — node caps at 10 assets, so either run a ~10-ETF subset, pay for
  a bigger node, or do signals-only first.
- **Open — options overlay:** scoped as Phase 2.

## 9. Repo map

```
This repo (trader) — QuantConnect pipeline:
  quantconnect/sid_quantconnect_experiments.py   # the parameterized algorithm
  quantconnect/deploy.py                          # one-command deploy
  shared/                                         # reference math the unit tests lock down
  tests/                                          # unit tests (CI)
  docs/onepager/  ·  docs/pipeline/               # one-pager + pipeline graphic
  docs/SID_METHOD_PROJECT.md                      # this document

Companion repo (ibkr-trader) — local engine:
  strategies/sid_method/   # local Python engine + daily scanner
  execution/               # IBKR paper/live adapter
```
