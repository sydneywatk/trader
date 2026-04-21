# trader

Multi-strategy algorithmic trading platform for US equities. Each strategy is
self-contained under `strategies/` and shares common data, indicator, and
earnings utilities from `shared/`.

## Layout

```
trader/
├── shared/              # Reusable modules across strategies
│   ├── config.py            platform-level constants (paths, periods)
│   ├── data.py              yfinance daily/weekly OHLCV + cache
│   ├── indicators.py        RSI / MACD / SMA
│   └── earnings.py          earnings date fetch + proximity check
├── strategies/
│   ├── sid_method/      # Active — RSI 30/70 + weekly alignment
│   └── supply_demand/   # Planned — zone detection (see RESEARCH.md)
├── execution/           # Broker adapters
│   ├── ibkr_data.py         IBKR live tick stream
│   └── ibkr_paper.py        IBKR paper trading executor
├── cache/               # (gitignored) OHLCV + earnings cache
├── output/              # (gitignored) Excel reports, logs, signal queues
├── .env                 # (gitignored) secrets (Gmail, Sheets, IBKR)
└── requirements.txt
```

## Strategies

| Strategy | Status | Notes |
|---|---|---|
| **SID Method** (`strategies/sid_method/`) | **Live** | RSI 30/70 entry + weekly RSI alignment + MACD + earnings/SPY filter. 88.4% WR on out-of-sample backtest (100 tickers, 2024-today). Daily scanner with email + optional IBKR paper execution. |
| **Supply & Demand** (`strategies/supply_demand/`) | Planned | See `strategies/supply_demand/RESEARCH.md` for zone detection design. Target build: 2–3 weeks. |

## Running strategies

### SID Method — daily scanner (EOD)
```
cd strategies/sid_method
python3 daily_scanner.py          # scan + email alerts + signal queue
python3 ibkr_paper.py --dry-run   # (optional) review auto-exec plan
```

### SID Method — full backtest
```
cd strategies/sid_method
python3 main.py                   # backtest over WATCHLIST, writes Excel to ../../output/
```

### SID Method — studies
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
Runs scanner + IBKR paper at 4:30 PM ET Mon–Fri. Requires TWS paper running on port 7497 for live intraday updates (falls back to yfinance EOD automatically).

## Setup

```
cp .env.example .env          # fill in Gmail app password, IBKR port, etc.
pip install -r requirements.txt
```

Environment variables loaded from `.env`:
- `EMAIL_USER`, `EMAIL_PASSWORD` — Gmail SMTP app password (scanner alerts)
- `IBKR_HOST`, `IBKR_PORT`, `IBKR_CLIENT_ID` — IBKR TWS/Gateway (default `127.0.0.1:7497` paper)
- `GSHEET_ID` — Google Sheets ID for `scripts/ci_daily_scan.py` output (optional)

## Conventions

- Shared modules use `from shared.X import ...`.
- Strategy-local modules use bare imports (e.g. `from backtest import ...` inside `sid_method/`).
- Each runnable script adds the project root to `sys.path` at the top so absolute imports resolve when invoked directly.
- Paths (`cache/`, `output/`) resolve from the platform root regardless of where a script runs from.
