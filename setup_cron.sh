#!/usr/bin/env bash
# Install the SID Scanner daily cron job.
# Runs scanner + IBKR paper trading at 4:30 PM ET (21:30 UTC) Mon–Fri.
#
# Note: For live IBKR data, ensure TWS is running BEFORE
# the cron job fires at 4:30pm ET. Without TWS the scanner
# falls back to yfinance (EOD) data automatically.

set -euo pipefail

PROJECT_DIR="$HOME/sid_backtester"
PYTHON_BIN="$(command -v python3)"
LOG_DIR="$PROJECT_DIR/output"
mkdir -p "$LOG_DIR"

CRON_CMD="30 16 * * 1-5 cd $PROJECT_DIR && $PYTHON_BIN daily_scanner.py && $PYTHON_BIN ibkr_paper.py >> $LOG_DIR/ibkr_log.txt 2>&1"
MARKER="# SID_SCANNER_CRON"

# Pull current crontab (may be empty)
CURRENT="$(crontab -l 2>/dev/null || true)"

# Drop any previous SID_SCANNER_CRON line(s)
FILTERED="$(echo "$CURRENT" | grep -v "$MARKER" || true)"

# Append the new entry with marker
NEW_CRON="$(printf '%s\n%s %s\n' "$FILTERED" "$CRON_CMD" "$MARKER")"
echo "$NEW_CRON" | crontab -

echo "Installed cron job:"
echo "  $CRON_CMD"
echo
echo "Current crontab:"
crontab -l
