# trader

Multi-strategy algorithmic trading platform for US equities. Each strategy is
self-contained under `strategies/` and shares common data, indicator, and
earnings utilities from `shared/`. The flagship effort is a faithful port and
**honest, survivorship-free validation** of the discretionary "SID Method"
mean-reversion strategy — including the QuantConnect work under `quantconnect/`.

## What this is

An **automation project**: take a manually-traded discretionary method (the
"SID Method," an RSI/MACD mean-reversion strategy reported at a **~76% win
rate**) and turn it into a tested, deployable pipeline.

**I bring the strategy and the judgment; I use Claude Code as the implementation
and automation engine** — it coded the method in Python and built a one-command
deploy pipeline. Stack: **Python · QuantConnect · GitHub · Claude Code**.

- **Universe:** the trader's own **~100-ticker watchlist** (~50/50 stocks/ETFs);
  92 names compiled from his published lists.
- **Cadence:** the automated bot fires **~20–30 trades/month**, matching how he
  trades it by hand.
- **Target:** reproduce his **~76% win rate** on shares, then add the options
  overlay he actually uses.

**How I use Claude Code:**
- **Implementation** — I supply the trading method and the judgment calls (what
  to test, how to read it); Claude codes it in Python — the faithful port and
  every backtested variant.
- **Automation** — I had Claude build a **one-command deploy** (`make deploy` →
  push · compile · backtest · ship), plus the CI test suite and scheduling.

Built as a faithful port of the published checklist (RSI 30/70 entry · RSI+MACD
point/cross · earnings > 14 days · whole-number swing stop · exit at RSI 50),
with realistic Interactive Brokers fills and split-adjusted data. The engine is
cross-checked against the trader's own logged trades — it independently reproduced
**14 of 18** documented IWM trades and the DIS example to the day. We also
re-run it on a survivorship-free universe as an honesty benchmark to separate
"the method" from "the names."

**Status: work in progress** — see [`docs/SID_METHOD_PROJECT.md`](docs/SID_METHOD_PROJECT.md).
Full code: [`quantconnect/`](quantconnect/).

## Layout

```
trader/
├── shared/              # Reusable modules across strategies
│   ├── config.py            platform-level constants (paths, periods)
│   ├── data.py              yfinance daily/weekly OHLCV + cache
│   ├── indicators.py        RSI / MACD / SMA
│   └── earnings.py          earnings date fetch + proximity check
├── strategies/
│   ├── sid_method/      # RSI 30/70 mean-reversion (local Python engine)
│   ├── supply_demand/   # Phase 2 committed — 1h RBR zones (see RESEARCH.md)
│   └── breakout/        # v1 spec'd — 52-week-high closing break
├── quantconnect/        # QuantConnect ports + survivorship-free validation
│   ├── sid_quantconnect.py             faithful fixed-watchlist port
│   ├── sid_quantconnect_dynamic.py     broad survivorship-free stress test
│   └── sid_quantconnect_experiments.py parameterized harness (universe / exit /
│                                       filters / ticker + date override)
├── execution/           # Broker adapters (IBKR live + paper)
├── docs/                # decision log, research, pipeline graphic
├── cache/ · output/     # (gitignored) OHLCV/earnings cache, Excel reports
└── requirements.txt
```

## Strategies

| Strategy | Status | Notes |
|---|---|---|
| **SID Method** (`strategies/sid_method/`, `quantconnect/`) | **WIP** | Automating a discretionary RSI 30/70 + MACD mean-reversion method (reported ~76% WR) on his ~100-ticker watchlist, ~20–30 trades/month. Faithful port validated against his real trades; survivorship-free benchmark separates method from names. Shares pipeline first, options overlay next. Daily scanner with email + optional IBKR paper execution. |
| **Supply & Demand** (`strategies/supply_demand/`) | Phase 2 committed | RBR long-only zones on 1h intraday. 3,184 trades, +$290k long-only (vs +$127k long+short), 39.4% WR, edge persistent out-of-sample. Shorts disabled. See `strategies/supply_demand/RESEARCH.md`. |
| **Breakout** (`strategies/breakout/`) | v1 spec'd | 52-week-high closing break (George & Hwang 2004), technical-only, S&P 500 universe, breadth filter, partial-at-1R + trail exit. See `docs/decisions/DECISION_LOG.md`. |

## QuantConnect validation (`quantconnect/`)

`sid_quantconnect_experiments.py` is one parameterized algorithm that drives every
test from a single compile. Key parameters:

| Param | Values | Purpose |
|---|---|---|
| `universe` | `dynamic` · `watchlist` · `etf_rule` | broad stress test · author's list · **survivorship-free ETF-holdings rule** |
| `exit_mode` | `rsi50` · `trail` | author's RSI-50 take-profit · ATR trailing stop |
| `side` | `both` · `long` · `short` | isolate the long edge / drop toxic shorts |
| `spy_filter`, `weekly_filter`, `earnings_exit`, `max_days` | on/off | ablate the trader-repo add-ons that are **not** in the published method |
| `tickers`, `start_*` / `end_*` | override | single-name + tight-window trade validation |

The faithful, checklist-only run is `spy_filter=0 weekly_filter=0 max_days=0
earnings_exit=0`. The validated edge is `universe=etf_rule exit_mode=trail
atr_mult=3 max_days=10 side=long`.

Reproduce on QuantConnect: create a Python project, paste
`sid_quantconnect_experiments.py` as `main.py`, and run backtests with the
parameter sets above (train `2020-01-01…2023-12-31`, test
`2024-01-01…2026-04-30`).

## Running the local SID engine

### Daily scanner (EOD)
```
cd strategies/sid_method
python3 daily_scanner.py          # scan + email alerts + signal queue
python3 ibkr_paper.py --dry-run   # (optional) review auto-exec plan
```

### Full backtest
```
cd strategies/sid_method
python3 main.py                   # backtest over WATCHLIST, writes Excel to ../../output/
```

### Studies
```
cd strategies/sid_method/studies
python3 train_test_study.py       # train/test split validation
python3 validation_study.py       # survivorship + open-trade check
python3 intraday_study.py         # hourly vs daily execution comparison
```

### Automated daily run (cron)
```
bash strategies/sid_method/setup_cron.sh
```
Runs scanner + IBKR paper at 4:30 PM ET Mon–Fri. Requires TWS paper running on
port 7497 for live intraday updates (falls back to yfinance EOD automatically).

## Setup

```
cp .env.example .env          # fill in Gmail app password, IBKR port, etc.
pip install -r requirements.txt
```

Environment variables loaded from `.env`:
- `EMAIL_USER`, `EMAIL_PASSWORD` — Gmail SMTP app password (scanner alerts)
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` — IBKR TWS/Gateway (default `127.0.0.1:7497` paper)
- `GSHEET_ID` — Google Sheets ID for `scripts/ci_daily_scan.py` output (optional)

QuantConnect work runs in the QC cloud (MCP server / web IDE); no local data needed.

## Deploy (one command)

`quantconnect/deploy.py` ships a strategy to QuantConnect end-to-end — push →
compile → backtest → print stats — so a new algo goes live in one command:

```
make deploy STRATEGY=quantconnect/sid_quantconnect_experiments.py
# or with parameters:
python3 quantconnect/deploy.py quantconnect/sid_quantconnect_experiments.py \
    --params universe=watchlist side=long start_year=2024
```

Credentials come from the environment or `.env` (QuantConnect → Account →
Security): `QC_USER_ID`, `QC_API_TOKEN`. Add `--no-backtest` to deploy + compile
only.

## Tests

Deterministic strategy math is unit-tested (`tests/`, 33 tests, run in CI via
`.github/workflows/tests.yml`):

- `test_indicators.py` — RSI / MACD / SMA: invariants (RSI bounded [0,100],
  all-gains→100, warmup NaN, histogram ≡ line − signal) plus characterization
  locks on a fixed price vector, so a formula change trips a test.
- `test_signals.py` — the RSI 30/70 crossing logic (exact entry-signal semantics).
- `test_earnings.py` — the pure earnings-date helpers (the > 14-day rule).

```
pip install -r requirements-dev.txt
python3 -m pytest tests/ -q
```

The *deterministic math* is unit-tested; the *strategy behavior* is validated
empirically against the author's own logged trades (see `quantconnect/`).

## Conventions

- Shared modules use `from shared.X import ...`.
- Strategy-local modules use bare imports (e.g. `from backtest import ...` inside `sid_method/`).
- Each runnable script adds the project root to `sys.path` so absolute imports resolve when invoked directly.
- Paths (`cache/`, `output/`) resolve from the platform root regardless of where a script runs from.
- QuantConnect code is PEP8 (snake_case) per the QC LEAN API.
