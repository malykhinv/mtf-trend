import asyncio
import pathlib
import sys

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from app.pollers.base import _BasePoller
from domain.models.enums import BotState
from domain.models.state import SymbolState
from domain.services.symbol_registry import SymbolRegistry


class DummyPoller(_BasePoller):
    def __init__(self, registry: SymbolRegistry) -> None:
        super().__init__(rest=None, registry=registry, delay_sec=0)
        self.polled: list[str] = []

    async def _sleep(self) -> None:  # pragma: no cover - replaced in tests
        raise asyncio.CancelledError

    async def _poll(self, symbol: str, state: SymbolState) -> None:
        self.polled.append(symbol)


def test_run_polls_only_watched_states() -> None:
    registry = SymbolRegistry()
    registry.put(SymbolState(symbol="A", state=BotState.WATCHING))
    registry.put(SymbolState(symbol="B", state=BotState.IDLE))
    registry.put(SymbolState(symbol="C", state=BotState.ENTERED))

    poller = DummyPoller(registry)

    try:
        asyncio.run(poller.run())
    except asyncio.CancelledError:
        pass

    assert poller.polled == ["A", "C"]

