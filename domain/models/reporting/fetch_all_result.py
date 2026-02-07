"""DTO for combined OHLCV/OI/market-cap fetching results."""

from __future__ import annotations

from dataclasses import dataclass

from domain.models.reporting.market_caps_result import MarketCapsResult


@dataclass(frozen=True, slots=True)
class FetchAllResult:
    ohlcv: dict[str, int | str]
    open_interest: dict[str, int | str]
    market_caps: MarketCapsResult
