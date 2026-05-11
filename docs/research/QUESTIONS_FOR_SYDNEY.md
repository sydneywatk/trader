# Questions for Sydney — SID QuantConnect Port

These came up during the port. Most are flagged-and-shipped (i.e. the port made a choice; you may want to revisit) rather than hard blockers. Marked **[BLOCKING]** if I need an answer before the QC results are interpretable.

## Resolution status (2026-05-11)

| # | Topic | Decision | Code change? |
|---|---|---|---|
| 1 | Reversal patterns | Leave out. Trader-repo is source of truth. | No |
| 2 | MACD histogram fallback | Ship strict; revisit if trade count low | No |
| 3 | Earnings filter | **Use free EODHDUpcomingEarnings dataset** | **Yes** |
| 4 | Weekly RSI tracker | Skip unit test; trust backtest | No |
| 5 | snake_case vs PascalCase | Acknowledged | No |
| 6 | Date range | **Fixed end-date 2026-04-30** | **Yes** |
| 7 | Kelly sizing | Existing comment is fine | No |
| 8 | Universe size | Ship 99-ticker; expand as follow-up | No |
| 9 | Slippage / commission | Default IBKR model | No |

Per-question detail below preserved for posterity.

---

## 1. Reversal-pattern confirmation step (Step 3 in your prompt)

Your prompt says: *"Step 3 (optional but recommended): reversal pattern confirmation (head-and-shoulders, double top/bottom)."*

The trader-repo code (`signals.py`, `backtest.py`) does **not** implement any reversal-pattern check. Step 3 in the code is the gap / RSI-50 / weekly-RSI / SPY filter stack, not pattern recognition.

The port matches the **code**, not the documented method.

**Question:** Was the reversal-pattern step intentionally dropped, or is it a missing implementation? If the latter, do you want me to (a) leave it for a follow-up, (b) add a simple double-top/bottom detector to the port, or (c) hold the QC port until pattern detection is in the trader code first?

**My recommendation:** (a) leave it. Pattern detection is hard to do well from price alone and would add a new error source while we're trying to validate the *existing* logic. After the QC port establishes a baseline, adding patterns is a clean +ablation.

**Resolution:** Approved (a). No code change.

---

## 2. MACD histogram-increasing fallback

The trader-repo condition for "MACD bullish" (long entry) is:

```python
macd_bullish = (macd_line > macd_signal) or (
    not pd.isna(macd_hist) and not pd.isna(prev_macd_hist)
    and macd_hist > 0 and macd_hist > prev_macd_hist
)
```

I.e., enter even when MACD line is still below signal, as long as the histogram is positive and growing (momentum building).

The QC port keeps no bar-over-bar histogram window, so it uses only `macd_line > macd_signal`. **This is strictly stricter** — fewer entries on the same bars.

**Question:** is the histogram-OR-clause a meaningful contributor to the trader-repo's 89% WR, or is it a "just in case" clause that rarely fires? I can add a `RollingWindow[float](2)` to track `macd.histogram.current.value` between bars and restore the exact OR-clause; it's ~10 lines. Worth doing if the QC trade count comes in materially below 739.

**My recommendation:** ship the strict version for the first run, then add the histogram fallback as a follow-up if trade count is low.

**Resolution:** Approved. Strict version shipped. Add fallback if QC trade count comes in materially below 739.

---

## 3. Earnings filter — `next_earnings_date` approximation

The trader code uses `shared/earnings.py` which fetches per-ticker earnings dates from yfinance. QC's `Fundamentals.EarningReports.FileDate` returns the most recent *past* filing, so the port approximates next earnings as `file_date + 90 days`. This is wrong for off-cycle reporters and miss-by-a-few-days for normal quarterly reporters.

**Question:** how strict do you want the earnings filter in QC? Three options:
- (a) Keep the +90-day approximation. Acceptable if earnings precision isn't load-bearing for the WR.
- (b) Subscribe to a QC Tiingo Fundamentals data feed (paid) and use the actual next earnings date.
- (c) Ship the trader-repo earnings cache (`cache/*_earnings.json`) into QC as an `add_data` custom dataset.

**My recommendation:** (a) for the first run. If the QC WR comes in low, check the trade log to see how many trades happen in the 14-day pre-earnings window (which the trader code would have filtered); if it's >5% of trades, escalate to (c).

**Resolution:** Recommendation REJECTED. Use QuantConnect's free `EODHDUpcomingEarnings` dataset instead (1998+, daily, 97% exact-date precision per the QC data catalog). Match `shared/earnings.py`'s window exactly.

**Implemented changes:**
- Added `self.add_universe(EODHDUpcomingEarnings, self._on_earnings_data)` to the main algorithm and the sweep harness.
- The callback populates a side-effect dict `self.earnings_by_symbol[equity_symbol] = report_date`, and returns only already-subscribed symbols so the universe selection is a no-op on subscriptions.
- `_next_earnings_date(symbol)` now reads from the dict, ignoring stale entries (`report_date < self.time.date()`).
- **Off-by-one fix discovered while implementing this:** the trader-repo rule (`shared/earnings.py:91-94`) is `days_away > 14 → safe`, meaning reject when `0 <= days_away <= 14`. The original port used `< EARNINGS_MIN_DAYS`, which would have failed to reject day-14 entries. Corrected in both files.

---

## 4. Weekly RSI semantics in LEAN

The trader code uses `pd.resample('W').agg({Close: 'last'})` on daily bars. yfinance's behavior on partial weeks gives you an in-progress current-week bar whose "Close" is the latest available daily close. So `_get_weekly_rsi_on_date` returns (current_week_in_progress_rsi, last_completed_week_rsi).

LEAN's standard `Calendar.Weekly` consolidator emits weekly bars only at week-end. Using that, the RSI indicator never sees the in-progress current week.

The port works around this with a custom `WeeklyRsiTracker` class that maintains a deque of completed weekly closes plus the current week's running close. On each daily bar, it updates the running close; on each new ISO-week boundary, it commits the prior week's close to the deque.

**Question:** I'm fairly confident this matches the trader code's behavior, but it's a 30-line class with edge cases. Want me to write a quick unit test (against a small synthetic time series) to verify the QC tracker gives identical weekly RSI values to the trader code? It's ~15 min of work but pinpoints the divergence if WR drifts.

**My recommendation:** Run the QC backtest first. If trade count is within ~10% of the trader-repo's 739 trades, the tracker is fine. If it's off by a lot, write the test.

**Resolution:** Approved. No code change.

---

## 5. Snake_case vs PascalCase LEAN API

I used the modern lowercase API (`self.set_start_date`, `self.add_equity`, `self.rsi`, `self.set_brokerage_model`, etc.). QC has been migrating to snake_case for Python in the last year. The legacy PascalCase methods (`SetStartDate`, `AddEquity`, etc.) still work but show deprecation warnings in newer LEAN versions.

**Not a question, just a heads-up:** if you find the QC IDE shows deprecation warnings or errors, swap to PascalCase. Both files use the lowercase style consistently — a global find-and-replace can flip it.

**Resolution:** Acknowledged. No code change.

---

## 6. **[BLOCKING]** Backtest date range — match the trader-repo or run a longer history?

Your prompt says: *"Date range: match whatever the current backtester uses, so results are directly comparable."*

Trader repo uses `START_DATE = "2020-01-01"`, `END_DATE = "today"`. The port matches this — start 2020-01-01, no end-date (runs to today).

**Question:** are you comfortable running a 6+ year backtest including a live-still-running 2026 partial year? Or would you prefer a fixed end-date of e.g. 2026-04-30 to match the validated trader-repo result?

**My recommendation:** fixed end-date 2026-04-30 for the apples-to-apples comparison. Then a separate run with no end-date to capture May-onward as a true out-of-sample window.

**Resolution:** Approved. `self.set_end_date(2026, 4, 30)` added to both `sid_quantconnect.py` and `sid_parameter_sweep.py`.

---

## 7. Kelly sizing — calibration

The port has `USE_KELLY_SIZING = False` by default. If enabled, it sizes positions using fractional Kelly with `WR=0.71, R≈1.0` → `f* = 2p - 1 = 0.42`, scaled by `KELLY_FRACTION = 0.5` (half-Kelly).

**Question:** the 0.71 WR is Naiman's documented student-trade number, not your own calibration. After the first QC backtest, the *measured* WR + average R should be plugged in before enabling Kelly. Want me to add a comment to that effect in code, or are you fine treating this as "obviously needs calibration before flipping"?

**My recommendation:** the comment is already in code (line ~415 of `sid_quantconnect.py`). No action needed unless you want it louder.

**Resolution:** Approved. No code change.

---

## 8. Universe size — extension question

You mentioned in your prompt: *"Find the actual list and treat it as canonical for now."*

The port uses the 99-ticker `WATCHLIST` from `strategies/sid_method/config.py` exactly. But the trader-repo `universe.py` builds a much larger scan universe (S&P 500 + ETFs + Sid's list, ~500 tickers) and the WATCHLIST is the top-WR subset of that.

**Question:** for the QC validation run, the 99-ticker universe is right (apples-to-apples). For a follow-up run, do you want me to add a `sid_quantconnect_full_universe.py` that uses the broader S&P 500 list to test whether the edge holds outside the curated set? This is essentially the "Open question 1" in `docs/research/sid_method/BACKTEST_RESULTS.md`.

**My recommendation:** ship the 99-ticker version first. If WR comes in healthy, the S&P 500 extension is a high-value follow-up; if WR is low, the universe expansion isn't worth doing until selection bias is addressed.

**Resolution:** Approved. No code change.

---

## 9. Slippage / commission tuning

The port uses `BrokerageModel.INTERACTIVE_BROKERS_BROKERAGE`. LEAN's IBKR model includes:
- Commission: $0.005/share, $1.00 minimum, capped at 1% of trade value
- Spread modeling: yes, but conservative (treats fills at midpoint with some slippage)

**No question — just verifying you're OK with QC's default IBKR model.** It's slightly more pessimistic than real IBKR fills (real IBKR Pro is usually $0.0035/share with smaller minimums), so the backtest is mildly conservative on PnL. Good for the validation goal; suboptimal if you later use the QC results for return projection.

**Resolution:** Approved. No code change.

---

## Summary

All 9 items resolved (2026-05-11). Items #3 (earnings dataset) and #6 (date range) drove real code changes in both QC files; the other 7 are confirmed flag-and-ship choices. The port is now ready to paste into QC.
