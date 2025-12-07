from __future__ import annotations

from abc import ABC, abstractmethod

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.context import Context
from crypto_screener.domain.models.setup import Setup
from crypto_screener.domain.models.timeframe import Timeframe


class Strategy(ABC):
    name: str

    @abstractmethod
    def detect_setup(
            self,
            symbol: str,
            bars: list[Bar],
            timeframe: Timeframe,
            context: Context,
    ) -> Setup:
        """Определяет сетап для заданного символа."""
