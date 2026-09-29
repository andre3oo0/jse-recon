"""The contract every vendor adapter meets; adapters return what the vendor said, unconverted."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

PRICE_COLUMNS = [
    "vendor_symbol",
    "price_date",     # ISO date in the exchange's local calendar
    "open",
    "high",
    "low",
    "close",
    "adj_close",
    "volume",
    "dividends",
    "splits",
    "reported_unit",  # as the vendor reports it, e.g. 'ZAc'
]


@dataclass
class SymbolStatus:
    vendor_symbol: str
    status: str  # 'ok' | 'empty' | 'error'
    row_count: int
    reported_unit: str | None = None
    error: str | None = None
    isin: str | None = None  # when the vendor publishes it alongside prices


class PriceSource(ABC):
    name: str

    @abstractmethod
    def vendor_symbol(self, security_id: str) -> str:
        """Map an internal security_id to this vendor's symbol."""

    @abstractmethod
    def fetch(
        self, symbols: list[str], period: str
    ) -> tuple[pd.DataFrame, list[SymbolStatus]]:
        """Return prices in PRICE_COLUMNS plus one status per symbol, never raising for one bad symbol."""
