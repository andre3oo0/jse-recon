"""AFX (afx.kwayisi.org) share pages: the last ten sessions per stock, fetched at the pace robots.txt asks for."""

import io
import re
import time

import pandas as pd

from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus
from src.sources.http import session

UNIT_NOTE = "Monetary values are quoted in South African Rand (ZAR)"


class AfxSource(PriceSource):
    name = "afx"

    def __init__(self, url: str, crawl_delay_seconds: float = 60, attempts: int = 2, sleep=time.sleep):
        self.url = url
        self.delay = crawl_delay_seconds
        self.attempts = attempts
        self.sleep = sleep
        self.http = session()

    def vendor_symbol(self, security_id: str) -> str:
        return security_id

    def fetch(self, symbols, period):
        frames, statuses = [], []
        for i, symbol in enumerate(symbols):
            if i:
                self.sleep(self.delay)
            df, status = self._fetch_one(symbol)
            frames.append(df)
            statuses.append(status)
        frames = [f for f in frames if not f.empty]
        prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)
        return prices, statuses

    def _fetch_one(self, symbol: str):
        empty = pd.DataFrame(columns=PRICE_COLUMNS)
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                r = self.http.get(self.url.format(code=symbol.lower()), timeout=30)
                if r.status_code == 404:
                    return empty, SymbolStatus(symbol, "empty", 0)
                r.raise_for_status()
                return self.parse(symbol, r.text)
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the run
                last_error = f"{type(exc).__name__}: {exc}"
            if attempt < self.attempts:
                self.sleep(self.delay)
        return empty, SymbolStatus(symbol, "error", 0, error=last_error[:500])

    @staticmethod
    def parse(symbol: str, html: str):
        history = next(
            (t for t in pd.read_html(io.StringIO(html)) if {"Date", "Close", "Volume"} <= set(t.columns)),
            None,
        )
        if history is None or history.empty:
            return pd.DataFrame(columns=PRICE_COLUMNS), SymbolStatus(symbol, "empty", 0)

        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        unit = "ZAR" if UNIT_NOTE in text else None  # read from the page, never assumed
        isin = re.search(rf"ISIN\s*\)\s+of\s+JSE:{re.escape(symbol)}\s+is\s+([A-Z]{{2}}[A-Z0-9]{{9}}[0-9])", text)
        df = pd.DataFrame({
            "vendor_symbol": symbol,
            "price_date": pd.to_datetime(history["Date"]).dt.strftime("%Y-%m-%d"),
            "close": pd.to_numeric(history["Close"], errors="coerce"),
            "volume": pd.to_numeric(history["Volume"], errors="coerce"),
            "reported_unit": unit,
        })
        for col in PRICE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        status = SymbolStatus(symbol, "ok", len(df), unit, isin=isin.group(1) if isin else None)
        return df[PRICE_COLUMNS], status
