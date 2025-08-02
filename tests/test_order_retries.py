import pathlib
import sys

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
import exchanges


class FlakyExchange:
    def __init__(self) -> None:
        self.perp_attempts = 0
        self.spot_attempts = 0

    async def place_order(self, *args, **kwargs):
        self.perp_attempts += 1
        if self.perp_attempts < 3:
            raise RuntimeError("fail")
        return {"orderId": "perp"}

    async def place_spot_order(self, *args, **kwargs):
        self.spot_attempts += 1
        if self.spot_attempts < 2:
            raise RuntimeError("fail")
        return {"orderId": "spot"}

    async def get_order_status(self, order_id):
        return {"status": "FILLED", "order_id": order_id}


class AlwaysFailExchange(FlakyExchange):
    async def place_order(self, *args, **kwargs):  # type: ignore[override]
        self.perp_attempts += 1
        raise RuntimeError("fail")

    async def place_spot_order(self, *args, **kwargs):  # type: ignore[override]
        self.spot_attempts += 1
        raise RuntimeError("fail")


async def _fast_sleep(_: float) -> None:
    pass


@pytest.mark.asyncio
async def test_place_order_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    exchanges.current = FlakyExchange()
    monkeypatch.setattr(exchanges.asyncio, "sleep", _fast_sleep)
    order = await exchanges.place_order("BTCUSDT", "BUY", 1)
    assert order["orderId"] == "perp"
    assert exchanges.current.perp_attempts == 3  # type: ignore[attribute-defined-outside-init]


@pytest.mark.asyncio
async def test_spot_order_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    exchanges.current = FlakyExchange()
    monkeypatch.setattr(exchanges.asyncio, "sleep", _fast_sleep)
    order = await exchanges.place_spot_order("BTCUSDT", "BUY", 1)
    assert order["orderId"] == "spot"
    assert exchanges.current.spot_attempts == 2  # type: ignore[attribute-defined-outside-init]


@pytest.mark.asyncio
async def test_place_order_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    exchanges.current = AlwaysFailExchange()
    monkeypatch.setattr(exchanges.asyncio, "sleep", _fast_sleep)
    with pytest.raises(RuntimeError):
        await exchanges.place_order("BTCUSDT", "BUY", 1)
    assert exchanges.current.perp_attempts == exchanges.ORDER_RETRIES  # type: ignore[attribute-defined-outside-init]


@pytest.mark.asyncio
async def test_spot_order_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    exchanges.current = AlwaysFailExchange()
    monkeypatch.setattr(exchanges.asyncio, "sleep", _fast_sleep)
    with pytest.raises(RuntimeError):
        await exchanges.place_spot_order("BTCUSDT", "BUY", 1)
    assert exchanges.current.spot_attempts == exchanges.ORDER_RETRIES  # type: ignore[attribute-defined-outside-init]
