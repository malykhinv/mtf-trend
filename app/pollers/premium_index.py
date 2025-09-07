"""Poll premium index percentage and update metric."""

from __future__ import annotations

from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from ..utils import _with_backoff
from .base import _BasePoller


class PremiumIndexPoller(_BasePoller):
    """Poll premium index percentage and update metric."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_PREMIUM)

    async def run(self) -> None:
        while True:
            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                if state.state not in self._WATCHED_STATES:
                    continue

                premium = await _with_backoff(self._rest.get_premium_pct, symbol)
                metrics = state.metrics
                metrics.premium_pct = premium
                self._registry.update(symbol, state)

            await self._sleep()
