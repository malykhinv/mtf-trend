"""Poll taker buy/sell volumes and store them in metrics."""

from __future__ import annotations

from domain.models.state import SymbolState
from domain.ports.rest_client import RestClient
from domain.services.symbol_registry import SymbolRegistry

import constants

from ..utils import _with_backoff
from .base import _BasePoller


class TakerRatioPoller(_BasePoller):
    """Poll taker buy/sell volumes and store them in metrics."""

    def __init__(self, rest: RestClient, registry: SymbolRegistry) -> None:
        super().__init__(rest, registry, constants.REST_POLL_SEC_TAKER)

    async def _poll(self, symbol: str, state: SymbolState) -> None:
        buy, sell = await _with_backoff(self._rest.get_taker_ratio, symbol)
        metrics = state.metrics
        metrics.taker_buy_volume = buy
        metrics.taker_sell_volume = sell
