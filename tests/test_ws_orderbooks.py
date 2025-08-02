import asyncio
import json
import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from exchanges.binance import BinanceExchange  # noqa: E402


class DummyWebSocket:
    def __init__(self, symbol: str, payload: dict[str, list[list[str]]]) -> None:
        self.symbol = symbol
        self.payload = payload
        self._sent = False

    async def recv(self) -> str:
        if not self._sent:
            self._sent = True
            return json.dumps(self.payload)
        # wait forever until cancelled
        await asyncio.Future()

    async def close(self) -> None:  # pragma: no cover - no cleanup needed
        pass


@pytest.mark.asyncio
async def test_orderbooks_are_isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    ex = BinanceExchange("key", "secret")

    messages = {
        "BTCUSDT": {"bids": [["1", "2"]], "asks": [["3", "4"]]},
        "ETHUSDT": {"bids": [["5", "6"]], "asks": [["7", "8"]]},
    }

    async def dummy_connect(symbol: str) -> DummyWebSocket:
        return DummyWebSocket(symbol, messages[symbol])

    monkeypatch.setattr(ex, "_connect", dummy_connect)

    # Запрашиваем стаканы одновременно, запускаются отдельные слушатели
    await asyncio.gather(
        ex.get_orderbook("BTCUSDT"),
        ex.get_orderbook("ETHUSDT"),
    )

    # Ждём обновления обоих стаканов
    async def wait_for_data() -> None:
        while True:
            if (
                ex._orderbooks.get("BTCUSDT") and
                ex._orderbooks.get("ETHUSDT")
            ):
                return
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_data(), timeout=1)

    assert ex._orderbooks["BTCUSDT"] == messages["BTCUSDT"]
    assert ex._orderbooks["ETHUSDT"] == messages["ETHUSDT"]
    assert ex._orderbooks["BTCUSDT"] != ex._orderbooks["ETHUSDT"]

    for task in ex._ws_tasks.values():
        task.cancel()
    await asyncio.gather(*ex._ws_tasks.values(), return_exceptions=True)
