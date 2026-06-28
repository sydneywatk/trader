# SID Method

RSI 30/70 entry with weekly RSI alignment, MACD confirmation, earnings buffer, and SPY trend filter.

> **Local engine — secondary track.** This is the original standalone Python
> backtester + daily EOD scanner (email alerts, signal queue, optional **IBKR**
> paper execution). The pipeline this project showcases runs on **QuantConnect**
> — see the [top-level README](../../README.md). For honest, survivorship-free
> performance numbers, trust the QuantConnect validation, not curated-watchlist
> backtests here.

## The signal

1. **Daily RSI** crosses 30 (long signal, "OS") or 70 (short signal, "OB").
2. **Entry bar** after signal must satisfy, same day:
   - RSI moving toward 50 (rising for long, falling for short) but not yet at 50
   - MACD line above signal line **OR** histogram positive and increasing (long, mirrored for short)
   - Weekly RSI moved >3 points in trade direction vs prior week
   - Earnings > 14 calendar days away (if earnings data available)
   - SPY trend aligned (SPY RSI direction + SPY vs SMA50)
3. **Stop loss**: lowest low (long) or highest high (short) between signal and entry, rounded to whole dollar with 1pt buffer.
4. **Exit priority**: stop → RSI 50 → 10 trading days → earnings approach.

## Files

| File | Purpose |
|---|---|
| `config.py` | Watchlist + SID thresholds; re-exports shared constants |
| `signals.py` | RSI OS/OB signal detection |
| `backtest.py` | Entry conditions + stop + exit + sizing |
| `daily_scanner.py` | Live scanner: email alerts, signal queue, Tier scoring |
| `ibkr_paper.py` (in `execution/`) | Reads signal queue, submits IBKR paper orders |
| `scanner_universe.py` | Rank ~500 tickers by backtest WR to build watchlist |
| `universe.py` | Fetch Russell/NASDAQ ticker lists |
| `main.py` | Batch backtest over WATCHLIST → Excel report |
| `output.py` | Excel formatting for backtest results |
| `sheets_output.py` | Google Sheets formatter for CI runs |
| `setup_cron.sh` | Install 4:30 PM ET Mon–Fri cron job |
| `studies/` | Train/test split, validation, intraday execution, diagnostics |
| `scripts/ci_daily_scan.py` | CI-friendly scanner entry (sheets-only, no email/IBKR) |

## Run

```
python3 daily_scanner.py           # scan today + email + queue
python3 main.py                    # full backtest → Excel
python3 studies/train_test_study.py
python3 scripts/ci_daily_scan.py --dry-run
```
