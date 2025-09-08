from __future__ import annotations

import logging

from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.services.symbol_registry import SymbolRegistry
import constants


logger = logging.getLogger(__name__)


class CooldownHandler:
    """Transition symbols out of cooldown when time has passed."""

    def __init__(self, registry: SymbolRegistry) -> None:
        self._registry = registry

    def handle(self, symbol: str, state: SymbolState, now: float) -> None:
        if (
            state.last_signal_ts is not None
            and now - state.last_signal_ts >= constants.COOLDOWN_AFTER_TRADE_SEC
        ):
            state.state = BotState.IDLE
            state.last_signal_ts = None
            self._registry.update(symbol, state)
            logger.info("Символ %s снова IDLE", symbol)


__all__ = ["CooldownHandler"]
