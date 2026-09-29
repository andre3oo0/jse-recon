"""The contract every price vendor adapter meets.

The recon core only ever sees PRICE_COLUMNS, so swapping or adding a
vendor never touches the matching SQL. Adapters return what the vendor
said, unconverted: units, symbols and calendars are normalised later, in
staging, where the conversion is visible and testable.
"""

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


class PriceSource(ABC):
    name: str

    @abstractmethod
    def vendor_symbol(self, security_id: str) -> str:
        """Map an internal security_id to this vendor's symbol."""

    @abstractmethod
    def fetch(
        self, symbols: list[str], period: str
    ) -> tuple[pd.DataFrame, list[SymbolStatus]]:
        """Return prices in PRICE_COLUMNS plus one status per symbol.

        Must not raise for a single bad symbol: record it as 'empty' or
        'error' and carry on, so one dead ticker cannot sink a run.
        """
