import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
import exchanges


class FailingPerpExchange:
    def __init__(self) -> None:
        self.cancelled: list[tuple[str, str]] = []

    async def place_spot_order(self, *args, **kwargs):
        return {"orderId": "spot"}

    async def place_order(self, *args, **kwargs):
        raise RuntimeError("fail")

    async def cancel_order(self, symbol: str, order_id: str):
        self.cancelled.append((symbol, order_id))
        return {"status": "CANCELED"}

    async def get_order_status(self, order_id: str):
        return {"status": "FILLED", "order_id": order_id}


async def _fast_sleep(_: float) -> None:
    pass


@pytest.mark.asyncio
async def test_first_leg_canceled_on_second_leg_error(monkeypatch: pytest.MonkeyPatch) -> None:
    exch = FailingPerpExchange()
    exchanges.current = exch
    monkeypatch.setattr(exchanges.asyncio, "sleep", _fast_sleep)
    with pytest.raises(RuntimeError):
        await exchanges.hedge("BTCUSDT", 1)
    assert exch.cancelled == [("BTCUSDT", "spot")]
