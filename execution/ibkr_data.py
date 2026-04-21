"""Minimal IBKR TWS client for fetching live price + historical daily bars.

Used by daily_scanner.py. If TWS is not reachable, probe() returns False and
the scanner falls back to yfinance EOD data.

Uses the official `ibapi` library (IB's callback-based API).
"""
from __future__ import annotations

import os
import socket
import threading
import time
from queue import Queue, Empty

import pandas as pd

try:
    from ibapi.client import EClient
    from ibapi.wrapper import EWrapper
    from ibapi.contract import Contract
    _IBAPI_AVAILABLE = True
except ImportError:
    _IBAPI_AVAILABLE = False


IBKR_HOST = os.environ.get("IBKR_HOST", "127.0.0.1")
IBKR_PORT = int(os.environ.get("IBKR_PORT", "7497"))
IBKR_CLIENT_ID = int(os.environ.get("IBKR_CLIENT_ID", "101"))


def probe() -> bool:
    """Quick TCP probe: is TWS/IB Gateway listening?"""
    if not _IBAPI_AVAILABLE:
        return False
    try:
        with socket.create_connection((IBKR_HOST, IBKR_PORT), timeout=1.5):
            return True
    except Exception:
        return False


if _IBAPI_AVAILABLE:

    class _IBClient(EWrapper, EClient):
        def __init__(self):
            EClient.__init__(self, self)
            self.historical_q: dict[int, list] = {}
            self.tick_q: dict[int, Queue] = {}
            self._done: dict[int, threading.Event] = {}

        def historicalData(self, reqId, bar):
            self.historical_q.setdefault(reqId, []).append({
                "date": bar.date,
                "open": bar.open, "high": bar.high,
                "low": bar.low, "close": bar.close, "volume": bar.volume,
            })

        def historicalDataEnd(self, reqId, start, end):
            ev = self._done.get(reqId)
            if ev:
                ev.set()

        def tickPrice(self, reqId, tickType, price, attrib):
            # tickType 4 = LAST, 9 = CLOSE (prev day)
            q = self.tick_q.get(reqId)
            if q is not None and tickType == 4 and price > 0:
                q.put(price)
                ev = self._done.get(reqId)
                if ev:
                    ev.set()


    def _mk_contract(symbol: str) -> Contract:
        c = Contract()
        c.symbol = symbol
        c.secType = "STK"
        c.exchange = "SMART"
        c.currency = "USD"
        c.primaryExchange = "NASDAQ"
        return c


    class IBKRSession:
        """Context-managed IBKR session — connects, disconnects cleanly."""

        def __init__(self):
            self.client = _IBClient()
            self._thread = None

        def __enter__(self):
            self.client.connect(IBKR_HOST, IBKR_PORT, clientId=IBKR_CLIENT_ID)
            self._thread = threading.Thread(target=self.client.run, daemon=True)
            self._thread.start()
            # Wait briefly for connection handshake
            for _ in range(20):
                if self.client.isConnected():
                    break
                time.sleep(0.1)
            return self

        def __exit__(self, *exc):
            try:
                self.client.disconnect()
            except Exception:
                pass

        def fetch_daily_bars(self, symbol: str, n_days: int = 60) -> pd.DataFrame:
            """Fetch last N days of daily bars for a stock. Returns DataFrame
            with columns Open/High/Low/Close/Volume indexed by date."""
            req_id = abs(hash(symbol)) % 10_000 + 1
            self.client.historical_q[req_id] = []
            self.client._done[req_id] = threading.Event()
            self.client.reqHistoricalData(
                req_id, _mk_contract(symbol), "",
                f"{n_days} D", "1 day", "TRADES", 1, 1, False, []
            )
            if not self.client._done[req_id].wait(timeout=10):
                return pd.DataFrame()
            bars = self.client.historical_q.get(req_id, [])
            if not bars:
                return pd.DataFrame()
            df = pd.DataFrame(bars)
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").rename(columns={
                "open": "Open", "high": "High", "low": "Low",
                "close": "Close", "volume": "Volume"
            })
            return df

        def fetch_last_price(self, symbol: str, timeout: float = 3.0) -> float | None:
            """Fetch current (delayed or live depending on market data subs)
            last price via a streaming tick. Returns None on timeout."""
            req_id = abs(hash(symbol + "_tick")) % 10_000 + 5_000
            self.client.tick_q[req_id] = Queue()
            self.client._done[req_id] = threading.Event()
            try:
                self.client.reqMarketDataType(3)  # delayed data fallback
            except Exception:
                pass
            self.client.reqMktData(req_id, _mk_contract(symbol), "", False, False, [])
            try:
                price = self.client.tick_q[req_id].get(timeout=timeout)
                return price
            except Empty:
                return None
            finally:
                try:
                    self.client.cancelMktData(req_id)
                except Exception:
                    pass

else:

    class IBKRSession:
        def __enter__(self):
            raise RuntimeError("ibapi not installed — pip install ibapi")

        def __exit__(self, *exc):
            pass
