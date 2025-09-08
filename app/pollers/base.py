"""Common functionality for REST pollers."""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod

from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry


class _BasePoller(ABC):
    """Base class for REST pollers with common logic."""

    _WATCHED_STATES = {
        BotState.WATCHING,
        BotState.ENTERED,
    }

    def __init__(self, rest: RestClient, registry: SymbolRegistry, delay_sec: int) -> None:
        self._rest = rest
        self._registry = registry
        self._delay_sec = delay_sec

    async def _sleep(self) -> None:
        await asyncio.sleep(self._delay_sec * random.uniform(0.8, 1.2))

    @abstractmethod
    async def _poll(self, symbol: str, state: SymbolState) -> None:
        """Poll a single symbol and update its state."""

    async def run(self) -> None:
        while True:
            for symbol in self._registry.all_symbols():
                state = self._registry.get(symbol)
                if state.state not in self._WATCHED_STATES:
                    continue

                await self._poll(symbol, state)
                self._registry.update(symbol, state)

            await self._sleep()
