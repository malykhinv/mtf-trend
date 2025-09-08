"""Poll open interest and update delta percentage metric."""

from __future__ import annotations

from domain.models.state import SymbolState
from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from ..utils import _with_backoff
from .base import _BasePoller


class OpenInterestPoller(_BasePoller):
    """Poll open interest and update delta percentage metric."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_OI)
        self._last_oi: dict[str, float] = {}

    async def _poll(self, symbol: str, state: SymbolState) -> None:
        oi = await _with_backoff(self._rest.get_open_interest, symbol)
        prev = self._last_oi.get(symbol)
        delta_pct = ((oi - prev) / prev * 100.0) if prev else 0.0
        self._last_oi[symbol] = oi

        metrics = state.metrics
        metrics.delta_oi_pct = delta_pct
