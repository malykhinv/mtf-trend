import asyncio
import json
import pathlib
import sys

import pytest

# Add repository root to sys.path
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from exchanges.bybit import BybitExchange  # noqa: E402


class DummyWS:
    """Simple websocket stub delivering queued messages."""

    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.queue: asyncio.Queue[str] = asyncio.Queue()

    async def send(self, _msg: str) -> None:  # pragma: no cover - unused
        pass

    async def recv(self) -> str:
        return await self.queue.get()

    async def close(self) -> None:  # pragma: no cover - no cleanup needed
        pass


@pytest.mark.asyncio
async def test_orderbook_multiple_symbols(monkeypatch: pytest.MonkeyPatch) -> None:
    events: dict[str, asyncio.Queue[str]] = {}

    async def fake_connect(self: BybitExchange, symbol: str) -> DummyWS:
        ws = DummyWS(symbol)
        events[symbol] = ws.queue
        return ws

    ex = BybitExchange("key", "secret")
    # Patch _connect to return our dummy websockets
    monkeypatch.setattr(ex, "_connect", fake_connect.__get__(ex, BybitExchange))

    # Subscribe to two different symbols
    await ex.get_orderbook("BTCUSDT")
    await ex.get_orderbook("ETHUSDT")

    # Allow tasks to establish connections
    await asyncio.sleep(0.01)

    # Ensure queues for both symbols exist (separate connections created)
    assert set(events.keys()) == {"BTCUSDT", "ETHUSDT"}

    # Send distinct messages for each symbol
    await events["BTCUSDT"].put(
        json.dumps({
            "topic": "orderbook.1.BTCUSDT",
            "data": {"b": [["1", "2"]], "a": []},
        })
    )
    await events["ETHUSDT"].put(
        json.dumps({
            "topic": "orderbook.1.ETHUSDT",
            "data": {"b": [["3", "4"]], "a": []},
        })
    )

    # Allow listener tasks to process messages
    await asyncio.sleep(0.1)

    assert ex._orderbooks["BTCUSDT"]["bids"][0][0] == "1"
    assert ex._orderbooks["ETHUSDT"]["bids"][0][0] == "3"

    # Cleanup tasks
    for task in ex._ws_tasks.values():
        task.cancel()
    await asyncio.gather(*ex._ws_tasks.values(), return_exceptions=True)
