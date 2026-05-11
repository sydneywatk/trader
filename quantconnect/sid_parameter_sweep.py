# =============================================================================
# SID Method — QuantConnect parameter sweep harness
# =============================================================================
#
# This is a parameterized variant of sid_quantconnect.py that exposes the
# strategy's key knobs to QuantConnect's Optimizer. Workflow:
#
#   1. In the QC web IDE, create a NEW project (separate from the main one).
#   2. Paste this entire file into main.py.
#   3. Open the project's "Parameters" tab and add:
#         rsi_oversold        (int, default 30)
#         rsi_overbought      (int, default 70)
#         rsi_exit            (int, default 50)
#         macd_fast           (int, default 12)
#         macd_slow           (int, default 26)
#         macd_signal         (int, default 9)
#         weekly_rsi_delta    (float, default 3.0)
#         max_trade_days      (int, default 10)
#         risk_pct            (float, default 0.01)
#         use_spy_filter      (int, default 1)        # 1/0 toggle
#         use_earnings_filter (int, default 1)
#   4. Click "Optimize" and set the grid (see SUGGESTED_GRID below).
#   5. QC's optimizer will run the cartesian product, sorting by
#      `Statistics["Sharpe Ratio"]` (or whichever metric you pick in the
#      optimizer UI). Reasonable optimizer-objective choices:
#        - Sharpe Ratio          (rewards consistent risk-adjusted return)
#        - Probabilistic Sharpe  (penalises low trade counts)
#        - Profit Factor         (rewards win-bias-vs-loss-bias)
#        - SortinoRatio          (rewards downside-aware sizing)
#      DO NOT optimise on raw Win Rate alone — that biases toward filters that
#      reduce trade count more than they improve edge.
#
# SUGGESTED_GRID (paste into QC optimizer UI):
#   rsi_oversold:      25, 28, 30, 32, 35
#   rsi_overbought:    65, 68, 70, 72, 75    (symmetric with rsi_oversold)
#   macd_fast:         8, 10, 12, 14
#   macd_slow:         20, 26, 32
#   macd_signal:       7, 9, 11
#   weekly_rsi_delta:  1.0, 3.0, 5.0
#   max_trade_days:    5, 10, 15
#   risk_pct:          0.005, 0.01, 0.015
#
# Full cartesian = ~12k runs. Start narrower (single-axis sweeps) before going
# full grid; the QC free tier has compute caps. See implementation notes.
#
# OUTPUT: each run emits a TAGGED line at end via self.log() prefixed with
# `SWEEP_RESULT|` so post-processing can grep+parse without running the QC API.
# =============================================================================

from AlgorithmImports import *
from datetime import timedelta
from collections import deque
import math


SID_UNIVERSE = [
    "GPN", "NUGT", "AKAM", "CME", "CMG", "FIS", "HBAN", "MKC", "RF",
    "APO", "COST", "FANG", "MS", "WM", "XLU", "PAYX", "TSLA", "BLK", "CMCSA",
    "CDW", "ED", "GDX", "HPE", "HRL", "MRSH", "OKE", "XLV", "AEP", "CHTR",
    "CTRA", "ELF", "IP", "KEYS", "SATS", "TLT", "XLC", "ZTS", "ABNB", "ACGL",
    "AFL", "APTV", "AVB", "CNP", "CPB", "F", "GM", "GS", "HST", "HUT", "IT",
    "JBHT", "JCI", "JPM", "LMT", "MAR", "MTB", "NUE", "PKG", "PPG", "REG",
    "SO", "TPR", "UNP", "XLI", "XOP", "BIIB", "AMT", "CSCO", "ROST", "TKO",
    "DIS", "DXCM", "EMR", "PG", "ADBE", "ANET", "BALL", "BG", "BMY", "CCI",
    "CF", "COP", "CTVA", "DDOG", "EFA", "EXEL", "FCX", "GDXJ", "LUV", "LYV",
    "MAS", "MRK", "UDR", "USB", "VTR", "AAPL", "DGX", "DOC", "EQIX",
]

ACCOUNT_SIZE = 100_000
SMA_PERIOD = 50
RSI_PERIOD = 14


# ----- Re-using the WeeklyRsiTracker pattern from the main file -----
class WeeklyRsiTracker:
    def __init__(self, period=RSI_PERIOD):
        self.period = period
        self.completed_weekly_closes = deque(maxlen=period + 5)
        self.current_week_close = None
        self.current_week_id = None

    def on_daily_bar(self, end_time, close):
        wid = (end_time.isocalendar().year, end_time.isocalendar().week)
        if self.current_week_id is None or wid != self.current_week_id:
            if self.current_week_close is not None:
                self.completed_weekly_closes.append(self.current_week_close)
            self.current_week_id = wid
        self.current_week_close = close

    def _wilder_rsi(self, closes):
        if len(closes) < self.period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        avg_gain = sum(gains[:self.period]) / self.period
        avg_loss = sum(losses[:self.period]) / self.period
        alpha = 1.0 / self.period
        for i in range(self.period, len(deltas)):
            avg_gain = (1 - alpha) * avg_gain + alpha * gains[i]
            avg_loss = (1 - alpha) * avg_loss + alpha * losses[i]
        if avg_loss == 0:
            return 100.0
        return 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))

    def current_and_prev(self):
        if self.current_week_close is None:
            return None, None
        completed = list(self.completed_weekly_closes)
        return self._wilder_rsi(completed + [self.current_week_close]), self._wilder_rsi(completed)


class SymbolState:
    def __init__(self):
        self.rsi = None
        self.rsi_prev = None
        self.macd = None
        self.weekly = WeeklyRsiTracker(RSI_PERIOD)
        self.pending_signal_type = None
        self.pending_signal_low = None
        self.pending_signal_high = None
        self.signal_bar = None
        self.in_trade = False
        self.side = None
        self.entry_price = None
        self.stop = None
        self.shares = None
        self.bars_held = 0
        self.stop_ticket = None


class SidSweep(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2020, 1, 1)
        self.set_cash(ACCOUNT_SIZE)
        self.set_benchmark("SPY")
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        # -------- Parameter binding --------
        # Each call reads from the QC project's Parameters config; defaults match
        # the main algorithm's constants for parity when run as a one-off.
        self.p_rsi_os = int(self.get_parameter("rsi_oversold") or 30)
        self.p_rsi_ob = int(self.get_parameter("rsi_overbought") or 70)
        self.p_rsi_exit = int(self.get_parameter("rsi_exit") or 50)
        self.p_macd_fast = int(self.get_parameter("macd_fast") or 12)
        self.p_macd_slow = int(self.get_parameter("macd_slow") or 26)
        self.p_macd_sig = int(self.get_parameter("macd_signal") or 9)
        self.p_weekly_delta = float(self.get_parameter("weekly_rsi_delta") or 3.0)
        self.p_max_days = int(self.get_parameter("max_trade_days") or 10)
        self.p_risk = float(self.get_parameter("risk_pct") or 0.01)
        self.p_spy_filter = bool(int(self.get_parameter("use_spy_filter") or 1))
        self.p_earnings_filter = bool(int(self.get_parameter("use_earnings_filter") or 1))

        # SPY
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
        self.spy_rsi = self.rsi(self.spy, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
        self.spy_sma = self.sma(self.spy, SMA_PERIOD, Resolution.DAILY)
        self.spy_rsi_prev = None

        # Universe
        self.symbol_state = {}
        for ticker in SID_UNIVERSE:
            try:
                sec = self.add_equity(ticker, Resolution.DAILY)
            except Exception:
                continue
            symbol = sec.symbol
            st = SymbolState()
            st.rsi = self.rsi(symbol, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
            st.macd = self.macd(symbol, self.p_macd_fast, self.p_macd_slow, self.p_macd_sig,
                                MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self.symbol_state[symbol] = st

        self.set_warm_up(timedelta(days=250))
        self.trade_log = []

    # ---------------- core loop ----------------
    def on_data(self, data):
        for symbol, st in self.symbol_state.items():
            if symbol in data and data[symbol] is not None:
                st.weekly.on_daily_bar(data[symbol].end_time, data[symbol].close)
        if self.is_warming_up:
            if self.spy_rsi.is_ready:
                self.spy_rsi_prev = self.spy_rsi.current.value
            return

        spy_long_ok = self._spy_ok("OS")
        spy_short_ok = self._spy_ok("OB")

        for symbol, st in self.symbol_state.items():
            if symbol not in data or data[symbol] is None:
                continue
            if not st.rsi.is_ready or not st.macd.is_ready:
                continue
            bar = data[symbol]
            if st.in_trade:
                self._maybe_exit(symbol, st, bar)
            else:
                self._maybe_enter(symbol, st, bar, spy_long_ok, spy_short_ok)
            st.rsi_prev = st.rsi.current.value

        if self.spy_rsi.is_ready:
            self.spy_rsi_prev = self.spy_rsi.current.value

    def _spy_ok(self, side):
        if not self.p_spy_filter:
            return True
        if not (self.spy_rsi.is_ready and self.spy_sma.is_ready) or self.spy_rsi_prev is None:
            return True
        close = self.securities[self.spy].price
        if close == 0:
            return True
        sma = self.spy_sma.current.value
        rsi_now, rsi_prev = self.spy_rsi.current.value, self.spy_rsi_prev
        if side == "OS":
            return (rsi_now > rsi_prev) and (close > sma)
        return (rsi_now < rsi_prev) and (close < sma * 1.02)

    def _maybe_enter(self, symbol, st, bar, spy_long_ok, spy_short_ok):
        rsi_now = st.rsi.current.value
        rsi_prev = st.rsi_prev
        if rsi_prev is not None:
            if rsi_prev >= self.p_rsi_os and rsi_now < self.p_rsi_os:
                st.pending_signal_type = "OS"
                st.signal_bar = self.time
                st.pending_signal_low = bar.low
                st.pending_signal_high = bar.high
            elif rsi_prev <= self.p_rsi_ob and rsi_now > self.p_rsi_ob:
                st.pending_signal_type = "OB"
                st.signal_bar = self.time
                st.pending_signal_low = bar.low
                st.pending_signal_high = bar.high

        if st.pending_signal_type is None:
            return
        st.pending_signal_low = min(st.pending_signal_low, bar.low)
        st.pending_signal_high = max(st.pending_signal_high, bar.high)

        # Gap / stale-signal abort
        if st.pending_signal_type == "OS" and rsi_now >= self.p_rsi_exit:
            st.pending_signal_type = None
            return
        if st.pending_signal_type == "OB" and rsi_now <= self.p_rsi_exit:
            st.pending_signal_type = None
            return

        if st.signal_bar == self.time:
            return

        # Conditions
        macd_l = st.macd.current.value
        macd_s = st.macd.signal.current.value
        if st.pending_signal_type == "OS":
            if not (rsi_now > rsi_prev and macd_l > macd_s):
                return
        else:
            if not (rsi_now < rsi_prev and macd_l < macd_s):
                return

        cw, pw = st.weekly.current_and_prev()
        if cw is None or pw is None:
            return
        delta = cw - pw
        if st.pending_signal_type == "OS" and delta <= self.p_weekly_delta:
            return
        if st.pending_signal_type == "OB" and delta >= -self.p_weekly_delta:
            return

        if st.pending_signal_type == "OS" and not spy_long_ok:
            return
        if st.pending_signal_type == "OB" and not spy_short_ok:
            return

        # Earnings filter — uses approximation; see main algorithm comments
        if self.p_earnings_filter:
            ed = self._earnings(symbol)
            if ed is not None and 0 <= (ed.date() - self.time.date()).days < 14:
                return

        # Enter
        side = st.pending_signal_type
        entry = bar.close
        if side == "OS":
            ll = st.pending_signal_low
            stop = math.floor(ll) - 1.0 if ll == math.floor(ll) else math.floor(ll)
        else:
            hh = st.pending_signal_high
            stop = math.ceil(hh) + 1.0 if hh == math.ceil(hh) else math.ceil(hh)
        rps = abs(entry - stop)
        if rps <= 0:
            st.pending_signal_type = None
            return
        risk_dollars = self.portfolio.total_portfolio_value * self.p_risk
        shares = math.floor(risk_dollars / rps)
        if shares <= 0:
            st.pending_signal_type = None
            return
        qty = shares if side == "OS" else -shares
        ticket = self.market_order(symbol, qty, tag=f"SWEEP entry {side}")
        if ticket.status not in (OrderStatus.FILLED, OrderStatus.SUBMITTED):
            st.pending_signal_type = None
            return
        st.in_trade = True
        st.side = "Long" if side == "OS" else "Short"
        st.entry_price = entry
        st.stop = stop
        st.shares = shares
        st.bars_held = 0
        stop_qty = -shares if side == "OS" else shares
        st.stop_ticket = self.stop_market_order(symbol, stop_qty, stop, tag="SWEEP stop")
        st.pending_signal_type = None

    def _maybe_exit(self, symbol, st, bar):
        st.bars_held += 1
        r = st.rsi.current.value
        if st.side == "Long" and r >= self.p_rsi_exit:
            return self._close(symbol, st, bar.close, "rsi50")
        if st.side == "Short" and r <= self.p_rsi_exit:
            return self._close(symbol, st, bar.close, "rsi50")
        if st.bars_held >= self.p_max_days:
            return self._close(symbol, st, bar.close, "time")

    def _close(self, symbol, st, exit_price, reason):
        if st.stop_ticket is not None:
            try:
                st.stop_ticket.cancel()
            except Exception:
                pass
        self.liquidate(symbol, tag=f"SWEEP exit {reason}")
        self._record(symbol, st, exit_price, reason)
        st.in_trade = False
        st.stop_ticket = None

    def _record(self, symbol, st, exit_price, reason):
        pnl = (exit_price - st.entry_price) * st.shares if st.side == "Long" \
              else (st.entry_price - exit_price) * st.shares
        self.trade_log.append({"pnl": pnl, "win": pnl > 0, "reason": reason})

    def _earnings(self, symbol):
        try:
            f = self.securities[symbol].fundamentals
            er = getattr(f, "earning_reports", None)
            if er is None:
                return None
            fd = getattr(er, "file_date", None)
            if fd is None or fd.value is None:
                return None
            return fd.value + timedelta(days=90)
        except Exception:
            return None

    def on_order_event(self, e):
        if e.status != OrderStatus.FILLED:
            return
        st = self.symbol_state.get(e.symbol)
        if st is None or not st.in_trade or st.stop_ticket is None:
            return
        if e.order_id == st.stop_ticket.order_id:
            self._record(e.symbol, st, e.fill_price, "stop")
            st.in_trade = False
            st.stop_ticket = None

    # ---------------- emit sweep result ----------------
    def on_end_of_algorithm(self):
        n = len(self.trade_log)
        if n == 0:
            self.log("SWEEP_RESULT|trades=0")
            return
        wins = sum(1 for t in self.trade_log if t["win"])
        wr = wins / n
        total = sum(t["pnl"] for t in self.trade_log)
        gw = sum(t["pnl"] for t in self.trade_log if t["pnl"] > 0)
        gl = abs(sum(t["pnl"] for t in self.trade_log if t["pnl"] <= 0))
        pf = (gw / gl) if gl > 0 else float("inf")

        # Single greppable line — parse with awk -F'|' from QC log download.
        # Parameters echoed so the row is self-describing.
        self.log(
            "SWEEP_RESULT|"
            f"rsi_os={self.p_rsi_os}|rsi_ob={self.p_rsi_ob}|rsi_exit={self.p_rsi_exit}|"
            f"macd={self.p_macd_fast}_{self.p_macd_slow}_{self.p_macd_sig}|"
            f"wkdelta={self.p_weekly_delta}|maxdays={self.p_max_days}|risk={self.p_risk}|"
            f"spy={int(self.p_spy_filter)}|earn={int(self.p_earnings_filter)}|"
            f"trades={n}|wr={wr:.4f}|pnl={total:.2f}|pf={pf:.2f}|"
            f"final={self.portfolio.total_portfolio_value:.2f}"
        )
