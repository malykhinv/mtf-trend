from __future__ import annotations

import constants
from domain.models.state import GlobalState
from domain.services.symbol_registry import SymbolRegistry


class GlobalPauseGuard:
    """Service checking for BTC-induced global pause."""

    def __init__(self, gstate: GlobalState, registry: SymbolRegistry) -> None:
        self._gstate = gstate
        self._registry = registry

    def should_pause(self, now_ms: int) -> bool:
        """Return ``True`` if processing should pause due to BTC spike."""
        pause_until = self._gstate.btc_pause_until_ms
        if pause_until is not None:
            if now_ms < pause_until:
                return True
            self._gstate.btc_pause_until_ms = None

        if self._registry.has("BTCUSDT"):
            btc_state = self._registry.get("BTCUSDT")
            if btc_state.metrics.z_px > constants.GLOBAL_BTC_PAUSE_Z:
                self._gstate.btc_pause_until_ms = (
                    now_ms + constants.GLOBAL_BTC_PAUSE_SEC * 1000
                )
                return True
        return False


__all__ = ["GlobalPauseGuard"]
