import asyncio
import json
import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from exchanges.bybit import BybitExchange  # noqa: E402


class DummyWebSocket:
    def __init__(self, symbol: str, payload: dict) -> None:
        self.symbol = symbol
        self.payload = payload
        self._sent = False

    async def recv(self) -> str:
        if not self._sent:
            self._sent = True
            return json.dumps(self.payload)
        await asyncio.Future()

    async def close(self) -> None:  # pragma: no cover - nothing to clean up
        pass


@pytest.mark.asyncio
async def test_bybit_futures_orderbooks_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    ex = BybitExchange("key", "secret")

    messages = {
        "BTCUSDT": {"topic": "orderbook.1.BTCUSDT", "data": {"b": [["1", "2"]], "a": [["3", "4"]]}},
        "ETHUSDT": {"topic": "orderbook.1.ETHUSDT", "data": {"b": [["5", "6"]], "a": [["7", "8"]]}},
    }

    async def dummy_connect(symbol: str) -> DummyWebSocket:
        return DummyWebSocket(symbol, messages[symbol])

    monkeypatch.setattr(ex, "_connect", dummy_connect)

    await asyncio.gather(
        ex.get_orderbook("BTCUSDT"),
        ex.get_orderbook("ETHUSDT"),
    )

    async def wait_for_data() -> None:
        while True:
            if ex._orderbooks.get("BTCUSDT") and ex._orderbooks.get("ETHUSDT"):
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_data(), timeout=1)

    assert ex._orderbooks["BTCUSDT"] == {
        "bids": messages["BTCUSDT"]["data"]["b"],
        "asks": messages["BTCUSDT"]["data"]["a"],
    }
    assert ex._orderbooks["ETHUSDT"] == {
        "bids": messages["ETHUSDT"]["data"]["b"],
        "asks": messages["ETHUSDT"]["data"]["a"],
    }
    assert ex._orderbooks["BTCUSDT"] != ex._orderbooks["ETHUSDT"]

    for task in ex._ws_tasks.values():
        task.cancel()
    await asyncio.gather(*ex._ws_tasks.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_bybit_spot_orderbooks_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    ex = BybitExchange("key", "secret")

    messages = {
        "BTCUSDT": {"topic": "orderbook.1.BTCUSDT", "data": {"b": [["1", "2"]], "a": [["3", "4"]]}},
        "ETHUSDT": {"topic": "orderbook.1.ETHUSDT", "data": {"b": [["5", "6"]], "a": [["7", "8"]]}},
    }

    async def dummy_connect(symbol: str) -> DummyWebSocket:
        return DummyWebSocket(symbol, messages[symbol])

    monkeypatch.setattr(ex, "_connect_spot", dummy_connect)

    await asyncio.gather(
        ex.get_spot_orderbook("BTCUSDT"),
        ex.get_spot_orderbook("ETHUSDT"),
    )

    async def wait_for_data() -> None:
        while True:
            if ex._spot_orderbooks.get("BTCUSDT") and ex._spot_orderbooks.get("ETHUSDT"):
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_data(), timeout=1)

    assert ex._spot_orderbooks["BTCUSDT"] == {
        "bids": messages["BTCUSDT"]["data"]["b"],
        "asks": messages["BTCUSDT"]["data"]["a"],
    }
    assert ex._spot_orderbooks["ETHUSDT"] == {
        "bids": messages["ETHUSDT"]["data"]["b"],
        "asks": messages["ETHUSDT"]["data"]["a"],
    }
    assert ex._spot_orderbooks["BTCUSDT"] != ex._spot_orderbooks["ETHUSDT"]

    for task in ex._spot_ws_tasks.values():
        task.cancel()
    await asyncio.gather(*ex._spot_ws_tasks.values(), return_exceptions=True)
