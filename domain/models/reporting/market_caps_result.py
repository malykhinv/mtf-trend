"""DTO for market-cap fetching results."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarketCapsResult:
    market_caps: dict[str, float | str]
