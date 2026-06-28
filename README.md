# trader — SID Method × QuantConnect

Automating a hand-traded discretionary swing-trading method (the **"SID Method,"**
an RSI/MACD mean-reversion strategy reported at a **~76% win rate**) into a
tested, **survivorship-free-validated**, one-command-deployable **QuantConnect**
pipeline.

**I bring the strategy and the judgment; I use Claude Code as the implementation
and automation engine** — it coded the method in Python and built a one-command
deploy. Stack: **Python · QuantConnect/LEAN · GitHub · Claude Code**.

> 📄 **Start here:** the [**project writeup**](docs/SID_METHOD_PROJECT.md) (the full
> story — thesis, method, honest results) and the
> [**one-pager**](docs/onepager/sid_onepager.pdf) (one-page summary).

- **Universe:** the trader's own **~100-ticker watchlist** (~50/50 stocks/ETFs;
  92 names compiled from his published lists), plus a **survivorship-free**
  universe used as an honesty benchmark.
- **Cadence:** the automated strategy fires **~20–30 trades/month**, matching how
  he trades it by hand.
- **Target:** reproduce his **~76% win rate** on shares, then add the options
  overlay he actually uses.

**How I use Claude Code:**
- **Implementation** — I supply the trading method and the judgment calls (what
  to test, how to read it); Claude codes it in Python — the faithful port and
  every backtested variant.
- **Automation** — I had Claude build a **one-command deploy** (`make deploy` →
  push · compile · backtest · ship), plus the CI test suite.

**Status: work in progress** — see [`docs/SID_METHOD_PROJECT.md`](docs/SID_METHOD_PROJECT.md).

## The method

A daily-chart mean-reversion system, ported 1:1 from the published checklist:

- **Entry:** RSI(14) < 30 (oversold → long) or > 70 (overbought → short)
- **Confirmation:** RSI and MACD(12,26,9) align (point/cross) in the trade direction
- **Earnings filter:** no entry within 14 days of earnings
- **Stop:** swing low/high between signal and entry, rounded to the whole dollar
- **Exit:** take profit when RSI returns to 50 (only two exits: stop or RSI-50)

Modeled with **QuantConnect's Interactive Brokers fill model** and
**split-adjusted** data. The engine is cross-checked against the trader's own
logged trades — it reproduces his DIS example to the day — and re-run on a
**survivorship-free** universe to separate "the method" from "the names."

## QuantConnect pipeline (`quantconnect/`)

`sid_quantconnect_experiments.py` is **one parameterized algorithm** that drives
every test from a single compile — so each A/B is clean (no code drift) and a
train/test holdout is just a parameter change.

| Param | Values | Purpose |
|---|---|---|
| `universe` | `dynamic` · `watchlist` · `etf_rule` | broad stress test · author's list · **survivorship-free ETF-holdings rule** |
| `exit_mode` | `rsi50` · `trail` | author's RSI-50 take-profit · ATR trailing stop |
| `side` | `both` · `long` · `short` | isolate the long edge / drop toxic shorts |
| `spy_filter`, `weekly_filter`, `earnings_exit`, `max_days` | on/off | ablate add-ons **not** in the published method |
| `tickers`, `start_*` / `end_*` | override | single-name + tight-window trade validation |

- **Faithful, checklist-only run:** `spy_filter=0 weekly_filter=0 max_days=0 earnings_exit=0`.
- **Holdout:** train `2020-01-01…2023-12-31`, test `2024-01-01…2026-04-30` — judge by
  expectancy / profit-loss ratio, not win rate.

Files:

```
quantconnect/
├── sid_quantconnect_experiments.py   parameterized algorithm — one compile drives
│                                     every variant (universe / exit / filters / side / dates)
└── deploy.py                         one-command push → compile → backtest → stats
```

Reproduce: create a QuantConnect Python project, paste
`sid_quantconnect_experiments.py` as `main.py`, and run with the parameter sets
above — or use the deploy command below.

## Deploy (one command)

`quantconnect/deploy.py` ships a strategy to QuantConnect end-to-end — push →
compile → backtest → print stats — so a new algo goes live in one command:

```
make deploy STRATEGY=quantconnect/sid_quantconnect_experiments.py
# or with parameters:
python3 quantconnect/deploy.py quantconnect/sid_quantconnect_experiments.py \
    --params universe=watchlist side=long start_year=2024
```

Credentials come from the environment or a (gitignored) `.env` (QuantConnect →
Account → Security): `QC_USER_ID`, `QC_API_TOKEN`. Add `--no-backtest` to deploy +
compile only.

## Tests

The deterministic strategy math is unit-tested (`tests/`, 26 tests, run in CI via
[`.github/workflows/tests.yml`](.github/workflows/tests.yml)):

- `test_indicators.py` — RSI / MACD / SMA: invariants (RSI bounded [0,100],
  all-gains→100, warmup NaN, histogram ≡ line − signal) plus characterization
  locks on a fixed price vector, so a formula change trips a test.
- `test_earnings.py` — the pure earnings-date helpers (the > 14-day rule).

```
pip install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

The *deterministic math* is unit-tested; the *strategy behavior* is validated
empirically on QuantConnect against the author's own logged trades.

## Repo layout

```
trader/
├── quantconnect/        # the pipeline this repo showcases
│   ├── sid_quantconnect_experiments.py   parameterized algorithm (universe / exit / filters / side / dates)
│   └── deploy.py                          one-command push → compile → backtest → stats
├── shared/              # the indicator/earnings/config math the tests lock down
├── tests/               # unit tests for that math (CI)
└── docs/                # project writeup, decision log, research, one-pager + graphic
```

> **A note on scope.** This repo is the **QuantConnect** pipeline — the faithful
> port, the survivorship-free validation, and the one-command deploy. The
> **local** track — a daily EOD scanner with email alerts and **IBKR** paper
> execution, plus two other research strategies — lives in a separate repo,
> [`ibkr-trader`](https://github.com/sydneywatk/ibkr-trader). IBKR is **not**
> part of this pipeline; QuantConnect handles execution natively.

## Setup

```
cp .env.example .env          # add QC_USER_ID / QC_API_TOKEN for deploy
pip install -r requirements.txt
```

QuantConnect work runs in the QC cloud (MCP server / web IDE) — no local market
data needed. The local engine's environment (`EMAIL_*`, `IBKR_*`, `GSHEET_ID`) is
documented in the local README.

## Conventions

- Shared modules use `from shared.X import ...`; QuantConnect code is self-contained
  and PEP8 (snake_case) per the QC LEAN API.
- The QuantConnect algorithm inlines its constants (it runs in the QC cloud, which
  can't import the local package); `shared/` exists as the reference math the unit
  tests lock down, mirroring the port.
