"""REST data pollers for additional metrics."""

from __future__ import annotations

import asyncio
import random

from domain.models.enums import BotState
from domain.services.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from .utils import _with_backoff


class _BasePoller:
    """Common functionality for REST pollers."""

    _WATCHED_STATES = {
        BotState.WATCHING,
        BotState.CONFIRMING,
        BotState.ENTERED,
    }

    def __init__(self, rest: RestClient, registry: SymbolRegistry, delay_sec: int) -> None:
        self._rest = rest
        self._registry = registry
        self._delay_sec = delay_sec

    async def _sleep(self) -> None:
        await asyncio.sleep(self._delay_sec * random.uniform(0.8, 1.2))

    async def run(self) -> None:  # pragma: no cover - to be implemented by subclasses
        raise NotImplementedError


class OpenInterestPoller(_BasePoller):
    """Poll open interest and update delta percentage metric."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_OI)
        self._last_oi: dict[str, float] = {}

    async def run(self) -> None:
        while True:
            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                if state.state not in self._WATCHED_STATES:
                    continue

                oi = await _with_backoff(self._rest.get_open_interest, symbol)
                prev = self._last_oi.get(symbol)
                delta_pct = ((oi - prev) / prev * 100.0) if prev else 0.0
                self._last_oi[symbol] = oi

                metrics = state.metrics
                metrics.delta_oi_pct = delta_pct
                self._registry.update(symbol, state)

            await self._sleep()


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


__all__ = [
    "OpenInterestPoller",
    "TakerRatioPoller",
    "PremiumIndexPoller",
]

