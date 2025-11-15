from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from datetime import datetime

from crypto_screener.domain.exchange import FuturesSymbol


class Mode(ABC):
    """Base class for application modes."""


class Live(Mode):
    """Run application in live monitoring mode."""


class TestMarket(Mode):
    """Run application in market-wide testing mode."""


@dataclass(frozen=True)
class TestSymbol(Mode):
    """Run application against a single symbol with custom parameters."""

    symbol: FuturesSymbol
    end: datetime
    limit: int
