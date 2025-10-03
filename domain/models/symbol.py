from __future__ import annotations

from dataclasses import dataclass

from .enums import Exchange


@dataclass(frozen=True, slots=True)
class SymbolFilters:
    exchange: Exchange
    symbol: str
    base_asset: str
    quote_asset: str
    price_tick_size: float
    quantity_step_size: float
    min_price: float
    max_price: float
    min_qty: float
    max_qty: float
    min_notional: float


__all__ = ["SymbolFilters"]
