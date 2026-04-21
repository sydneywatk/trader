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


def _fmt_date(val) -> str:
    if val is None or val == "":
        return ""
    if hasattr(val, "strftime"):
        return val.strftime("%Y-%m-%d")
    return str(val)


def _fmt_price(val) -> str:
    """Format price as string with 2 decimal places."""
    if val is None or val == "":
        return ""
    try:
        return f"{float(val):.2f}"
    except (ValueError, TypeError):
        return str(val)


def _fmt_weekly_delta(val) -> str:
    """Format weekly RSI delta as '+4.5 (up)' / '-2.1 (down)' / '0.0 (flat)'."""
    if val is None:
        return ""
    v = float(val)
    direction = "up" if v > 0.5 else "down" if v < -0.5 else "flat"
    return f"{v:+.1f} ({direction})"


def build_rows(result: dict, run_ts: datetime) -> list[list]:
    """Build sheet rows from a scanner result dict."""
    run_date = run_ts.strftime("%Y-%m-%d")
    run_time = run_ts.strftime("%H:%M:%S")
    rows = []

    # ── Actionable setups → NEW_ENTRY ──
    for _tk, a in result.get("aligned", []):
        action = "Options (manual)" if a.get("tier") == 1 else "Shares (auto)"
        notes_parts = []
        if a.get("macd_crossed"):
            notes_parts.append("MACD crossed today")
        if a.get("weekly_rsi_delta") is not None and abs(a["weekly_rsi_delta"]) >= 5:
            notes_parts.append("strong weekly momentum")
        notes = "; ".join(notes_parts) if notes_parts else "entry conditions met"

        rows.append([
            run_date, run_time, a.get("ticker", _tk), a.get("order", ""),
            "NEW_ENTRY",
            _fmt_date(a.get("signal_date")),
            _fmt_price(a.get("entry_price")),
            _fmt_price(a.get("stop_loss")),
            _fmt_price(a.get("risk_per_share")),
            _fmt_price(a.get("position_size")),
            a.get("shares", ""),
            _fmt_weekly_delta(a.get("weekly_rsi_delta")),
            a.get("current_rsi", ""),
            a.get("macd_state", "crossed" if a.get("macd_crossed") else "aligned"),
            a.get("next_earnings", ""),
            a.get("days_to_earnings", "") if a.get("days_to_earnings") is not None else "",
            a.get("tier", ""),
            action,
            notes,
        ])

    # ── Watching → WATCHING ──
    for _tk, items in result.get("watching", []):
        for w in items:
            rows.append([
                run_date, run_time, _tk, w.get("order", ""),
                "WATCHING",
                _fmt_date(w.get("signal_date")),
                "", "", "", "", "",
                _fmt_weekly_delta(w.get("weekly_rsi_delta")),
                w.get("current_rsi", ""),
                w.get("macd_state", ""),
                w.get("next_earnings", ""),
                w.get("days_to_earnings") if w.get("days_to_earnings") is not None else "",
                "", "",
                w.get("notes", ""),
            ])

    # ── Open positions → OPEN_POSITION_UPDATE ──
    for _tk, o in result.get("open_trades", []):
        rows.append([
            run_date, run_time, _tk, o.get("order", ""),
            "OPEN_POSITION_UPDATE",
            _fmt_date(o.get("entry_date")),
            _fmt_price(o.get("entry_price")),
            _fmt_price(o.get("stop_loss")),
            "", "", "",
            _fmt_weekly_delta(o.get("weekly_rsi_delta")),
            o.get("current_rsi", ""),
            o.get("macd_state", ""),
            o.get("next_earnings", ""),
            o.get("days_to_earnings") if o.get("days_to_earnings") is not None else "",
            "", "",
            o.get("notes", ""),
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
