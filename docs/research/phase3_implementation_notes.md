# Phase 3 — Implementation Notes

How to run the LEAN port in QuantConnect, what the code does and does not match in the trader repo, and how to read the output.

## Files

- `quantconnect/sid_quantconnect.py` — main algorithm; this is the single-shot validation run.
- `quantconnect/sid_parameter_sweep.py` — parameter-bound variant for QC's Optimizer.

Both files are self-contained. They do **not** import from `shared/` or `strategies/sid_method/` — QC's cloud IDE can't see local Python packages, so the strategy logic is duplicated. This is intentional and documented in the file headers.

## QC web IDE setup — step by step

1. **Sign up** at https://www.quantconnect.com/ if you don't have an account. The free tier is sufficient for these backtests; an Organization Member plan would help if you want concurrent optimizer runs.

2. **Create a new Python algorithm project.** From the Algorithm Lab, click "Create New Algorithm" → Python.

3. **Replace `main.py`** with the entire contents of `quantconnect/sid_quantconnect.py`. Save.

4. **Backtest.** Click the "Backtest" button (top right). First run will take 5-15 minutes to fetch data and run; subsequent runs use QC's cache and are faster. Watch the log pane for `INFO ENTER` / `EXIT` lines as trades execute.

5. **Inspect results.** The QC results UI gives you Sharpe, Sortino, MDD, profit factor, total return, alpha, beta, etc. out of the box. The custom trade log is dumped via `self.log()` at end-of-algorithm — scroll to the bottom of the log pane.

6. **For the parameter sweep**, create a SEPARATE QC project, paste `sid_parameter_sweep.py` into its `main.py`, then:
   - Open the project's "Parameters" tab and add the 11 parameters listed at the top of the sweep file (with defaults).
   - Click "Optimize" (not Backtest). Set up the grid per the SUGGESTED_GRID block in the file header.
   - Pick "Sharpe Ratio" or "Profit Factor" as the optimization target — NOT raw Win Rate (see file header for why).
   - QC's Optimizer will run the cartesian product and present a sortable results table.

## What the port does exactly match

Each of these is bit-for-bit faithful to `strategies/sid_method/`:

- Indicator parameters: RSI(14) Wilder smoothing; MACD(12, 26, 9) with EMA smoothing; SPY SMA(50).
- Signal definition: daily RSI crossing 30 (long) or 70 (short) on bar-over-bar basis.
- Entry RSI direction check (rising for long, falling for short).
- Weekly RSI semantics: in-progress current week vs prior completed week (via custom `WeeklyRsiTracker`, since LEAN's standard `Calendar.Weekly` consolidator only emits at week-end).
- Weekly RSI delta filter: must move strictly more than +3 points in trade direction.
- "No room to run" gate: abort signal if daily RSI has already crossed 50.
- SPY two-leg regime filter: RSI direction + price-vs-SMA50, with the asymmetric "within 2% of SMA50" tolerance on shorts.
- Stop loss calc: lowest-low / highest-high between signal-bar and entry-bar, rounded to whole dollar; -1 / +1 if already whole.
- Exit priority order: stop (resting) → RSI=50 → 10-day time exit → pre-earnings close.
- 2-day RSI reversal exit: **intentionally not implemented** (matches the trader-repo's `backtest.py:238–243` comment).
- 1%-account-risk position sizing.
- 99-ticker universe (matches `config.py` exactly).

## Where the port diverges or approximates

Each of these is flagged in `QUESTIONS_FOR_SYDNEY.md`; the implementation makes a stated choice but you may want to revisit.

1. **MACD histogram increasing check** — the trader-repo `_check_entry_conditions` allows entry on `(macd > signal) OR (hist > 0 AND hist > prev_hist)`. The QC port keeps no bar-over-bar histogram window so it uses only `macd > signal`. This is **strictly stricter** than the trader code; the port will produce *fewer* entries than the trader code on the same bars where the only-difference is "macd_line still below signal but histogram building."
   - **Fix path**: add a `RollingWindow[float](2)` for `macd.histogram.current.value` updated on each daily bar and check `state.macd_hist_window[0] > state.macd_hist_window[1]` for "increasing."

2. **Earnings filter approximation** — the trader-repo uses `shared/earnings.py` which fetches per-ticker earnings dates from yfinance. QC fundamentals expose `Fundamentals.EarningReports.FileDate` (the most recent filing), so the port estimates "next earnings" as `file_date + 90 days`. For high-precision earnings filtering you'd want an external earnings calendar feed (QC has Tiingo / Estimize integrations, paid).
   - **Fix path**: subscribe to a QC earnings dataset (Tiingo Fundamentals earnings calendar), then call `self.history(EarningsEvent, symbol, ...)` to get the actual next reporting date.

3. **Weekly RSI rounding window** — `WeeklyRsiTracker` keeps the last 19 completed weekly closes (`period + 5 = 19`). Wilder's RSI mathematically requires 15+ data points to stabilize; 19 gives margin. If your QC start date is too close to a holiday-heavy stretch, the tracker may need more warmup; the algorithm's `set_warm_up(timedelta(days=250))` should be sufficient.

4. **Earnings exit timing** — trader code exits on "last trading day before earnings"; QC port exits when `days_to_earnings <= 1`. These differ when earnings fall on a Monday after a Friday holiday — trader code would exit Thursday, QC port exits Friday. Minor.

5. **`market_order` fill timing** — LEAN with `Resolution.Daily` fills market orders at the next bar's open by default. The trader backtest fills at the same-day close. So entries in the QC port happen at the *next day's open*, which is ~1 bar lagged vs the trader code. This is realistic for live trading (you can't fill at a close that hasn't happened yet) but means QC results will differ slightly from yfinance results even before fills/slippage matter.

6. **`LimitOrder` for take-profit** — the user's spec asked for bracket orders with a LimitOrder TP and StopMarketOrder SL. SID's TP is "RSI reaches 50" (a state condition, not a price target), so it cannot be expressed as a resting LimitOrder. The port uses a resting StopMarketOrder for the stop only and monitors RSI=50 / time / earnings exits in `on_data` daily. This matches the trader-repo behavior; departing from it would change the strategy semantics.

## Expected output format

### Log output during run

```
2020-01-02 00:00:00 :: SID Method initialized — universe size: 99
2020-03-12 16:00:00 :: ENTER Long ZTS @ 116.45, stop 111.00, shares 18, signal 2020-03-09, weekly_delta 4.21
2020-03-23 16:00:00 :: EXIT  Long ZTS @ 123.50 (RSI reached 50), pnl $126.90, W
...
```

### End-of-algorithm summary

```
============================================================
SID Method — final summary
============================================================
Trades       : 712
Wins / Losses: 532 / 180
Win rate     : 74.7%
Total P&L    : $142,338.10
Gross win    : $189,228.40
Gross loss   : $46,890.30
Profit factor: 4.04
Final equity : $242,338.10
Long : 478 trades, 76.2% WR, $108,221.40 P&L
Short: 234 trades, 71.8% WR, $34,116.70 P&L
Exit reasons:
  RSI reached 50: 489
  Stop loss: 158
  Time exit - 10 days: 41
  Earnings approaching: 24
```

(Numbers above are illustrative only — actual results will come from the QC run.)

### QC built-in stats panel

Shown automatically in the QC backtest results UI:

- Sharpe Ratio, Sortino Ratio
- Max Drawdown
- Beta vs SPY, Alpha
- Total Return, CAGR
- Profit Factor
- Win Rate (QC's calc — should match the custom log line within rounding)

## How to read the results

Compare against the calibration targets in `SUMMARY.md`. The TL;DR:

- WR 65-78% → port is faithful, edge survives clean data + IBKR fills. Proceed to paper trading.
- WR <60% → survivorship + fills + selection were carrying the yfinance backtest. Revisit universe.
- WR >80% → strategy still looks too good. Likely indicates a port bug (most common: entries firing on signal-bar without the required next-bar separation). Re-check `_scan_for_signal_and_entry`'s `st.signal_bar == self.time` guard.
- Trade count materially lower than 739 → the MACD-histogram-increasing approximation (item 1 above) may be too strict; implement the rolling-histogram-window fix.

## Known limitations

- **No reversal-pattern confirmation step** (head-and-shoulders, double-top/bottom). Trader-repo doesn't implement it; port matches trader-repo, not Naiman's documented method. To add it, you'd need a pattern-recognition library; QC supports `IndicatorBase` subclasses for custom patterns.
- **No tier scoring** that the live scanner uses. The port emits raw trades only; tier scoring is a post-processing decision and isn't needed for validation.
- **Equity-only.** ETFs in the universe (XLU, XLV, GDX, etc.) trade as ordinary equities in LEAN; for currency-hedged or leveraged ETFs you'd want to verify the fill simulator handles them correctly (it should, but worth eyeballing the trade log).
- **No live trading.** This is a backtest port. To live-trade in QC, you'd swap `BrokerageName.INTERACTIVE_BROKERS_BROKERAGE` for the live broker config and deploy through QC's live trading flow — but this is explicitly out of scope for the validation step.
