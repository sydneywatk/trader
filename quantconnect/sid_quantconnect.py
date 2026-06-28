# =============================================================================
# SID Method — QuantConnect / LEAN port
# =============================================================================
#
# WHAT THIS IS
#   A faithful port of the SID Method strategy implemented in this trader repo
#   at strategies/sid_method/. The yfinance backtest produces ~89% win rate on a
#   99-ticker hand-curated universe (2020–2026-04, 739 trades). This QC port
#   exists to validate that result against survivorship-bias-corrected data with
#   realistic IBKR fills and commissions.
#
# WHAT TO EXPECT
#   Naiman's documented student-trade win rate is 70-71% over ~13,200 trades.
#   The closest published daily RSI+MACD mean-reversion backtest is
#   QuantifiedStrategies at 73% / 235 trades on a single ETF. The closest
#   public LEAN daily-equity mean-reversion benchmark is "The Alpha Formula"
#   port at 63% WR / 22.8% MDD on S&P 500. A faithful SID port should land
#   somewhere in the 65-78% WR range; anything materially higher than that
#   suggests residual survivorship bias in the universe or backtest
#   assumptions, anything materially lower suggests the trader repo
#   over-fitted on yfinance data. See docs/research/SUMMARY.md.
#
# WHAT TO DO WITH IT
#   Paste this entire file into QuantConnect's web IDE (https://www.quantconnect.com/),
#   create a new Python algorithm, replace `main.py` with this content, click
#   "Backtest". Expect 5–15 minutes for the full 2020-today run on daily
#   resolution across 100 symbols (99 universe + SPY benchmark).
#   See docs/research/phase3_implementation_notes.md for setup.
#
# NOT INCLUDED
#   - Naiman's discretionary "reversal pattern" confirmation (head-and-shoulders,
#     double-top/bottom). The trader-repo code does not implement this; the
#     port matches the code, not the documented method. See QUESTIONS_FOR_SYDNEY.md.
#   - The 2-day RSI reversal exit: explicitly disabled in the trader repo
#     because it caught oscillations rather than reversals in daily-bar backtests.
#
# EARNINGS DATA
#   Uses QuantConnect's EODHDUpcomingEarnings dataset (free on QC Cloud, daily,
#   1998-present, ~97% exact-date precision vs Nasdaq). The dataset is consumed
#   via add_universe; the callback updates a side-effect dict mapping equity
#   symbol -> next report_date. See _on_earnings_data and _next_earnings_date.
#
# =============================================================================

from AlgorithmImports import *
from datetime import timedelta
from collections import deque
import math


# -----------------------------------------------------------------------------
# Universe — 99 tickers, matches strategies/sid_method/config.py exactly
# -----------------------------------------------------------------------------
# Ranked by historical WR in the yfinance backtest. FOX removed (50% test WR).
# ★ = Sid Naiman's original list per Trading Cafe.
SID_UNIVERSE = [
    "GPN", "NUGT",
    "AKAM", "CME", "CMG", "FIS", "HBAN", "MKC", "RF",
    "APO", "COST", "FANG", "MS", "WM", "XLU",
    "PAYX", "TSLA",
    "BLK", "CMCSA",
    "CDW", "ED", "GDX", "HPE", "HRL", "MRSH", "OKE", "XLV",
    "AEP", "CHTR", "CTRA", "ELF", "IP", "KEYS", "SATS", "TLT", "XLC", "ZTS",
    "ABNB", "ACGL", "AFL", "APTV", "AVB", "CNP", "CPB", "F",
    "GM", "GS", "HST", "HUT", "IT", "JBHT", "JCI", "JPM",
    "LMT", "MAR", "MTB", "NUE", "PKG", "PPG", "REG", "SO",
    "TPR", "UNP", "XLI", "XOP",
    "BIIB",
    "AMT", "CSCO", "ROST", "TKO",
    "DIS", "DXCM", "EMR", "PG",
    "ADBE", "ANET", "BALL", "BG", "BMY", "CCI", "CF", "COP",
    "CTVA", "DDOG", "EFA", "EXEL", "FCX", "GDXJ", "LUV", "LYV",
    "MAS", "MRK", "UDR", "USB", "VTR",
    "AAPL", "DGX", "DOC", "EQIX",
]


# -----------------------------------------------------------------------------
# Strategy constants — mirror config.py exactly
# -----------------------------------------------------------------------------
RSI_PERIOD = 14
MACD_FAST = 12
MACD_SLOW = 26
MACD_SIGNAL = 9
SMA_PERIOD = 50

RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
RSI_EXIT = 50               # Take-profit when RSI reaches midline
WEEKLY_RSI_MIN_DELTA = 3    # Weekly RSI must move >3 points in trade direction
MAX_TRADE_DAYS = 10         # Force exit if RSI never reaches 50
EARNINGS_MIN_DAYS = 14      # Cannot enter within N days of earnings

# Risk
ACCOUNT_SIZE = 100_000
RISK_PCT = 0.01             # 1% account risk per trade
MAX_OPEN_POSITIONS = 20     # cap concurrent positions — matters on a broad universe,
                            # where same-day signal clusters would otherwise exceed margin

# Toggles for sweep / debug
USE_SPY_FILTER = True
USE_EARNINGS_FILTER = True  # Falls back to disabled per-symbol if fundamentals missing
USE_KELLY_SIZING = False    # If True, scale position by Kelly fraction (default off)
KELLY_FRACTION = 0.5        # Fractional Kelly factor when USE_KELLY_SIZING enabled


# -----------------------------------------------------------------------------
# WeeklyRsiTracker — replicates the trader repo's "current week in-progress
# vs prior completed week" weekly RSI semantics.
#
# LEAN's standard Calendar.Weekly consolidator only emits at week end, so the
# RSI indicator registered against it never sees the in-progress week. The
# trader repo, by contrast, resamples daily→weekly and lets the current
# (partial) week act as the "current" RSI bar. To preserve that behavior we
# maintain our own rolling buffer of completed weekly closes plus the current
# week's running close, and compute Wilder's RSI on demand.
# -----------------------------------------------------------------------------
class WeeklyRsiTracker:
    def __init__(self, period=14):
        self.period = period
        self.completed_weekly_closes = deque(maxlen=period + 5)
        self.current_week_close = None
        self.current_week_id = None  # (iso_year, iso_week)

    def on_daily_bar(self, end_time, close):
        week_id = (end_time.isocalendar().year, end_time.isocalendar().week)
        if self.current_week_id is None or week_id != self.current_week_id:
            if self.current_week_close is not None:
                # Previous week finalized — commit its close
                self.completed_weekly_closes.append(self.current_week_close)
            self.current_week_id = week_id
        self.current_week_close = close

    def _wilder_rsi(self, closes):
        """Wilder's RSI on a list of closes. Returns the last RSI value or None."""
        if len(closes) < self.period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0.0 for d in deltas]
        losses = [-d if d < 0 else 0.0 for d in deltas]
        # Seed: simple average over first `period` deltas
        avg_gain = sum(gains[:self.period]) / self.period
        avg_loss = sum(losses[:self.period]) / self.period
        # Wilder smoothing for the remainder
        alpha = 1.0 / self.period
        for i in range(self.period, len(deltas)):
            avg_gain = (1 - alpha) * avg_gain + alpha * gains[i]
            avg_loss = (1 - alpha) * avg_loss + alpha * losses[i]
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))

    def current_and_prev_rsi(self):
        """Return (current_week_rsi, prev_completed_week_rsi) or (None, None) if not enough data."""
        if self.current_week_close is None:
            return None, None
        completed = list(self.completed_weekly_closes)
        # "Current" RSI uses completed_weekly + [current_week_in_progress]
        curr_series = completed + [self.current_week_close]
        # "Previous" RSI uses completed only (prior completed week is last in completed)
        prev_series = completed
        curr_rsi = self._wilder_rsi(curr_series)
        prev_rsi = self._wilder_rsi(prev_series)
        return curr_rsi, prev_rsi


# -----------------------------------------------------------------------------
# Per-symbol state container
# -----------------------------------------------------------------------------
class SymbolState:
    def __init__(self):
        self.rsi = None
        self.rsi_prev = None
        self.macd = None
        self.macd_hist_prev = None   # prior-bar MACD histogram (for "hist rising/falling")
        self.weekly = WeeklyRsiTracker(RSI_PERIOD)

        # Signal tracking — a "signal" is the day RSI first crossed 30/70.
        # The strategy then searches subsequent days for a valid entry.
        self.pending_signal_type = None     # "OS" / "OB" / None
        self.pending_signal_date = None
        self.pending_signal_low = None      # Running lowest-low since signal (for long stop)
        self.pending_signal_high = None     # Running highest-high since signal (for short stop)

        # Active trade
        self.in_trade = False
        self.trade_side = None              # "Long" / "Short"
        self.entry_date = None
        self.entry_price = None
        self.stop_loss = None
        self.shares = None
        self.stop_ticket = None             # LEAN OrderTicket for the resting stop
        self.bars_held = 0
        self.next_earnings_date = None      # Used for pre-earnings exit


# -----------------------------------------------------------------------------
# Main algorithm
# -----------------------------------------------------------------------------
class SidMethodAlgorithm(QCAlgorithm):

    def initialize(self):
        # -------- Backtest configuration --------
        self.set_start_date(2020, 1, 1)
        # End-date fixed to match the trader-repo's validated backtest window
        # exactly. Drop this line (or extend it) to run further into the future
        # as a true out-of-sample check.
        self.set_end_date(2026, 4, 30)
        self.set_cash(ACCOUNT_SIZE)
        self.set_benchmark("SPY")

        # IBKR fills + commissions — this is the whole reason for the QC port.
        self.set_brokerage_model(BrokerageName.INTERACTIVE_BROKERS_BROKERAGE, AccountType.MARGIN)

        # -------- SPY (regime filter + benchmark) --------
        self.spy = self.add_equity("SPY", Resolution.DAILY).symbol
        self.spy_rsi = self.rsi(self.spy, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
        self.spy_sma = self.sma(self.spy, SMA_PERIOD, Resolution.DAILY)
        self.spy_rsi_prev = None

        # -------- Universe --------
        self.symbol_state = {}
        for ticker in SID_UNIVERSE:
            try:
                sec = self.add_equity(ticker, Resolution.DAILY)
            except Exception as e:
                self.debug(f"Could not add {ticker}: {e}")
                continue
            symbol = sec.symbol
            state = SymbolState()
            state.rsi = self.rsi(symbol, RSI_PERIOD, MovingAverageType.WILDERS, Resolution.DAILY)
            state.macd = self.macd(symbol, MACD_FAST, MACD_SLOW, MACD_SIGNAL,
                                   MovingAverageType.EXPONENTIAL, Resolution.DAILY)
            self.symbol_state[symbol] = state

        # -------- Earnings dataset (EODHD Upcoming Earnings, free on QC Cloud) --------
        # Universe-selection–style alt-data: the callback fires daily with all
        # currently-known upcoming earnings; we use it for the side-effect of
        # populating self.earnings_by_symbol. We return only symbols already in
        # our universe so the selection never expands the equity subscription set.
        self.earnings_by_symbol = {}  # equity Symbol -> next report_date (datetime)
        self.add_universe(EODHDUpcomingEarnings, self._on_earnings_data)

        # -------- Warm-up --------
        # Need 50 trading days minimum for SMA50; weekly RSI needs ~15 weeks of
        # daily history. 250 calendar days gives ample headroom for all indicators.
        self.set_warm_up(timedelta(days=250))

        # -------- Trade log (dumped at end) --------
        self.trade_log = []

        self.log(f"SID Method initialized — universe size: {len(self.symbol_state)}")

    # -------------------------------------------------------------------------
    # OnData — runs once per daily bar
    # -------------------------------------------------------------------------
    def on_data(self, data):
        # Update SPY rsi_prev tracker (we need prev day's RSI for "RSI rising" check)
        if self.spy_rsi.is_ready:
            self.spy_rsi_prev = self.spy_rsi.current.value if self.spy_rsi_prev is None else self.spy_rsi_prev

        # Update per-symbol weekly RSI tracker from each fresh daily bar
        for symbol, state in self.symbol_state.items():
            if symbol in data and data[symbol] is not None:
                bar = data[symbol]
                state.weekly.on_daily_bar(bar.end_time, bar.close)

        if self.is_warming_up:
            # Need to keep updating spy_rsi_prev during warm-up
            self._roll_spy_rsi()
            return

        # SPY regime computed once per OnData call
        spy_long_ok = self._spy_aligned("OS")
        spy_short_ok = self._spy_aligned("OB")

        for symbol, state in self.symbol_state.items():
            if symbol not in data or data[symbol] is None:
                continue
            if not state.rsi.is_ready or not state.macd.is_ready:
                continue

            bar = data[symbol]

            if state.in_trade:
                self._check_exit(symbol, state, bar)
            else:
                self._scan_for_signal_and_entry(symbol, state, bar,
                                                 spy_long_ok, spy_short_ok)

            # Update rsi_prev / macd histogram for next bar
            state.rsi_prev = state.rsi.current.value
            state.macd_hist_prev = state.macd.histogram.current.value

        # Update SPY rsi_prev for next bar (after consumers have used current/prev)
        self._roll_spy_rsi()

    def _roll_spy_rsi(self):
        if self.spy_rsi.is_ready:
            self.spy_rsi_prev = self.spy_rsi.current.value

    # -------------------------------------------------------------------------
    # SPY regime filter — mirrors backtest.py _check_spy_alignment exactly
    # -------------------------------------------------------------------------
    def _spy_aligned(self, signal_type):
        if not USE_SPY_FILTER:
            return True
        if not self.spy_rsi.is_ready or not self.spy_sma.is_ready or self.spy_rsi_prev is None:
            return True  # No SPY data yet — fail open, same as trader repo
        spy_close = self.securities[self.spy].price
        if spy_close == 0:
            return True
        sma = self.spy_sma.current.value
        rsi_now = self.spy_rsi.current.value
        rsi_prev = self.spy_rsi_prev
        if signal_type == "OS":
            rsi_rising = rsi_now > rsi_prev
            above_sma = spy_close > sma
            return rsi_rising and above_sma
        else:  # OB
            rsi_falling = rsi_now < rsi_prev
            near_or_below_sma = spy_close < sma * 1.02
            return rsi_falling and near_or_below_sma

    # -------------------------------------------------------------------------
    # Signal detection + entry search
    # -------------------------------------------------------------------------
    def _scan_for_signal_and_entry(self, symbol, state, bar, spy_long_ok, spy_short_ok):
        rsi_now = state.rsi.current.value
        rsi_prev = state.rsi_prev

        # ----- 1. Detect new signals (RSI crossing 30 / 70) -----
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

        # ----- 2. If there's a pending signal, check whether today is a valid entry -----
        if state.pending_signal_type is None:
            return

        # Update running lowest-low / highest-high since signal (for stop calc)
        state.pending_signal_low = min(state.pending_signal_low, bar.low)
        state.pending_signal_high = max(state.pending_signal_high, bar.high)

        # Abort if RSI has already reverted past 50 ("no room to run" — trader repo's gap-skip)
        if state.pending_signal_type == "OS" and rsi_now >= RSI_EXIT:
            self._clear_signal(state, "Skipped — gap to RSI 50")
            return
        if state.pending_signal_type == "OB" and rsi_now <= RSI_EXIT:
            self._clear_signal(state, "Skipped — gap to RSI 50")
            return

        # Abort if an opposite RSI extreme has been hit (signal stale)
        if state.pending_signal_type == "OS" and rsi_now >= RSI_OVERBOUGHT:
            self._clear_signal(state, "Skipped — RSI rotated to overbought")
            return
        if state.pending_signal_type == "OB" and rsi_now <= RSI_OVERSOLD:
            self._clear_signal(state, "Skipped — RSI rotated to oversold")
            return

        # Don't try to enter on the signal bar itself; need at least one subsequent bar
        if state.pending_signal_date == self.time:
            return

        # Check the multi-condition gate (A–E from backtest.py)
        if not self._entry_conditions_met(symbol, state, bar, rsi_now, rsi_prev,
                                           spy_long_ok, spy_short_ok):
            return

        # Respect the concurrent-position cap (broad-universe margin safety).
        open_positions = sum(1 for s in self.symbol_state.values() if s.in_trade)
        if open_positions >= MAX_OPEN_POSITIONS:
            return

        # All conditions met → enter
        self._enter_trade(symbol, state, bar)

    def _entry_conditions_met(self, symbol, state, bar, rsi_now, rsi_prev,
                              spy_long_ok, spy_short_ok):
        # --- A. RSI direction + MACD alignment (checklist item 2: "MACD point/cross") ---
        # Faithful to backtest.py _check_entry_conditions:
        #   bullish = (MACD line > signal)  OR  (hist > 0 AND hist rising)   [cross OR point]
        #   bearish = (MACD line < signal)  OR  (hist < 0 AND hist falling)
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

        # --- B. Weekly RSI delta ---
        curr_w, prev_w = state.weekly.current_and_prev_rsi()
        if curr_w is None or prev_w is None:
            return False
        weekly_delta = curr_w - prev_w
        if state.pending_signal_type == "OS" and weekly_delta <= WEEKLY_RSI_MIN_DELTA:
            return False
        if state.pending_signal_type == "OB" and weekly_delta >= -WEEKLY_RSI_MIN_DELTA:
            return False

        # --- C. Earnings filter ---
        # Trader-repo rule (shared/earnings.py earnings_safe): days_away > 14 → safe;
        # so reject when 0 <= days_away <= 14 (i.e. earnings is today or within 14 days).
        if USE_EARNINGS_FILTER:
            earnings_date = self._next_earnings_date(symbol)
            if earnings_date is not None:
                days_to_earnings = (earnings_date.date() - self.time.date()).days
                if 0 <= days_to_earnings <= EARNINGS_MIN_DAYS:
                    return False
                state.next_earnings_date = earnings_date

        # --- E. SPY regime ---
        if state.pending_signal_type == "OS" and not spy_long_ok:
            return False
        if state.pending_signal_type == "OB" and not spy_short_ok:
            return False

        return True

    # -------------------------------------------------------------------------
    # EODHD universe-selection callback — populates self.earnings_by_symbol as
    # a side effect, then returns only symbols already in our equity universe so
    # the selection itself is a no-op on subscriptions.
    # -------------------------------------------------------------------------
    def _on_earnings_data(self, earnings_data):
        for d in earnings_data:
            self.earnings_by_symbol[d.symbol] = d.report_date
        return [d.symbol for d in earnings_data if d.symbol in self.symbol_state]

    # -------------------------------------------------------------------------
    # Next-earnings lookup using the EODHD data the callback populates.
    # Returns the next future report_date for the symbol, or None.
    # -------------------------------------------------------------------------
    def _next_earnings_date(self, symbol):
        d = self.earnings_by_symbol.get(symbol)
        if d is None:
            return None
        # Ignore stale entries (report already passed)
        if d.date() < self.time.date():
            return None
        return d

    # -------------------------------------------------------------------------
    # Entry execution
    # -------------------------------------------------------------------------
    def _enter_trade(self, symbol, state, bar):
        signal_type = state.pending_signal_type
        entry_price = bar.close

        # Stop loss: lowest low (long) / highest high (short) between signal and entry,
        # rounded to whole dollar (down for long, up for short), with -1/+1 nudge if
        # already whole. Matches backtest._calc_stop_loss exactly.
        if signal_type == "OS":
            ll = state.pending_signal_low
            if ll == math.floor(ll):
                stop = math.floor(ll) - 1.0
            else:
                stop = math.floor(ll)
        else:
            hh = state.pending_signal_high
            if hh == math.ceil(hh):
                stop = math.ceil(hh) + 1.0
            else:
                stop = math.ceil(hh)

        risk_per_share = abs(entry_price - stop)
        if risk_per_share <= 0:
            self._clear_signal(state, "Invalid stop (risk_per_share <= 0)")
            return

        equity = self.portfolio.total_portfolio_value
        risk_dollars = equity * RISK_PCT
        if USE_KELLY_SIZING:
            # Naive fractional-Kelly: WR 0.71, R≈1.0 → f* ≈ 2p - 1 = 0.42; scale by KELLY_FRACTION.
            # Refine after first backtest with empirical edge/odds.
            risk_dollars *= (2 * 0.71 - 1) * KELLY_FRACTION * (1.0 / RISK_PCT)
        shares = math.floor(risk_dollars / risk_per_share)
        if shares <= 0:
            self._clear_signal(state, "Position size rounds to zero")
            return

        # Place entry as market order, then a resting stop after fill.
        # SID is daily-resolution so market-on-close is appropriate; LEAN treats a
        # market order on daily data as fill at the next bar's open by default.
        side_qty = shares if signal_type == "OS" else -shares
        entry_ticket = self.market_order(symbol, side_qty, asynchronous=False, tag=f"SID entry {signal_type}")

        if entry_ticket.status != OrderStatus.FILLED and entry_ticket.status != OrderStatus.SUBMITTED:
            self._clear_signal(state, f"Entry not filled (status={entry_ticket.status})")
            return

        state.in_trade = True
        state.trade_side = "Long" if signal_type == "OS" else "Short"
        state.entry_date = self.time
        state.entry_price = entry_price
        state.stop_loss = stop
        state.shares = shares
        state.bars_held = 0

        # Resting stop. For a long position: stop sells at `stop`; for short: stop buys at `stop`.
        stop_qty = -shares if signal_type == "OS" else shares
        state.stop_ticket = self.stop_market_order(symbol, stop_qty, stop, tag="SID stop")

        self.log(
            f"ENTER {state.trade_side} {symbol.value} @ {entry_price:.2f}, "
            f"stop {stop:.2f}, shares {shares}, signal {state.pending_signal_date.date()}, "
            f"weekly_delta {state.weekly.current_and_prev_rsi()[0] - state.weekly.current_and_prev_rsi()[1]:.2f}"
        )

        # Clear pending signal (we've consumed it)
        state.pending_signal_type = None
        state.pending_signal_date = None
        state.pending_signal_low = None
        state.pending_signal_high = None

    # -------------------------------------------------------------------------
    # Exit logic — priority order matches backtest._find_exit exactly:
    #   1. Stop loss (handled by the resting stop_market_order; OnOrderEvent records)
    #   2. RSI reaches 50
    #   3. Time exit (10 trading days)
    #   4. Earnings approaching (close on last trading day before earnings)
    # -------------------------------------------------------------------------
    def _check_exit(self, symbol, state, bar):
        state.bars_held += 1
        rsi_now = state.rsi.current.value

        # 1. Stop loss is automatic via state.stop_ticket — nothing to do here.
        #    If the stop fires intraday, OnOrderEvent fires below.

        # 2. RSI hits 50
        if state.trade_side == "Long" and rsi_now >= RSI_EXIT:
            self._close_trade(symbol, state, bar.close, "RSI reached 50")
            return
        if state.trade_side == "Short" and rsi_now <= RSI_EXIT:
            self._close_trade(symbol, state, bar.close, "RSI reached 50")
            return

        # 3. Time exit
        if state.bars_held >= MAX_TRADE_DAYS:
            self._close_trade(symbol, state, bar.close, "Time exit - 10 days")
            return

        # 4. Earnings approaching
        if state.next_earnings_date is not None:
            days_to_earnings = (state.next_earnings_date.date() - self.time.date()).days
            if days_to_earnings <= 1:
                self._close_trade(symbol, state, bar.close, "Earnings approaching")
                return

    def _close_trade(self, symbol, state, exit_price, reason):
        # Cancel resting stop, then liquidate.
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
            gain_per_share = exit_price - state.entry_price
        else:
            gain_per_share = state.entry_price - exit_price
        total_pnl = gain_per_share * state.shares
        win = total_pnl > 0
        self.trade_log.append({
            "ticker": str(symbol.value),
            "side": state.trade_side,
            "entry_date": state.entry_date.date().isoformat(),
            "exit_date": self.time.date().isoformat(),
            "entry_price": round(state.entry_price, 2),
            "exit_price": round(exit_price, 2),
            "stop_loss": round(state.stop_loss, 2),
            "shares": state.shares,
            "pnl": round(total_pnl, 2),
            "win": win,
            "reason": reason,
            "bars_held": state.bars_held,
        })
        self.log(
            f"EXIT  {state.trade_side} {symbol.value} @ {exit_price:.2f} "
            f"({reason}), pnl ${total_pnl:.2f}, {'W' if win else 'L'}"
        )

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

    def _clear_signal(self, state, reason):
        # Reset signal-tracking fields without affecting trade state
        state.pending_signal_type = None
        state.pending_signal_date = None
        state.pending_signal_low = None
        state.pending_signal_high = None

    # -------------------------------------------------------------------------
    # OnOrderEvent — captures stop-loss fills (since those happen outside on_data
    # via the resting stop ticket)
    # -------------------------------------------------------------------------
    def on_order_event(self, order_event):
        if order_event.status != OrderStatus.FILLED:
            return
        symbol = order_event.symbol
        state = self.symbol_state.get(symbol)
        if state is None or not state.in_trade:
            return
        # If the filled order matches our resting stop, the stop fired
        if state.stop_ticket is not None and order_event.order_id == state.stop_ticket.order_id:
            self._record_trade(symbol, state, order_event.fill_price, "Stop loss")
            self._reset_trade_state(state)

    # -------------------------------------------------------------------------
    # End of algorithm — dump trade log + summary
    # -------------------------------------------------------------------------
    def on_end_of_algorithm(self):
        if not self.trade_log:
            self.log("No trades executed.")
            return

        wins = [t for t in self.trade_log if t["win"]]
        losses = [t for t in self.trade_log if not t["win"]]
        wr = len(wins) / len(self.trade_log) * 100
        total_pnl = sum(t["pnl"] for t in self.trade_log)
        gross_win = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else float("inf")

        self.log("=" * 60)
        self.log(f"SID Method — final summary")
        self.log("=" * 60)
        self.log(f"Trades       : {len(self.trade_log)}")
        self.log(f"Wins / Losses: {len(wins)} / {len(losses)}")
        self.log(f"Win rate     : {wr:.1f}%")
        self.log(f"Total P&L    : ${total_pnl:,.2f}")
        self.log(f"Gross win    : ${gross_win:,.2f}")
        self.log(f"Gross loss   : ${gross_loss:,.2f}")
        self.log(f"Profit factor: {profit_factor:.2f}")
        self.log(f"Final equity : ${self.portfolio.total_portfolio_value:,.2f}")

        # Per-side breakdown
        for side in ("Long", "Short"):
            side_trades = [t for t in self.trade_log if t["side"] == side]
            if not side_trades:
                continue
            side_wins = sum(1 for t in side_trades if t["win"])
            side_wr = side_wins / len(side_trades) * 100
            side_pnl = sum(t["pnl"] for t in side_trades)
            self.log(f"{side:<5}: {len(side_trades)} trades, {side_wr:.1f}% WR, ${side_pnl:,.2f} P&L")

        # Per-exit-reason breakdown
        from collections import Counter
        reason_counts = Counter(t["reason"] for t in self.trade_log)
        self.log("Exit reasons:")
        for reason, count in reason_counts.most_common():
            self.log(f"  {reason}: {count}")
