"""CI entry point for the SID daily scanner.

Runs the scanner in --sheets-only mode (no email, no IBKR),
then writes results to Google Sheets.

Flags:
    --dry-run   Print what WOULD be written to Sheets, don't actually write.

Usage (from GitHub Actions):
    python scripts/ci_daily_scan.py

Local testing:
    GSHEET_ID=<your-sheet-id> python scripts/ci_daily_scan.py --dry-run
"""
import sys
import os
import time
from datetime import datetime
from pathlib import Path

# Capture flags before overwriting argv
_original_argv = sys.argv[:]
dry_run = "--dry-run" in _original_argv

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[3]))  # trader/
sys.path.insert(0, str(_HERE.parents[1]))  # strategies/sid_method/ (for daily_scanner, sheets_output)

# Force sheets-only mode for the scanner
sys.argv = [_original_argv[0], "--sheets-only"]


def main() -> int:
    start = time.time()
    run_ts = datetime.now()

    try:
        from daily_scanner import main as run_scanner
        result = run_scanner()
    except Exception as e:
        print(f"\nERROR: Scanner failed — {e}", file=sys.stderr)
        return 1

    elapsed = time.time() - start
    n_signals = len(result["aligned"])
    n_scanned = result["tickers_scanned"]

    # Google Sheets output
    from sheets_output import build_rows, append_signals, HEADER
    rows = build_rows(result, run_ts)

    if dry_run:
        print(f"\n[DRY RUN] Would write {len(rows)} rows to Google Sheets:")
        print(f"  Tab: {run_ts.year}")
        print(f"  Columns: {' | '.join(HEADER)}")
        print()
        for row in rows:
            sig_type = row[4]
            ticker = row[2]
            direction = row[3]
            rsi = row[12]
            wk_rsi = row[11]
            macd = row[13]
            earn = row[14]
            d2e = row[15]
            stop = row[7]
            note = row[18]
            print(f"  [{sig_type:>24s}] {ticker:>6s} {direction:>5s}  "
                  f"RSI {rsi}  WkΔ {wk_rsi}  MACD: {macd}  "
                  f"Earn: {earn} ({d2e}d)  Stop: {stop}  | {note}")
    else:
        try:
            n_written = append_signals(result, run_ts)
            print(f"\n  [sheets] Wrote {n_written} rows to Google Sheets "
                  f"(tab: {run_ts.year})")
        except Exception as e:
            print(f"\n  [sheets] FAILED: {e}", file=sys.stderr)

    print(f"\nScan complete: {n_signals} signals found, "
          f"{n_scanned} tickers scanned, {elapsed:.0f}s elapsed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
