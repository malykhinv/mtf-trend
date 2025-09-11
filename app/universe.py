from __future__ import annotations

import asyncio
import logging

from constants import MAX_SYMBOLS
from domain.ports.rest_client import RestClient

from . import universe_filters as filters


logger = logging.getLogger(__name__)


class UniverseBuilder:
    def __init__(self, rest: RestClient) -> None:
        self._rest = rest
        self._depth_sem = asyncio.Semaphore(20)

    async def build(self) -> list[str]:
        skipped_usdt = 0
        skipped_volume = 0
        skipped_spread = 0
        skipped_depth = 0

        tickers = await self._rest.fetch_all_tickers()

        candidates: list[tuple[str, float]] = []
        depth_coros = []
        for sym, bid, ask, qvol in tickers:
            if not self._is_usdt_pair(sym):
                skipped_usdt += 1
                continue

            if qvol < filters.MIN_24H_USDT:
                skipped_volume += 1
                continue

            if not self._within_spread(sym, bid, ask):
                skipped_spread += 1
                continue

            candidates.append((sym, qvol))
            depth_coros.append(self._has_depth(sym))

        depth_results = await asyncio.gather(*depth_coros)
        survivors: list[tuple[str, float]] = []
        for (sym, qvol), has_depth in zip(candidates, depth_results):
            if has_depth:
                survivors.append((sym, qvol))
            else:
                skipped_depth += 1

        # топ-180 по объёму после отсева
        survivors.sort(key=lambda x: x[1], reverse=True)
        selected = survivors[:MAX_SYMBOLS]
        symbols = [s for s, _ in selected]
        trimmed_by_limit = max(0, len(survivors) - len(selected))

        logger.info("Выбрано %d монет (лимит %d), всего после отсева: %d",
                    len(symbols), MAX_SYMBOLS, len(survivors))
        logger.info(
            "Скипы: !USDT=%d, объём=%d, спред=%d, глубина=%d, по_лимиту=%d",
            skipped_usdt,
            skipped_volume,
            skipped_spread,
            skipped_depth,
            trimmed_by_limit,
        )
        return symbols

    def _is_usdt_pair(self, sym: str) -> bool:
        return sym.endswith("USDT")

    def _within_spread(self, sym: str, bid: float, ask: float) -> bool:
        if bid <= 0:
            logger.debug("%s: неположительный bid", sym)
            return False
        spread_bps = (ask - bid) / bid * 10_000.0
        if spread_bps > filters.MAX_SPREAD_BPS:
            logger.debug("%s: спред %.1f bps > %.1f", sym, spread_bps, filters.MAX_SPREAD_BPS)
            return False
        return True

    async def _has_depth(self, sym: str) -> bool:
        async with self._depth_sem:
            bids = await self._rest.get_depth(sym)
        top10_bid_usdt = sum(p * q for p, q in bids)
        if top10_bid_usdt < filters.MIN_TOP10_BID_USDT:
            logger.debug(
                "%s: глубина %.0f < %.0f", sym, top10_bid_usdt, filters.MIN_TOP10_BID_USDT
            )
            return False
        return True
