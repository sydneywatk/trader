# =============================================================================
# SID Method — QuantConnect / LEAN port  ·  DYNAMIC UNIVERSE edition
# =============================================================================
#
# WHY THIS FILE EXISTS
#   The fixed-99 port (sid_quantconnect.py) runs the method on a hand-curated
#   watchlist that was itself selected by ranking tickers on historical win
#   rate. That makes its win rate circular — we grade the method on the names
#   it was chosen to fit. THIS file removes that bias entirely: it runs the
#   identical SID logic on a broad, liquidity-selected, survivorship-free
#   universe (top N US equities by dollar volume, re-selected daily, including
#   names that later delisted). It is the true "is the METHOD any good?" test.
#
#   The strategy logic (signals, 5-condition entry gate, stop rounding, exit
#   priority, earnings filter) is byte-for-byte the same as sid_quantconnect.py.
#   The ONLY differences are: (1) the universe is dynamic, (2) per-symbol
#   indicators are created/destroyed as names enter/leave the universe, and
#   (3) earnings are keyed by ticker string for robust matching.
#
# HOW TO READ THE RESULT
#   ~75-80% WR on 500 unseen names  → the method has real, transferable edge.
#   ~50-60% WR                      → the edge lived mostly in ticker selection.
#   Either answer is honest and useful. The end-of-run log prints WR, per-side
#   breakdown, exit-reason mix, AND earnings-filter coverage (so you can see the
#   14-day earnings rule is actually firing, not silently failing open).
#
# RUN
#   Paste this whole file into a new QC algorithm as main.py and Backtest.
#   500 daily-resolution names over 2020-2026 takes ~15-30 min. Lower
#   UNIVERSE_SIZE to 250 for a faster first pass.
# =============================================================================

from AlgorithmImports import *
from datetime import timedelta
from collections import deque, Counter
import math


# -----------------------------------------------------------------------------
# Strategy constants — identical to strategies/sid_method/config.py
# -----------------------------------------------------------------------------
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SMA_PERIOD = 50

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_EXIT = 50
WEEKLY_RSI_MIN_DELTA = 3
MAX_TRADE_DAYS = 10
EARNINGS_MIN_DAYS = 14

ACCOUNT_SIZE = 100_000
RISK_PCT = 0.01
MAX_OPEN_POSITIONS = 20      # margin safety on a broad universe
UNIVERSE_SIZE = 500          # top N by dollar volume, re-selected daily

USE_SPY_FILTER = True
USE_EARNINGS_FILTER = True


# -----------------------------------------------------------------------------
# WeeklyRsiTracker — "current in-progress week vs prior completed week" weekly
# RSI, matching the trader repo's daily->weekly resample semantics.
# -----------------------------------------------------------------------------
class WeeklyRsiTracker:
    def __init__(self, period=14):
        self.period = period
        self.completed_weekly_closes = deque(maxlen=period + 5)
        self.current_week_close = None
        self.current_week_id = None

    def on_daily_bar(self, end_time, close):
        week_id = (end_time.isocalendar()[0], end_time.isocalendar()[1])
        if self.current_week_id is None or week_id != self.current_week_id:
            if self.current_week_close is not None:
                self.completed_weekly_closes.append(self.current_week_close)
            self.current_week_id = week_id
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
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def current_and_prev_rsi(self):
        if self.current_week_close is None:
            return None, None
        completed = list(self.completed_weekly_closes)
        curr = self._wilder_rsi(completed + [self.current_week_close])
        prev = self._wilder_rsi(completed)
        return curr, prev


# -----------------------------------------------------------------------------
# Per-symbol state
# -----------------------------------------------------------------------------
class SymbolState:
    def __init__(self):
        self.rsi = None
        self.rsi_prev = None
        self.macd = None
        self.macd_hist_prev = None
        self.weekly = WeeklyRsiTracker(RSI_PERIOD)

        self.pending_signal_type = None
        self.pending_signal_date = None
        self.pending_signal_low = None
        self.pending_signal_high = None

        self.in_trade = False
        self.trade_side = None
        self.entry_date = None
        self.entry_price = None
        self.stop_loss = None
        self.shares = None
        self.stop_ticket = None
        self.bars_held = 0
        self.next_earnings_date = None


# -----------------------------------------------------------------------------
# Algorithm
# -----------------------------------------------------------------------------
class SidMethodDynamicAlgorithm(QCAlgorithm):

    def initialize(self):
        self.set_start_date(2020, 1, 1)
        self.set_end_date(2026, 4, 30)
        self.set_cash(ACCOUNT_SIZE)
        self.set_benchmark("SPY")
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        # Indicators created mid-backtest auto-warm from history.
        self.settings.automatic_indicator_warm_up = True

        # Dynamic universe: top N US equities by dollar volume, daily.
        self.universe_settings.resolution = Resolution.DAILY
        self.add_universe(self._select_universe)

        # SPY regime filter + benchmark (manual subscription; persists).
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
        self.spy_rsi = self.rsi(self.spy, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
        self.spy_sma = self.sma(self.spy, SMA_PERIOD, Resolution.DAILY)
        self.spy_rsi_prev = self.spy_rsi.previous.value if self.spy_rsi.is_ready else None

        # Earnings (EODHD Upcoming Earnings) — keyed by ticker string for robust
        # matching across the dynamic universe.
        self.earnings_by_symbol = {}     # ticker string -> next report_date
        self.add_universe(EODHDUpcomingEarnings, self._on_earnings_data)

        self.symbol_state = {}
        self.trade_log = []

        # Diagnostics so we can SEE the earnings filter is live.
        self.earnings_symbols_seen = set()
        self.earnings_blocks = 0

        self.log(f"SID Dynamic initialized — universe size target: {UNIVERSE_SIZE}")

    # -------------------------------------------------------------------------
    # Universe selection — top N by dollar volume, pinning names we still hold
    # so an open trade is never force-closed by a universe drop.
    # -------------------------------------------------------------------------
    def _select_universe(self, fundamental):
        investable = [f for f in fundamental
                      if f.has_fundamental_data and f.price > 5 and f.dollar_volume > 0]
        investable.sort(key=lambda f: f.dollar_volume, reverse=True)
        selected = {f.symbol for f in investable[:UNIVERSE_SIZE]}
        held = {sym for sym, st in self.symbol_state.items() if st.in_trade}
        return list(selected | held)

    # -------------------------------------------------------------------------
    # Indicator lifecycle as names enter/leave the universe
    # -------------------------------------------------------------------------
    def on_securities_changed(self, changes):
        # Removed
        for sec in changes.removed_securities:
            symbol = sec.symbol
            state = self.symbol_state.pop(symbol, None)
            if state is None:
                continue
            if state.in_trade:
                if state.stop_ticket is not None:
                    try:
                        state.stop_ticket.cancel()
                    except Exception:
                        pass
                self.liquidate(symbol, tag="universe removal")
            for ind in (state.rsi, state.macd):
                if ind is not None:
                    try:
                        self.deregister_indicator(ind)
                    except Exception:
                        pass

        # Added
        new_symbols = []
        for sec in changes.added_securities:
            symbol = sec.symbol
            if symbol == self.spy:
                continue
            if symbol in self.symbol_state:
                continue
            if symbol.security_type != SecurityType.EQUITY:
                continue  # skip EODHD custom-data symbols
            state = SymbolState()
            state.rsi = self.rsi(symbol, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
            state.macd = self.macd(symbol, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
                                   MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            if state.rsi.is_ready:
                state.rsi_prev = state.rsi.previous.value
            if state.macd.is_ready:
                state.macd_hist_prev = state.macd.histogram.previous.value
            self.symbol_state[symbol] = state
            new_symbols.append(symbol)

        # Warm the weekly trackers from history in one batched call.
        if new_symbols:
            try:
                hist = self.history(new_symbols, timedelta(days=200), Resolution.DAILY)
            except Exception:
                hist = None
            if hist is not None and not hist.empty and "close" in hist.columns:
                for symbol in new_symbols:
                    state = self.symbol_state.get(symbol)
                    if state is None:
                        continue
                    try:
                        sdf = hist.loc[symbol]
                    except (KeyError, TypeError):
                        continue
                    for ts, row in sdf.iterrows():
                        state.weekly.on_daily_bar(ts, float(row["close"]))

    # -------------------------------------------------------------------------
    # Earnings callback — populate side-effect dict; don't expand the universe.
    # -------------------------------------------------------------------------
    def _on_earnings_data(self, earnings_data):
        for d in earnings_data:
            self.earnings_by_symbol[d.symbol.value] = d.report_date
            self.earnings_symbols_seen.add(d.symbol.value)
        return []

    def _next_earnings_date(self, symbol):
        d = self.earnings_by_symbol.get(symbol.value)
        if d is None:
            return None
        if d.date() < self.time.date():
            return None
        return d

    # -------------------------------------------------------------------------
    # OnData
    # -------------------------------------------------------------------------
    def on_data(self, data):
        for symbol, state in self.symbol_state.items():
            if symbol in data and data[symbol] is not None:
                state.weekly.on_daily_bar(data[symbol].end_time, data[symbol].close)

        spy_long_ok = self._spy_aligned("OS")
        spy_short_ok = self._spy_aligned("OB")

        for symbol, state in list(self.symbol_state.items()):
            if symbol not in data or data[symbol] is None:
                continue
            if state.rsi is None or state.macd is None:
                continue
            if not state.rsi.is_ready or not state.macd.is_ready:
                continue

            bar = data[symbol]
            if state.in_trade:
                self._check_exit(symbol, state, bar)
            else:
                self._scan_for_signal_and_entry(symbol, state, bar, spy_long_ok, spy_short_ok)

            state.rsi_prev = state.rsi.current.value
            state.macd_hist_prev = state.macd.histogram.current.value

        self._roll_spy_rsi()

    def _roll_spy_rsi(self):
        if self.spy_rsi.is_ready:
            self.spy_rsi_prev = self.spy_rsi.current.value

    # -------------------------------------------------------------------------
    # SPY regime filter — mirrors backtest._check_spy_alignment
    # -------------------------------------------------------------------------
    def _spy_aligned(self, signal_type):
        if not USE_SPY_FILTER:
            return True
        if not self.spy_rsi.is_ready or not self.spy_sma.is_ready or self.spy_rsi_prev is None:
            return True
        spy_close = self.securities[self.spy].price
        if spy_close == 0:
            return True
        sma = self.spy_sma.current.value
        rsi_now = self.spy_rsi.current.value
        rsi_prev = self.spy_rsi_prev
        if signal_type == "OS":
            return (rsi_now > rsi_prev) and (spy_close > sma)
        else:
            return (rsi_now < rsi_prev) and (spy_close < sma * 1.02)

    # -------------------------------------------------------------------------
    # Signal detection + entry search
    # -------------------------------------------------------------------------
    def _scan_for_signal_and_entry(self, symbol, state, bar, spy_long_ok, spy_short_ok):
        rsi_now = state.rsi.current.value
        rsi_prev = state.rsi_prev

        if rsi_prev is not None:
            if rsi_prev >= RSI_OVERSOLD and rsi_now < RSI_OVERSOLD:
                state.pending_signal_type = "OS"
                state.pending_signal_date = self.time
                state.pending_signal_low = bar.low
                state.pending_signal_high = bar.high
            elif rsi_prev <= RSI_OVERBOUGHT and rsi_now > RSI_OVERBOUGHT:
                state.pending_signal_type = "OB"
                state.pending_signal_date = self.time
                state.pending_signal_low = bar.low
                state.pending_signal_high = bar.high

        if state.pending_signal_type is None:
            return

        state.pending_signal_low = min(state.pending_signal_low, bar.low)
        state.pending_signal_high = max(state.pending_signal_high, bar.high)

        # "No room to run" — RSI already reverted past 50
        if state.pending_signal_type == "OS" and rsi_now >= RSI_EXIT:
            self._clear_signal(state)
            return
        if state.pending_signal_type == "OB" and rsi_now <= RSI_EXIT:
            self._clear_signal(state)
            return
        # Signal stale — opposite extreme hit
        if state.pending_signal_type == "OS" and rsi_now >= RSI_OVERBOUGHT:
            self._clear_signal(state)
            return
        if state.pending_signal_type == "OB" and rsi_now <= RSI_OVERSOLD:
            self._clear_signal(state)
            return

        # Need at least one bar after the signal
        if state.pending_signal_date == self.time:
            return

        if not self._entry_conditions_met(symbol, state, bar, rsi_now, rsi_prev,
                                          spy_long_ok, spy_short_ok):
            return

        open_positions = sum(1 for s in self.symbol_state.values() if s.in_trade)
        if open_positions >= MAX_OPEN_POSITIONS:
            return

        self._enter_trade(symbol, state, bar)

    def _entry_conditions_met(self, symbol, state, bar, rsi_now, rsi_prev,
                              spy_long_ok, spy_short_ok):
        # A. RSI direction + MACD point/cross (faithful to backtest.py)
        macd_line = state.macd.current.value
        macd_signal = state.macd.signal.current.value
        macd_hist = state.macd.histogram.current.value
        macd_hist_prev = state.macd_hist_prev
        if state.pending_signal_type == "OS":
            rsi_rising = rsi_now > rsi_prev
            macd_bullish = (macd_line > macd_signal) or (
                macd_hist_prev is not None and macd_hist > 0 and macd_hist > macd_hist_prev
            )
            if not (rsi_rising and macd_bullish):
                return False
        else:
            rsi_falling = rsi_now < rsi_prev
            macd_bearish = (macd_line < macd_signal) or (
                macd_hist_prev is not None and macd_hist < 0 and macd_hist < macd_hist_prev
            )
            if not (rsi_falling and macd_bearish):
                return False

        # B. Weekly RSI delta > 3 in trade direction
        curr_w, prev_w = state.weekly.current_and_prev_rsi()
        if curr_w is None or prev_w is None:
            return False
        weekly_delta = curr_w - prev_w
        if state.pending_signal_type == "OS" and weekly_delta <= WEEKLY_RSI_MIN_DELTA:
            return False
        if state.pending_signal_type == "OB" and weekly_delta >= -WEEKLY_RSI_MIN_DELTA:
            return False

        # C. Earnings filter — reject if next earnings is today..14 days out
        if USE_EARNINGS_FILTER:
            earnings_date = self._next_earnings_date(symbol)
            if earnings_date is not None:
                days_to = (earnings_date.date() - self.time.date()).days
                if 0 <= days_to <= EARNINGS_MIN_DAYS:
                    self.earnings_blocks += 1
                    return False
                state.next_earnings_date = earnings_date

        # E. SPY regime
        if state.pending_signal_type == "OS" and not spy_long_ok:
            return False
        if state.pending_signal_type == "OB" and not spy_short_ok:
            return False

        return True

    # -------------------------------------------------------------------------
    # Entry
    # -------------------------------------------------------------------------
    def _enter_trade(self, symbol, state, bar):
        signal_type = state.pending_signal_type
        entry_price = bar.close

        if signal_type == "OS":
            ll = state.pending_signal_low
            stop = (math.floor(ll) - 1.0) if ll == math.floor(ll) else math.floor(ll)
        else:
            hh = state.pending_signal_high
            stop = (math.ceil(hh) + 1.0) if hh == math.ceil(hh) else math.ceil(hh)

        risk_per_share = abs(entry_price - stop)
        if risk_per_share <= 0:
            self._clear_signal(state)
            return

        risk_dollars = self.portfolio.total_portfolio_value * RISK_PCT
        shares = math.floor(risk_dollars / risk_per_share)
        if shares <= 0:
            self._clear_signal(state)
            return

        side_qty = shares if signal_type == "OS" else -shares
        entry_ticket = self.market_order(symbol, side_qty, asynchronous=False,
                                         tag=f"SID entry {signal_type}")
        if entry_ticket.status not in (OrderStatus.FILLED, OrderStatus.SUBMITTED):
            self._clear_signal(state)
            return

        state.in_trade = True
        state.trade_side = "Long" if signal_type == "OS" else "Short"
        state.entry_date = self.time
        state.entry_price = entry_price
        state.stop_loss = stop
        state.shares = shares
        state.bars_held = 0

        stop_qty = -shares if signal_type == "OS" else shares
        state.stop_ticket = self.stop_market_order(symbol, stop_qty, stop, tag="SID stop")

        state.pending_signal_type = None
        state.pending_signal_date = None
        state.pending_signal_low = None
        state.pending_signal_high = None

    # -------------------------------------------------------------------------
    # Exit priority: stop (resting) -> RSI 50 -> 10 days -> earnings
    # -------------------------------------------------------------------------
    def _check_exit(self, symbol, state, bar):
        state.bars_held += 1
        rsi_now = state.rsi.current.value

        if state.trade_side == "Long" and rsi_now >= RSI_EXIT:
            self._close_trade(symbol, state, bar.close, "RSI reached 50")
            return
        if state.trade_side == "Short" and rsi_now <= RSI_EXIT:
            self._close_trade(symbol, state, bar.close, "RSI reached 50")
            return

        if state.bars_held >= MAX_TRADE_DAYS:
            self._close_trade(symbol, state, bar.close, "Time exit - 10 days")
            return

        if state.next_earnings_date is not None:
            if (state.next_earnings_date.date() - self.time.date()).days <= 1:
                self._close_trade(symbol, state, bar.close, "Earnings approaching")
                return

    def _close_trade(self, symbol, state, exit_price, reason):
        if state.stop_ticket is not None:
            try:
                state.stop_ticket.cancel()
            except Exception:
                pass
        self.liquidate(symbol, tag=f"SID exit: {reason}")
        self._record_trade(symbol, state, exit_price, reason)
        self._reset_trade_state(state)

    def _record_trade(self, symbol, state, exit_price, reason):
        if state.trade_side == "Long":
            gain = exit_price - state.entry_price
        else:
            gain = state.entry_price - exit_price
        pnl = gain * state.shares
        self.trade_log.append({
            "ticker": str(symbol.value),
            "side": state.trade_side,
            "pnl": round(pnl, 2),
            "win": pnl > 0,
            "reason": reason,
        })

    def _reset_trade_state(self, state):
        state.in_trade = False
        state.trade_side = None
        state.entry_date = None
        state.entry_price = None
        state.stop_loss = None
        state.shares = None
        state.stop_ticket = None
        state.bars_held = 0
        state.next_earnings_date = None

    def _clear_signal(self, state):
        state.pending_signal_type = None
        state.pending_signal_date = None
        state.pending_signal_low = None
        state.pending_signal_high = None

    def on_order_event(self, order_event):
        if order_event.status != OrderStatus.FILLED:
            return
        state = self.symbol_state.get(order_event.symbol)
        if state is None or not state.in_trade:
            return
        if state.stop_ticket is not None and order_event.order_id == state.stop_ticket.order_id:
            self._record_trade(order_event.symbol, state, order_event.fill_price, "Stop loss")
            self._reset_trade_state(state)

    # -------------------------------------------------------------------------
    # Summary
    # -------------------------------------------------------------------------
    def on_end_of_algorithm(self):
        if not self.trade_log:
            self.log("No trades executed.")
            return

        wins = [t for t in self.trade_log if t["win"]]
        wr = len(wins) / len(self.trade_log) * 100
        total_pnl = sum(t["pnl"] for t in self.trade_log)
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in self.trade_log if not t["win"]))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

        self.log("=" * 60)
        self.log("SID Method (dynamic universe) — final summary")
        self.log("=" * 60)
        self.log(f"Trades        : {len(self.trade_log)}")
        self.log(f"Win rate      : {wr:.1f}%")
        self.log(f"Total P&L     : ${total_pnl:,.2f}")
        self.log(f"Profit factor : {pf:.2f}")
        self.log(f"Final equity  : ${self.portfolio.total_portfolio_value:,.2f}")

        for side in ("Long", "Short"):
            st = [t for t in self.trade_log if t["side"] == side]
            if st:
                swr = sum(1 for t in st if t["win"]) / len(st) * 100
                spnl = sum(t["pnl"] for t in st)
                self.log(f"{side:<5}: {len(st)} trades, {swr:.1f}% WR, ${spnl:,.2f}")

        reasons = Counter(t["reason"] for t in self.trade_log)
        self.log("Exit reasons: " + ", ".join(f"{r}={c}" for r, c in reasons.most_common()))

        # Earnings-filter coverage — proves the 14-day rule is actually active.
        self.log(f"Earnings: {len(self.earnings_symbols_seen)} symbols had EODHD data; "
                 f"{self.earnings_blocks} entries blocked by the 14-day rule.")
