"""EODHD end-of-day prices; the API key comes only from the EODHD_API_KEY environment variable."""

import os
import re
import time
from datetime import date, timedelta

import pandas as pd

from src.sources.base import PRICE_COLUMNS, PriceSource, SymbolStatus
from src.sources.http import session

URL = "https://eodhd.com/api/eod/{symbol}"
PERIOD_DAYS = {"10d": 14, "1mo": 31, "3mo": 92, "6mo": 183, "1y": 366, "2y": 731, "5y": 1827}


class MissingApiKey(RuntimeError):
    pass


class EodhdSource(PriceSource):
    name = "eodhd"

    def __init__(self, suffix: str = ".JSE", attempts: int = 2, api_key=None):
        self.suffix = suffix
        self.attempts = attempts
        self.api_key = api_key if api_key is not None else os.environ.get("EODHD_API_KEY", "")
        self.http = session()

    def vendor_symbol(self, security_id: str) -> str:
        return f"{security_id}{self.suffix}"

    def fetch(self, symbols, period):
        if not self.api_key:
            raise MissingApiKey("EODHD_API_KEY is not set")
        start = (date.today() - timedelta(days=PERIOD_DAYS[period])).isoformat()
        frames, statuses = [], []
        for symbol in symbols:
            df, status = self._fetch_one(symbol, start)
            frames.append(df)
            statuses.append(status)
        frames = [f for f in frames if not f.empty]
        prices = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=PRICE_COLUMNS)
        return prices, statuses

    def redact(self, text: str) -> str:
        # requests puts the full URL, token included, into its exception messages
        return re.sub(re.escape(self.api_key), "***", text) if self.api_key else text

    def _fetch_one(self, symbol: str, start: str):
        empty = pd.DataFrame(columns=PRICE_COLUMNS)
        last_error = None
        for attempt in range(1, self.attempts + 1):
            try:
                r = self.http.get(
                    URL.format(symbol=symbol),
                    params={"api_token": self.api_key, "fmt": "json", "from": start},
                    timeout=30,
                )
                if r.status_code == 404:
                    return empty, SymbolStatus(symbol, "empty", 0)
                if r.status_code != 200:
                    # 402/403 mean the plan does not cover the ticker; retrying spends calls for nothing
                    return empty, SymbolStatus(symbol, "error", 0, error=self.redact(f"HTTP {r.status_code}: {r.text[:200]}"))
                rows = r.json()
                if not rows:
                    return empty, SymbolStatus(symbol, "empty", 0)
                return self._to_frame(symbol, rows), SymbolStatus(symbol, "ok", len(rows))
            except Exception as exc:  # noqa: BLE001 - one bad symbol must not sink the run
                last_error = self.redact(f"{type(exc).__name__}: {exc}")
            if attempt < self.attempts:
                time.sleep(2 ** attempt)
        return empty, SymbolStatus(symbol, "error", 0, error=last_error[:500])

    def _to_frame(self, symbol: str, rows: list[dict]) -> pd.DataFrame:
        df = pd.DataFrame(rows).rename(columns={"date": "price_date", "adjusted_close": "adj_close"})
        df["vendor_symbol"] = symbol
        df["reported_unit"] = None  # EODHD does not state a currency or unit in its end-of-day response
        for col in PRICE_COLUMNS:
            if col not in df.columns:
                df[col] = None
        return df[PRICE_COLUMNS]
