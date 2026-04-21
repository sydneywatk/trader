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

# Capture --dry-run before overwriting argv
_original_argv = sys.argv[:]
dry_run = "--dry-run" in _original_argv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force sheets-only mode for the scanner
sys.argv = [sys.argv[0], "--sheets-only"]


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
        for row in rows:
            sig_type = row[4]
            ticker = row[2]
            direction = row[3]
            rsi = row[12]
            note = row[18] if row[18] else ""
            print(f"  [{sig_type:>24s}]  {ticker:>6s}  {direction:>5s}  "
                  f"RSI {rsi}  {note}")
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
