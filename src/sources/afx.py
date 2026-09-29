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

    def __init__(self, url: str, crawl_delay_seconds: float = 60, attempts: int = 2, sleep=time.sleep,
                 clock=time.monotonic, give_up_after: int = 3):
        self.url = url
        self.delay = crawl_delay_seconds
        self.attempts = attempts
        self.give_up_after = give_up_after
        self.sleep, self.clock = sleep, clock
        self.last_request = None
        self.http = session()

    def throttle(self) -> None:
        # The crawl delay is a minimum gap between requests, so time spent loading a page counts towards it
        if self.last_request is not None:
            wait = self.delay - (self.clock() - self.last_request)
            if wait > 0:
                self.sleep(wait)
        self.last_request = self.clock()

    def vendor_symbol(self, security_id: str) -> str:
        return security_id

    def fetch(self, symbols, period):
        frames, statuses, failures = [], [], 0
        for i, symbol in enumerate(symbols):
            if failures >= self.give_up_after:
                # The site is refusing or unreachable; stop rather than keep knocking for the rest of the batch
                statuses += [SymbolStatus(s, "error", 0, error=f"Skipped after {failures} consecutive failures")
                             for s in symbols[i:]]
                break
            df, status = self._fetch_one(symbol)
            failures = failures + 1 if status.status == "error" else 0
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
                self.throttle()
                r = self.http.get(self.url.format(code=symbol.lower()), timeout=(10, 30))
                if r.status_code == 404:
                    return empty, SymbolStatus(symbol, "empty", 0)
                r.raise_for_status()
                return self.parse(symbol, r.text)
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the run
                last_error = f"{type(exc).__name__}: {exc}"
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
