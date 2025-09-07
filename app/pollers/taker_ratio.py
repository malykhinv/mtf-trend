"""Poll taker buy/sell volumes and store them in metrics."""

from __future__ import annotations

from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from ..utils import _with_backoff
from .base import _BasePoller


class TakerRatioPoller(_BasePoller):
    """Poll taker buy/sell volumes and store them in metrics."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_TAKER)

    async def run(self) -> None:
        while True:
            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                if state.state not in self._WATCHED_STATES:
                    continue

                buy, sell = await _with_backoff(self._rest.get_taker_ratio, symbol)
                metrics = state.metrics
                metrics.taker_buy_volume = buy
                metrics.taker_sell_volume = sell
                self._registry.update(symbol, state)

            await self._sleep()
