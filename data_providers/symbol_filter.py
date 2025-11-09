from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol, TypedDict

from config.config import SymbolFiltersConfig

from .ccxt_client import TradingStats


class ListingAge(TypedDict, total=False):
    """Represents the calculated listing age for a symbol."""

    days: float
    source_timestamp: int


class _CcxtMarketInfo(TypedDict, total=False):
    """Subset of CCXT market payload used for filtering."""

    symbol: str
    info: "_CcxtExchangeSpecific"


class _CcxtExchangeSpecific(TypedDict, total=False):
    onboardDate: int


class SymbolFilterResult(TypedDict):
    """Outcome of the symbol filtering pipeline."""

    symbol: str
    allowed: bool
    is_new_listing: bool
    listing_age: ListingAge | None


class SymbolFilterProtocol(Protocol):
    def evaluate(self, symbol: str, market: _CcxtMarketInfo, stats: TradingStats) -> SymbolFilterResult:
        ...


@dataclass
class SymbolFilter(SymbolFilterProtocol):
    """Filter symbols based on listing age and trading activity thresholds."""

    config: SymbolFiltersConfig
    now_factory: Callable[[], datetime] = field(default=lambda: datetime.now(tz=timezone.utc))

    def _extract_onboard_timestamp(self, market: _CcxtMarketInfo) -> Optional[int]:
        info = market.get("info")
        if info is None:
            return None
        onboard = info.get("onboardDate")
        if onboard is None:
            return None
        try:
            return int(onboard)
        except (TypeError, ValueError) as exc:
            raise ValueError("Невозможно преобразовать onboardDate в int") from exc

    def _calculate_listing_age(self, market: _CcxtMarketInfo) -> ListingAge | None:
        onboard_timestamp = self._extract_onboard_timestamp(market)
        if onboard_timestamp is None:
            return None

        onboard_dt = datetime.fromtimestamp(onboard_timestamp / 1000, tz=timezone.utc)
        now = self.now_factory()
        age_seconds = (now - onboard_dt).total_seconds()
        age_days = max(age_seconds / 86400, 0.0)
        return ListingAge(days=age_days, source_timestamp=onboard_timestamp)

    def evaluate(self, symbol: str, market: _CcxtMarketInfo, stats: TradingStats) -> SymbolFilterResult:
        listing_age = self._calculate_listing_age(market)
        is_new_listing = False
        volume_threshold = self.config.min_volume_established
        trades_threshold = self.config.min_trades_established

        if listing_age is not None and listing_age["days"] < self.config.n_avg_days:
            is_new_listing = True
            volume_threshold = self.config.min_volume_new_listing
            trades_threshold = 0

        avg_daily_volume = float(stats.get("avg_daily_volume_usdt", 0.0))
        trades_24h = int(stats.get("trades_24h", 0))

        allowed = avg_daily_volume >= volume_threshold and trades_24h >= trades_threshold

        return SymbolFilterResult(
            symbol=symbol,
            allowed=allowed,
            is_new_listing=is_new_listing,
            listing_age=listing_age,
        )


__all__ = [
    "ListingAge",
    "SymbolFilter",
    "SymbolFilterResult",
]
