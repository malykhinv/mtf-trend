from __future__ import annotations

from domain.ports.rest_client import RestClient

from . import universe_filters as filters


class UniverseBuilder:
    def __init__(self, rest: RestClient) -> None:
        self._rest = rest

    async def build(self) -> list[str]:
        symbols: list[str] = []

        tickers = await self._rest.fetch_all_tickers()
        for sym, bid, ask in tickers:
            if not self._is_usdt_pair(sym):
                continue

            if not await self._passes_volume(sym):
                continue

            if not self._within_spread(bid, ask):
                continue

            if not await self._has_depth(sym):
                continue

            symbols.append(sym)

        return symbols

    def _is_usdt_pair(self, sym: str) -> bool:
        return sym.endswith("USDT")

    async def _passes_volume(self, sym: str) -> bool:
        quote_vol, _ = await self._rest.get_24h_stats(sym)
        return quote_vol >= filters.MIN_24H_USDT

    def _within_spread(self, bid: float, ask: float) -> bool:
        if bid <= 0:
            return False
        spread_bps = (ask - bid) / bid * 10_000.0
        return spread_bps <= filters.MAX_SPREAD_BPS

    async def _has_depth(self, sym: str) -> bool:
        bids = await self._rest.get_depth(sym)
        top10_bid_usdt = sum(p * q for p, q in bids)
        return top10_bid_usdt >= filters.MIN_TOP10_BID_USDT
