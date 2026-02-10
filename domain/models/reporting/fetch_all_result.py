"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.reporting.market_caps_result import MarketCapsResult
from domain.models.reporting.symbol_fetch_result import SymbolFetchResult


@dataclass(frozen=True, slots=True)
class FetchAllResult:
    ohlcv: dict[str, SymbolFetchResult]
    open_interest: dict[str, SymbolFetchResult]
    market_caps: MarketCapsResult
    failed_symbols_count: int
    has_errors: bool
