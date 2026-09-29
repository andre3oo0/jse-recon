import logging
import time
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import yfinance as yf

from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus

# yfinance logs every retry and crumb failure. Outcomes are recorded in
# ingest_symbol_status instead, so its own logging is only noise here.
logging.getLogger("yfinance").setLevel(logging.CRITICAL)

_RENAME = {
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Adj Close": "adj_close",
    "Volume": "volume",
    "Dividends": "dividends",
    "Stock Splits": "splits",
}


class YahooSource(PriceSource):
    name = "yahoo"

    def __init__(self, suffix: str = ".JO", workers: int = 6, attempts: int = 3):
        self.suffix = suffix
        self.workers = workers
        self.attempts = attempts

    def vendor_symbol(self, security_id: str) -> str:
        return f"{security_id}{self.suffix}"

    def fetch(self, symbols, period):
        with ThreadPoolExecutor(max_workers=self.workers) as pool:
            results = list(pool.map(lambda s: self._fetch_one(s, period), symbols))

        frames = [df for df, _ in results if not df.empty]
        prices = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=PRICE_COLUMNS)
        )
        return prices, [status for _, status in results]

    def _fetch_one(self, symbol: str, period: str):
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                ticker = yf.Ticker(symbol)
                hist = ticker.history(period=period, auto_adjust=False, actions=True)
                if hist.empty:
                    # Empty with no exception is usually a dead symbol, but
                    # the TLS-intercepting network here also produces empty
                    # responses, so it earns the same retries as an error.
                    last_error = None
                else:
                    unit = (ticker.history_metadata or {}).get("currency")
                    return self._to_frame(symbol, hist, unit), SymbolStatus(
                        symbol, "ok", len(hist), reported_unit=unit
                    )
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the run
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.attempts:
                time.sleep(2 ** attempt)

        empty = pd.DataFrame(columns=PRICE_COLUMNS)
        if last_error:
            return empty, SymbolStatus(symbol, "error", 0, error=last_error[:500])
        return empty, SymbolStatus(symbol, "empty", 0)

    @staticmethod
    def _to_frame(symbol: str, hist: pd.DataFrame, unit: str | None) -> pd.DataFrame:
        df = hist.rename(columns=_RENAME).reset_index()
        # Yahoo stamps each bar at local midnight, tz-aware. The date in the
        # exchange's own timezone is the trading date; converting to UTC
        # first would shift every bar to the previous day.
        df["price_date"] = df["Date"].dt.strftime("%Y-%m-%d")
        df["vendor_symbol"] = symbol
        df["reported_unit"] = unit
        for col in PRICE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[PRICE_COLUMNS]
