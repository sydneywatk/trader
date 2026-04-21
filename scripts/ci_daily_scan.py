"""CI entry point for the SID daily scanner.

Runs the scanner in --sheets-only mode (no email, no IBKR).
Exits 0 on success, non-zero with a clear error on failure.
Prints a one-line summary at the end.

Usage (from GitHub Actions):
    python scripts/ci_daily_scan.py
"""
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Force sheets-only mode regardless of CLI args
sys.argv = [sys.argv[0], "--sheets-only"]


def main() -> int:
    start = time.time()
    try:
        from daily_scanner import main as run_scanner
        result = run_scanner()
    except Exception as e:
        print(f"\nERROR: Scanner failed — {e}", file=sys.stderr)
        return 1

    elapsed = time.time() - start
    n_signals = len(result["aligned"])
    n_scanned = result["tickers_scanned"]

    print(f"\nScan complete: {n_signals} signals found, "
          f"{n_scanned} tickers scanned, {elapsed:.0f}s elapsed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
