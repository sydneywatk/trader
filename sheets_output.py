"""Google Sheets output for the SID daily scanner.

Authentication priority:
  1. GSHEET_SERVICE_ACCOUNT_JSON env var (full JSON string — used in CI)
  2. Local file at ~/secrets/sid-bot-key.json (for local dev/testing)

Sheet ID comes from GSHEET_ID env var.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LOCAL_KEY_PATH = Path.home() / "secrets" / "sid-bot-key.json"

HEADER = [
    "Run Date", "Run Time", "Ticker", "Direction", "Signal Type",
    "Signal Date", "Entry Price", "Stop Loss", "Risk Per Share",
    "Position Size $", "Max Shares", "Weekly RSI Δ", "Daily RSI",
    "MACD State", "Earnings Date", "Days to Earnings", "Tier",
    "Suggested Action", "Notes",
]


def _get_credentials() -> Credentials:
    raw = os.environ.get("GSHEET_SERVICE_ACCOUNT_JSON")
    if raw:
        info = json.loads(raw)
        return Credentials.from_service_account_info(info, scopes=SCOPES)

    if LOCAL_KEY_PATH.exists():
        return Credentials.from_service_account_file(str(LOCAL_KEY_PATH), scopes=SCOPES)

    raise RuntimeError(
        "Google Sheets auth failed: set GSHEET_SERVICE_ACCOUNT_JSON env var "
        f"or place a service account key at {LOCAL_KEY_PATH}"
    )


def _get_sheet_id() -> str:
    sid = os.environ.get("GSHEET_ID")
    if not sid:
        raise RuntimeError(
            "GSHEET_ID env var is not set. Set it to the Google Sheet ID "
            "(the long string in the Sheet URL between /d/ and /edit)."
        )
    return sid


def _ensure_tab(spreadsheet: gspread.Spreadsheet, tab_name: str) -> gspread.Worksheet:
    """Return the worksheet for tab_name, creating it with headers if needed."""
    try:
        ws = spreadsheet.worksheet(tab_name)
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=tab_name, rows=1000, cols=len(HEADER))
        ws.append_row(HEADER, value_input_option="RAW")
    return ws


def _format_signal_date(val) -> str:
    """Convert pd.Timestamp / datetime / str to YYYY-MM-DD string."""
    if val is None:
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val)


def build_rows(result: dict, run_ts: datetime) -> list[list]:
    """Build sheet rows from a scanner result dict.

    Returns a list of row-lists (each matching HEADER columns).
    """
    run_date = run_ts.strftime("%Y-%m-%d")
    run_time = run_ts.strftime("%H:%M:%S")
    rows = []

    # Actionable setups → NEW_ENTRY
    for _tk, a in result.get("aligned", []):
        earn_date = a.get("next_earnings") or ""
        if earn_date and a.get("signal_date"):
            try:
                from datetime import datetime as _dt
                sig_dt = a["signal_date"]
                if hasattr(sig_dt, "date"):
                    sig_dt = sig_dt.date()
                earn_dt = _dt.strptime(earn_date, "%Y-%m-%d").date() if isinstance(earn_date, str) else earn_date
                days_to_earn = (earn_dt - sig_dt).days
            except Exception:
                days_to_earn = ""
        else:
            days_to_earn = ""

        macd_state = "CROSSED" if a.get("macd_crossed") else "ALIGNED"
        action = "Options (manual)" if a.get("tier") == 1 else "Shares (auto)"
        rows.append([
            run_date, run_time, a.get("ticker", _tk), a.get("order", ""),
            "NEW_ENTRY",
            _format_signal_date(a.get("signal_date")),
            a.get("entry_price", ""), a.get("stop_loss", ""),
            a.get("risk_per_share", ""), a.get("position_size", ""),
            a.get("shares", ""), a.get("weekly_rsi_delta", ""),
            a.get("current_rsi", ""), macd_state,
            earn_date, days_to_earn, a.get("tier", ""),
            action, "",
        ])

    # Watching → WATCHING
    for _tk, items in result.get("watching", []):
        for w in items:
            note = "SPY-blocked" if w.get("spy_blocked") else ""
            rows.append([
                run_date, run_time, _tk, w.get("order", ""), "WATCHING",
                _format_signal_date(w.get("signal_date")),
                "", "", "", "", "", "", w.get("current_rsi", ""),
                "", "", "", "", "", note,
            ])

    # Open positions → OPEN_POSITION_UPDATE
    for _tk, o in result.get("open_trades", []):
        note = "time-exit threshold" if o.get("days_in", 0) >= 10 else ""
        rows.append([
            run_date, run_time, _tk, o.get("order", ""),
            "OPEN_POSITION_UPDATE",
            _format_signal_date(o.get("entry_date")),
            o.get("entry_price", ""), o.get("stop_loss", ""),
            "", "", "", "", o.get("current_rsi", ""),
            "", "", "", "", "", f"{o.get('days_in', '')}d in trade. {note}".strip(),
        ])

    return rows


def append_signals(result: dict, run_timestamp: datetime) -> int:
    """Write scanner results to Google Sheets. Returns number of rows written."""
    rows = build_rows(result, run_timestamp)
    if not rows:
        return 0

    creds = _get_credentials()
    gc = gspread.authorize(creds)
    sheet_id = _get_sheet_id()

    try:
        spreadsheet = gc.open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound:
        raise RuntimeError(
            f"Google Sheet not found (ID: {sheet_id}). "
            "Check the GSHEET_ID value and ensure the service account "
            "has Editor access to the sheet."
        )

    tab_name = str(run_timestamp.year)
    ws = _ensure_tab(spreadsheet, tab_name)
    ws.append_rows(rows, value_input_option="USER_ENTERED")
    return len(rows)
