"""Build the ~500-ticker scan universe: S&P 500 + ETFs + Sid's original list."""

import io
import warnings

import pandas as pd
import requests
import yfinance as yf


# Sid's original 64-ticker watchlist
SIDS_LIST = [
    "AAL", "AAPL", "AMD", "AMZN", "B", "BA", "BAC", "CAT", "COIN", "CVX",
    "DIA", "DIS", "DRIP", "ELF", "EXEL", "GDX", "GM", "GOOG", "GS", "HD",
    "HUM", "HUT", "IBM", "INTC", "IWM", "JPM", "KHC", "KO", "LUV", "LVS",
    "MCD", "MU", "NEM", "NUGT", "PYPL", "QQQ", "RIOT", "ROKU", "RTX", "SLB",
    "SLV", "SMH", "SPY", "SQQQ", "TGT", "TJX", "TNA", "TQQQ", "TSLA", "TZA",
    "V", "WMT", "XHB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE",
    "XLU", "XLV", "XLY", "XRT",
]

# Broad ETF list for sector/thematic coverage
ETF_LIST = [
    "DIA", "IWM", "QQQ", "SPY",          # Index
    "XLB", "XLC", "XLE", "XLF", "XLI",   # Sector
    "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
    "XHB", "XRT", "SMH", "XBI", "XOP",   # Industry
    "GDX", "GDXJ", "SLV", "GLD", "USO",  # Commodity
    "TLT", "HYG", "LQD", "TIP",          # Fixed income
    "EEM", "EFA", "FXI", "KWEB",         # International
    "ARKK", "ARKF",                        # Thematic
    "TQQQ", "SQQQ", "TNA", "TZA",        # Leveraged
    "DRIP", "NUGT",                        # Leveraged commodity
    "VXX",                                 # Volatility
]


def _fetch_sp500_table() -> pd.DataFrame:
    """Scrape S&P 500 constituents from Wikipedia. Returns DataFrame with Symbol and GICS Sector."""
    url = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
    # Use requests (which uses certifi) to avoid macOS SSL issues with urllib
    headers = {"User-Agent": "Mozilla/5.0 (compatible; SID-Backtester/1.0)"}
    resp = requests.get(url, timeout=30, headers=headers)
    resp.raise_for_status()
    tables = pd.read_html(io.StringIO(resp.text))
    df = tables[0]
    # Normalise symbol column: BRK.B -> BRK-B (Yahoo format)
    df["Symbol"] = df["Symbol"].str.replace(".", "-", regex=False)
    return df[["Symbol", "GICS Sector"]]


def _build_sector_map(sp500_df: pd.DataFrame) -> dict[str, str]:
    """Map ticker -> sector. ETFs get 'ETF', unknown get 'Other'."""
    sector_map = {}
    for _, row in sp500_df.iterrows():
        sector_map[row["Symbol"]] = row["GICS Sector"]
    for etf in ETF_LIST:
        sector_map.setdefault(etf, "ETF")
    return sector_map


def _filter_liquid(tickers: list[str], min_price: float = 5.0,
                   min_volume: int = 500_000, batch_size: int = 50) -> list[str]:
    """Filter tickers by avg close >= min_price and avg volume >= min_volume.

    Uses batch yf.download (much faster than Ticker.info per-symbol).
    """
    liquid = []
    for start in range(0, len(tickers), batch_size):
        batch = tickers[start:start + batch_size]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                data = yf.download(batch, period="1mo", progress=False,
                                   auto_adjust=True, threads=True)
            if data.empty:
                continue

            # yf.download returns MultiIndex columns (field, ticker) for multiple tickers
            if isinstance(data.columns, pd.MultiIndex):
                for t in batch:
                    try:
                        close = data["Close"][t]
                        volume = data["Volume"][t]
                        avg_close = close.mean()
                        avg_vol = volume.mean()
                        if not pd.isna(avg_close) and not pd.isna(avg_vol):
                            if avg_close >= min_price and avg_vol >= min_volume:
                                liquid.append(t)
                    except (KeyError, TypeError):
                        continue
            else:
                # Single ticker — columns are just field names
                t = batch[0]
                avg_close = data["Close"].mean()
                avg_vol = data["Volume"].mean()
                if not pd.isna(avg_close) and not pd.isna(avg_vol):
                    if avg_close >= min_price and avg_vol >= min_volume:
                        liquid.append(t)
        except Exception:
            # On batch failure, keep all tickers from this batch (fail open)
            liquid.extend(batch)
    return liquid


def get_universe() -> tuple[list[str], dict[str, str]]:
    """Build the full scan universe.

    Returns (sorted_tickers, sector_map).
    Falls back to SIDS_LIST + ETF_LIST if Wikipedia scrape fails.
    """
    # Try to fetch S&P 500
    sp500_df = None
    try:
        print("Fetching S&P 500 constituents from Wikipedia...", end=" ", flush=True)
        sp500_df = _fetch_sp500_table()
        sp500_symbols = sp500_df["Symbol"].tolist()
        print(f"{len(sp500_symbols)} found")
    except Exception as e:
        print(f"FAILED ({e}) — using fallback list")
        sp500_symbols = []

    # Union all lists, deduplicate
    all_tickers = sorted(set(sp500_symbols + ETF_LIST + SIDS_LIST))
    print(f"Universe before liquidity filter: {len(all_tickers)} tickers")

    # Liquidity filter
    print("Running liquidity filter (avg close >= $5, avg volume >= 500K)...", flush=True)
    liquid = _filter_liquid(all_tickers)
    print(f"Universe after liquidity filter: {len(liquid)} tickers")

    # Always keep Sid's list (they're hand-picked)
    for t in SIDS_LIST:
        if t not in liquid:
            liquid.append(t)

    liquid = sorted(set(liquid))

    # Build sector map
    if sp500_df is not None:
        sector_map = _build_sector_map(sp500_df)
    else:
        sector_map = {etf: "ETF" for etf in ETF_LIST}

    # Fill in unknowns
    for t in liquid:
        sector_map.setdefault(t, "Other")

    return liquid, sector_map


if __name__ == "__main__":
    tickers, sectors = get_universe()
    print(f"\nFinal universe: {len(tickers)} tickers")
    # Show sector breakdown
    from collections import Counter
    counts = Counter(sectors[t] for t in tickers)
    print("\nSector breakdown:")
    for sector, count in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {sector}: {count}")
    # Verify Sid's list is fully included
    missing = [t for t in SIDS_LIST if t not in tickers]
    if missing:
        print(f"\nWARNING: Missing from Sid's list: {missing}")
    else:
        print(f"\nAll {len(SIDS_LIST)} of Sid's tickers present")
