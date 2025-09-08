"""Common functionality for REST pollers."""

from __future__ import annotations

import asyncio
import random

from domain.models.enums import BotState
from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry


class _BasePoller:
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

    async def run(self) -> None:  # pragma: no cover - to be implemented by subclasses
        raise NotImplementedError
