"""SID Method Live Daily Scanner — with IBKR live data, tiers, email alerts.

Run order in production (via cron at 4:30 PM ET Mon–Fri):
    python3 daily_scanner.py          # scans, emails, writes queue
    python3 ibkr_paper.py             # reads queue, auto-exec Tier 2

Data source:
    - If TWS/IB Gateway is listening on port 7497 → IBKR live price used
      to update today's RSI / MACD with the latest intraday tick
    - Otherwise → yfinance EOD data only

Env (loaded from .env via python-dotenv):
    EMAIL_USER, EMAIL_PASSWORD   (for Gmail SMTP app password)
    IBKR_HOST, IBKR_PORT, IBKR_CLIENT_ID
"""
from __future__ import annotations

import sys, os, json, math, smtplib, ssl
from datetime import datetime
from email.mime.text import MIMEText
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from config import (
    WATCHLIST, ACCOUNT_SIZE, RISK_PCT, RSI_EXIT, MAX_TRADE_DAYS,
    WEEKLY_RSI_MIN_DELTA, EARNINGS_MIN_DAYS, OUTPUT_DIR,
)
from data import fetch_daily, fetch_weekly
from indicators import add_daily_indicators, add_weekly_rsi, _rsi, _macd
from signals import find_rsi_signals
from earnings import fetch_earnings_dates, next_earnings_date
from backtest import (
    run_backtest_for_ticker, _check_entry_conditions, _calc_stop_loss,
)
import ibkr_data


LOOKBACK_DAYS = 10
QUEUE_PATH = os.path.join(OUTPUT_DIR, "ibkr_signal_queue.json")
OPEN_TRADES_PATH = os.path.join(OUTPUT_DIR, "ibkr_open_trades.json")


# ── Tier classification ──────────────────────────────────────────────────────
def classify_tier(signal_rsi: float, weekly_delta: float, macd_crossed: bool,
                  earnings_days_away: int | None, direction: str) -> int:
    """Tier 1 = best-of-best (options-quality, manual entry).
    Tier 2 = solid actionable (stock, auto-execute).

    Tier 1 criteria (all required):
      - RSI extreme: ≤22 OS or ≥78 OB
      - |weekly RSI delta| ≥ 5
      - MACD crossed today
      - Earnings ≥21 days away (or no earnings data)
    """
    rsi_extreme = (direction == "Long" and signal_rsi <= 22) or \
                  (direction == "Short" and signal_rsi >= 78)
    wk_strong = abs(weekly_delta) >= 5.0
    earn_far = earnings_days_away is None or earnings_days_away >= 21
    if rsi_extreme and wk_strong and macd_crossed and earn_far:
        return 1
    return 2


# ── IBKR live data augmentation ──────────────────────────────────────────────
def augment_with_live_price(daily_df: pd.DataFrame, ib_session, symbol: str) -> pd.DataFrame:
    """Replace today's close with IBKR live last price, recompute indicators.
    Returns the augmented DataFrame; falls back to input on failure."""
    try:
        price = ib_session.fetch_last_price(symbol, timeout=2.0)
        if price is None or price <= 0:
            return daily_df
        df = daily_df.copy()
        today = pd.Timestamp(datetime.now().date())
        # If today's bar exists, update close; else append a partial bar
        if not df.empty and df.index[-1].date() == today.date():
            df.loc[df.index[-1], "Close"] = price
            df.loc[df.index[-1], "High"] = max(df.iloc[-1]["High"], price)
            df.loc[df.index[-1], "Low"] = min(df.iloc[-1]["Low"], price)
        else:
            last = df.iloc[-1]
            df.loc[today] = {"Open": price, "High": price, "Low": price,
                             "Close": price, "Volume": 0}
        # Recompute indicators on Close
        df["RSI"] = _rsi(df["Close"], 14)
        m = _macd(df["Close"], 12, 26, 9)
        df["MACD"] = m["MACD"]; df["MACD_hist"] = m["MACD_hist"]
        df["MACD_signal"] = m["MACD_signal"]
        df["SMA50"] = df["Close"].rolling(50).mean()
        return df
    except Exception:
        return daily_df


# ── Core scan ────────────────────────────────────────────────────────────────
def scan_ticker(ticker, spy, ib_session):
    out = {"ticker": ticker, "aligned": None, "watching": [], "open": None,
           "error": None}
    try:
        d = fetch_daily(ticker); w = fetch_weekly(ticker)
        if d.empty or w.empty:
            out["error"] = "no data"; return out
        d = add_daily_indicators(d); w = add_weekly_rsi(w)

        if ib_session is not None:
            d = augment_with_live_price(d, ib_session, ticker)

        sigs = find_rsi_signals(d)
        earn = fetch_earnings_dates(ticker)
        last_idx = len(d) - 1
        last_date = d.index[last_idx]; last_row = d.iloc[last_idx]

        trades, _ = run_backtest_for_ticker(ticker, d, w, sigs, earn, spy)
        if trades:
            lt = trades[-1]
            if lt["exit_date"] == last_date and "End of data" in lt.get("notes", ""):
                out["open"] = {
                    "order": lt["order"], "entry_date": lt["entry_date"],
                    "entry_price": lt["entry_price"], "stop_loss": lt["stop_loss"],
                    "current_price": round(last_row["Close"], 2),
                    "days_in": (last_date - lt["entry_date"]).days,
                    "current_rsi": round(last_row["RSI"], 1) if pd.notna(last_row["RSI"]) else None,
                }

        cutoff = d.index[max(0, last_idx - LOOKBACK_DAYS)]
        windows = [(t["signal_date"], t["exit_date"]) for t in trades]
        pending = [s for s in sigs if s["date"] >= cutoff
                   and not any(sd <= s["date"] <= ed for sd, ed in windows)]

        for sig in pending:
            try: sig_idx = d.index.get_loc(sig["date"])
            except KeyError: continue
            rsi_t = last_row["RSI"]
            if sig["type"] == "OS" and pd.notna(rsi_t) and rsi_t >= RSI_EXIT: continue
            if sig["type"] == "OB" and pd.notna(rsi_t) and rsi_t <= RSI_EXIT: continue

            conds, _, nxt_earn, spy_blocked = _check_entry_conditions(
                d, w, last_idx, sig["type"], earn, spy
            )
            if conds:
                stop = _calc_stop_loss(d, sig_idx, last_idx, sig["type"])
                entry = last_row["Close"]
                risk_ps = abs(entry - stop)
                if risk_ps <= 0: continue
                risk_pos = ACCOUNT_SIZE * RISK_PCT
                shares = math.floor(risk_pos / risk_ps)
                if shares <= 0: continue

                # Tier inputs
                direction = "Long" if sig["type"] == "OS" else "Short"
                wmask = w.index <= last_date
                wrsi = w.loc[wmask, "RSI"].iloc[-1]
                wrsi_p = w.loc[wmask, "RSI"].iloc[-2]
                w_delta = float(wrsi - wrsi_p)
                # MACD cross detection
                prev = d.iloc[last_idx - 1]
                if sig["type"] == "OS":
                    macd_crossed = (prev["MACD"] <= prev["MACD_signal"]
                                    and last_row["MACD"] > last_row["MACD_signal"])
                else:
                    macd_crossed = (prev["MACD"] >= prev["MACD_signal"]
                                    and last_row["MACD"] < last_row["MACD_signal"])
                earn_days = (nxt_earn.date() - last_date.date()).days if nxt_earn else None
                tier = classify_tier(sig["rsi"], w_delta, bool(macd_crossed),
                                     earn_days, direction)

                out["aligned"] = {
                    "ticker": ticker, "tier": tier, "order": direction,
                    "signal_date": sig["date"], "signal_rsi": round(sig["rsi"], 1),
                    "entry_price": round(entry, 2), "stop_loss": round(stop, 2),
                    "risk_per_share": round(risk_ps, 2),
                    "shares": shares, "position_size": round(shares * entry, 2),
                    "current_rsi": round(rsi_t, 1) if pd.notna(rsi_t) else None,
                    "weekly_rsi_delta": round(w_delta, 2),
                    "macd_crossed": bool(macd_crossed),
                    "next_earnings": nxt_earn.strftime("%Y-%m-%d") if nxt_earn else None,
                }
                break
            else:
                out["watching"].append({
                    "order": "Long" if sig["type"] == "OS" else "Short",
                    "signal_date": sig["date"], "signal_rsi": round(sig["rsi"], 1),
                    "current_rsi": round(rsi_t, 1) if pd.notna(rsi_t) else None,
                    "spy_blocked": spy_blocked,
                })
    except Exception as e:
        out["error"] = str(e)
    return out


# ── Email ────────────────────────────────────────────────────────────────────
def build_email_body(spy_regime, aligned, open_trades, watching_count) -> str:
    lines = [f"SPY regime: {spy_regime}", ""]
    if aligned:
        lines.append(f"★ ACTIONABLE SETUPS — {len(aligned)}")
        lines.append("-" * 60)
        for tk, a in aligned:
            lines.append(f"\n{tk}  TIER {a['tier']}  {a['order'].upper()}")
            lines.append(f"  Signal: {a['signal_date'].strftime('%Y-%m-%d')} "
                         f"RSI {a['signal_rsi']} (now {a['current_rsi']})")
            lines.append(f"  Entry: ${a['entry_price']}  Stop: ${a['stop_loss']}  "
                         f"Risk/sh: ${a['risk_per_share']}")
            lines.append(f"  Size:  {a['shares']} shares  "
                         f"(${a['position_size']:,.0f} position)")
            lines.append(f"  Earnings: {a['next_earnings'] or 'N/A'}  "
                         f"Weekly RSI Δ: {a['weekly_rsi_delta']:+.1f}  "
                         f"MACD cross: {a['macd_crossed']}")
            if a["tier"] == 1:
                lines.append("  → TIER 1: options-quality — MANUAL ENTRY")
            else:
                lines.append("  → TIER 2: auto-execute via ibkr_paper.py")
    if open_trades:
        lines.append(f"\nOPEN POSITIONS — {len(open_trades)}")
        lines.append("-" * 60)
        for tk, o in open_trades:
            warn = "  ⚠ time-exit threshold" if o["days_in"] >= MAX_TRADE_DAYS else ""
            lines.append(f"{tk} {o['order']} entered "
                         f"{o['entry_date'].strftime('%Y-%m-%d')} @ ${o['entry_price']}  "
                         f"now ${o['current_price']}  RSI {o['current_rsi']}  "
                         f"({o['days_in']}d){warn}")
    lines.append(f"\nWatching list: {watching_count} pending signals")
    lines.append("\nRun daily_scanner.py for full details.")
    return "\n".join(lines)


def send_email(subject: str, body: str) -> bool:
    user = os.environ.get("EMAIL_USER")
    pwd = os.environ.get("EMAIL_PASSWORD")
    if not user or not pwd or pwd.startswith("your_") or pwd == "<app_password_placeholder>":
        print("  [email] EMAIL_USER/EMAIL_PASSWORD not set in .env — skipping send.")
        return False
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = "sydneywatk@gmail.com"
    try:
        with smtplib.SMTP("smtp.gmail.com", 587) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)
        print("  [email] sent to sydneywatk@gmail.com")
        return True
    except Exception as e:
        print(f"  [email] FAILED: {e}")
        return False


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print("=" * 70)
    print("  SID METHOD — DAILY LIVE SCANNER")
    print(f"  Run: {datetime.now().strftime('%Y-%m-%d %H:%M')}   "
          f"Watchlist: {len(WATCHLIST)} tickers")

    # Data source
    ib_up = ibkr_data.probe()
    data_source = "IBKR LIVE" if ib_up else "yfinance (EOD)"
    print(f"  Data source: {data_source}")
    print("=" * 70)

    ib_session = None
    if ib_up:
        try:
            ib_session = ibkr_data.IBKRSession().__enter__()
        except Exception as e:
            print(f"  IBKR connect failed ({e}) — falling back to yfinance")
            ib_session = None
            data_source = "yfinance (EOD)"

    print("\nLoading SPY...", end=" ", flush=True)
    spy = add_daily_indicators(fetch_daily("SPY"))
    sr, sp = spy.iloc[-1], spy.iloc[-2]
    long_ok = sr["RSI"] > sp["RSI"] and sr["Close"] > sr["SMA50"]
    short_ok = sr["RSI"] < sp["RSI"] and sr["Close"] < sr["SMA50"] * 1.02
    regime = "LONG-FAVORABLE" if long_ok else "SHORT-FAVORABLE" if short_ok else "MIXED / NEUTRAL"
    print(f"data through {spy.index[-1].strftime('%Y-%m-%d')}")
    print(f"SPY regime: {regime}")

    aligned, watching, opens, errors = [], [], [], []
    print(f"\nScanning {len(WATCHLIST)} tickers...", flush=True)
    for i, tk in enumerate(WATCHLIST, 1):
        r = scan_ticker(tk, spy, ib_session)
        if r["error"]: errors.append((tk, r["error"]))
        if r["aligned"]: aligned.append((tk, r["aligned"]))
        if r["watching"]: watching.append((tk, r["watching"]))
        if r["open"]: opens.append((tk, r["open"]))
        if i % 25 == 0 or i == len(WATCHLIST):
            print(f"  [{i:3d}/{len(WATCHLIST)}]", flush=True)

    if ib_session is not None:
        ib_session.__exit__(None, None, None)

    # Report
    print("\n" + "=" * 70)
    print(f"  ★ ACTIONABLE SETUPS — {len(aligned)}")
    print("=" * 70)
    tier2_queue = []
    for tk, a in aligned:
        print(f"\n  {tk}  TIER {a['tier']}  {a['order'].upper()}")
        print(f"    Entry ${a['entry_price']}  Stop ${a['stop_loss']}  "
              f"{a['shares']}sh  (${a['position_size']:,.0f})")
        print(f"    Signal {a['signal_date'].strftime('%Y-%m-%d')} RSI {a['signal_rsi']} → now {a['current_rsi']}")
        print(f"    Weekly Δ {a['weekly_rsi_delta']:+.1f}  MACD cross {a['macd_crossed']}  Earnings {a['next_earnings']}")
        if a["tier"] == 2:
            tier2_queue.append({
                "ticker": a["ticker"], "order": a["order"],
                "entry_price": a["entry_price"], "stop_loss": a["stop_loss"],
                "shares": a["shares"], "tier": 2,
                "signal_date": a["signal_date"].strftime("%Y-%m-%d"),
                "queued_at": datetime.now().isoformat(timespec="seconds"),
            })
    if not aligned: print("  None.")

    print("\n" + "=" * 70)
    print(f"  OPEN POSITIONS — {len(opens)}")
    print("=" * 70)
    for tk, o in opens:
        warn = "  ⚠ time-exit" if o["days_in"] >= MAX_TRADE_DAYS else ""
        print(f"  {tk} {o['order']} {o['entry_date'].strftime('%Y-%m-%d')} "
              f"@ ${o['entry_price']} → ${o['current_price']} "
              f"RSI {o['current_rsi']} ({o['days_in']}d){warn}")
    if not opens: print("  None.")

    total_watching = sum(len(v) for _, v in watching)
    print(f"\n  Watching: {total_watching} pending signals")

    # Queue file for ibkr_paper.py
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(QUEUE_PATH, "w") as f:
        json.dump(tier2_queue, f, indent=2)
    print(f"\n  Wrote {len(tier2_queue)} Tier-2 signal(s) to {QUEUE_PATH}")

    # Email — send if actionable/open, OR if --test-email flag is passed
    test_email = "--test-email" in sys.argv
    if aligned or opens or test_email:
        date_str = datetime.now().strftime("%Y-%m-%d")
        if test_email and not aligned and not opens:
            subject = f"SID Scanner [{date_str}] — TEST EMAIL (no live signals)"
            body = build_email_body(regime,
                                    [("TEST", {"tier": 2, "order": "Long",
                                      "signal_date": datetime.now(), "signal_rsi": 28.5,
                                      "entry_price": 100.00, "stop_loss": 97.00,
                                      "risk_per_share": 3.00, "shares": 333,
                                      "position_size": 33300.00, "current_rsi": 35.2,
                                      "weekly_rsi_delta": 4.5, "macd_crossed": True,
                                      "next_earnings": "2026-05-15"})],
                                    opens, total_watching)
            body = "[TEST EMAIL — fake signal for verification]\n\n" + body
        else:
            subject = f"SID Scanner [{date_str}] — {len(aligned)} Signals | {total_watching} Watching"
            body = build_email_body(regime, aligned, opens, total_watching)
        send_email(subject, body)
    else:
        print("\n  [email] no actionable setups or open positions — not sending.")

    print("\n" + "=" * 70)
    print(f"  Summary: {len(aligned)} actionable | {len(opens)} open | "
          f"{total_watching} watching | data: {data_source}")
    print("=" * 70)


if __name__ == "__main__":
    main()
