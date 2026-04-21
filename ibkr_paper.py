"""IBKR Paper Trading Executor for SID Method Tier-2 signals.

=============================================================================
  SETUP INSTRUCTIONS
=============================================================================
  1. Download and install TWS (or IB Gateway) from interactivebrokers.com.
  2. Log into TWS with your PAPER account credentials.
  3. Enable API access:
        Edit → Global Configuration → API → Settings
           • Enable ActiveX and Socket Clients  ✓
           • Socket port: 7497  (paper)        | 7496 = live, do NOT use
           • Trusted IPs: 127.0.0.1
           • Read-Only API:  OFF  (must be off to place orders)
  4. Install the official IB Python API:
        pip install ibapi
  5. Run TWS, then run:  python3 ibkr_paper.py
=============================================================================

Workflow:
  - Reads tier-2 signals from ./output/ibkr_signal_queue.json
    (written by daily_scanner.py)
  - Places a LIMIT order at the calculated entry price
  - Places a STOP order (attached) for the stop loss
  - Tracks every filled entry in ./output/ibkr_open_trades.json
  - Checks all tracked open trades for exit conditions (RSI-50 / day-10 / stop)
    and places market orders to close where appropriate

Safety:
  - Paper trading port ONLY (7497). If connection fails, logs and exits cleanly.
  - Tier-1 signals are NEVER auto-executed here — they alert only (options).
"""
from __future__ import annotations

import os, sys, json, time, threading
from datetime import datetime
from queue import Queue

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    from ibapi.order import Order
    _IBAPI = True
except ImportError:
    _IBAPI = False

import pandas as pd

from config import OUTPUT_DIR, RSI_EXIT, MAX_TRADE_DAYS
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi
from signals import find_rsi_signals
from earnings import fetch_earnings_dates
from backtest import run_backtest_for_ticker

QUEUE_PATH = os.path.join(OUTPUT_DIR, "ibkr_signal_queue.json")
OPEN_PATH = os.path.join(OUTPUT_DIR, "ibkr_open_trades.json")

IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "102"))  # different from scanner


# ── IB client ────────────────────────────────────────────────────────────────
if _IBAPI:
    class _Executor(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.next_order_id = None
            self.order_status: dict[int, dict] = {}
            self.ready = threading.Event()

        def nextValidId(self, orderId):
            self.next_order_id = orderId
            self.ready.set()

        def orderStatus(self, orderId, status, filled, remaining, avgFillPrice,
                        permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice):
            self.order_status[orderId] = {
                "status": status, "filled": filled, "avgFillPrice": avgFillPrice,
            }

        def error(self, reqId, errorCode, errorString, *args, **kwargs):
            # 2104/2106/2158 are benign connection status messages
            if errorCode in (2104, 2106, 2158, 2103, 2107):
                return
            print(f"  [IB err] reqId={reqId} code={errorCode} {errorString}")


def _mk_contract(sym: str):
    c = Contract(); c.symbol = sym; c.secType = "STK"
    c.exchange = "SMART"; c.currency = "USD"
    return c


def _mk_order(action: str, qty: int, order_type: str,
              limit_price: float = None, stop_price: float = None,
              parent_id: int = None, transmit: bool = True) -> "Order":
    o = Order()
    o.action = action
    o.totalQuantity = qty
    o.orderType = order_type
    if limit_price is not None: o.lmtPrice = limit_price
    if stop_price is not None:  o.auxPrice = stop_price
    if parent_id is not None:   o.parentId = parent_id
    o.transmit = transmit
    o.eTradeOnly = False
    o.firmQuoteOnly = False
    return o


def _load_json(path, default):
    if not os.path.exists(path): return default
    try:
        with open(path) as f: return json.load(f)
    except Exception: return default


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f: json.dump(obj, f, indent=2, default=str)


# ── Execution ────────────────────────────────────────────────────────────────
def execute_signal(client, signal: dict) -> dict | None:
    """Place a bracket: limit entry + attached stop. Returns record dict."""
    sym = signal["ticker"]; order = signal["order"]; qty = signal["shares"]
    entry_px = signal["entry_price"]; stop_px = signal["stop_loss"]
    action_entry = "BUY" if order == "Long" else "SELL"
    action_stop  = "SELL" if order == "Long" else "BUY"

    parent_id = client.next_order_id
    client.next_order_id += 1
    child_id = client.next_order_id
    client.next_order_id += 1

    parent = _mk_order(action_entry, qty, "LMT", limit_price=entry_px, transmit=False)
    stop = _mk_order(action_stop, qty, "STP", stop_price=stop_px,
                     parent_id=parent_id, transmit=True)

    c = _mk_contract(sym)
    try:
        client.placeOrder(parent_id, c, parent)
        client.placeOrder(child_id, c, stop)
    except Exception as e:
        print(f"  [{sym}] placeOrder failed: {e}")
        return None

    rec = {
        "ib_parent_id": parent_id, "ib_stop_id": child_id,
        "ticker": sym, "order": order, "tier": signal["tier"],
        "entry_price": entry_px, "stop_loss": stop_px, "shares": qty,
        "entry_date": datetime.now().strftime("%Y-%m-%d"),
        "signal_date": signal["signal_date"],
        "placed_at": datetime.now().isoformat(timespec="seconds"),
        "status": "SUBMITTED",
    }
    print(f"  [{sym}] Tier-{signal['tier']} {order} LMT ${entry_px} "
          f"STP ${stop_px} x{qty} → parent#{parent_id} stop#{child_id}")
    return rec


# ── Exit management ──────────────────────────────────────────────────────────
def check_exits(client, open_trades: list[dict]) -> list[dict]:
    """For each tracked open trade, check RSI-50 / day-10 / stop. Place market
    orders where appropriate. Returns updated list (closed ones marked)."""
    remaining = []
    for t in open_trades:
        if t.get("status") == "CLOSED":
            continue
        sym = t["ticker"]
        try:
            df = add_daily_indicators(fetch_daily(sym))
            last = df.iloc[-1]
            rsi = last["RSI"]
            entry_date = pd.Timestamp(t["entry_date"])
            days_in = (df.index[-1] - entry_date).days

            should_close = False; reason = ""
            if t["order"] == "Long" and pd.notna(rsi) and rsi >= RSI_EXIT:
                should_close, reason = True, "RSI reached 50"
            elif t["order"] == "Short" and pd.notna(rsi) and rsi <= RSI_EXIT:
                should_close, reason = True, "RSI reached 50"
            elif days_in >= MAX_TRADE_DAYS:
                should_close, reason = True, f"Day {days_in} time exit"

            if should_close:
                oid = client.next_order_id; client.next_order_id += 1
                action = "SELL" if t["order"] == "Long" else "BUY"
                mkt = _mk_order(action, t["shares"], "MKT")
                client.placeOrder(oid, _mk_contract(sym), mkt)
                t["status"] = "CLOSED"
                t["close_reason"] = reason
                t["close_order_id"] = oid
                t["closed_at"] = datetime.now().isoformat(timespec="seconds")
                print(f"  [{sym}] EXIT ({reason}) → market order #{oid}")
        except Exception as e:
            print(f"  [{sym}] exit check failed: {e}")

        remaining.append(t)
    return remaining


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print(f"  IBKR PAPER TRADING — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 70)

    if not _IBAPI:
        print("  ibapi not installed — pip install ibapi. Exiting.")
        return

    queue = _load_json(QUEUE_PATH, [])
    open_trades = _load_json(OPEN_PATH, [])
    print(f"  Queue: {len(queue)} Tier-2 signals")
    print(f"  Open:  {sum(1 for t in open_trades if t.get('status') != 'CLOSED')} active positions")

    # Connect
    client = _Executor()
    try:
        client.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
    except Exception as e:
        print(f"  Cannot connect to TWS at {IBKR_HOST}:{IBKR_PORT} — {e}")
        print("  Start TWS (paper), enable API, and retry. Skipping exec.")
        return

    thread = threading.Thread(target=client.run, daemon=True)
    thread.start()
    if not client.ready.wait(timeout=5):
        print("  TWS connected but nextValidId not received — skipping.")
        try: client.disconnect()
        except Exception: pass
        return
    print(f"  Connected, next_order_id={client.next_order_id}")

    # Execute new Tier-2 signals (skip if already open for that ticker)
    open_tickers = {t["ticker"] for t in open_trades if t.get("status") != "CLOSED"}
    new_records = []
    for sig in queue:
        if sig.get("tier") != 2:
            continue
        if sig["ticker"] in open_tickers:
            print(f"  [{sig['ticker']}] already open — skipping.")
            continue
        rec = execute_signal(client, sig)
        if rec: new_records.append(rec)
    open_trades.extend(new_records)

    # Check exits
    open_trades = check_exits(client, open_trades)

    # Persist
    _save_json(OPEN_PATH, open_trades)
    # Clear the queue (consumed)
    _save_json(QUEUE_PATH, [])

    time.sleep(1.5)  # let final statuses arrive
    try: client.disconnect()
    except Exception: pass
    print(f"\n  Wrote {OPEN_PATH}")
    print("=" * 70)


if __name__ == "__main__":
    main()
