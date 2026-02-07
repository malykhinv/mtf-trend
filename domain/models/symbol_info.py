"""Symbol metadata model."""

from __future__ import annotations

from dataclasses import dataclass

from domain.value_objects.volume import Volume


@dataclass(frozen=True, slots=True)
class SymbolInfo:
    symbol: str
    market_cap: float
    daily_volume: Volume
    is_active: bool = True

    def __post_init__(self) -> None:
        if not self.symbol:
            msg = "SymbolInfo symbol is required."
            raise ValueError(msg)

        if self.market_cap < 0:
            msg = "SymbolInfo market_cap cannot be negative."
            raise ValueError(msg)
