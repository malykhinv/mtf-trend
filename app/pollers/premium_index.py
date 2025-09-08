"""Poll premium index percentage and update metric."""

from __future__ import annotations

from domain.models.state import SymbolState
from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from ..utils import _with_backoff
from .base import _BasePoller


class PremiumIndexPoller(_BasePoller):
    """Poll premium index percentage and update metric."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_PREMIUM)

    async def _poll(self, symbol: str, state: SymbolState) -> None:
        premium = await _with_backoff(self._rest.get_premium_pct, symbol)
        metrics = state.metrics
        metrics.premium_pct = premium
