import asyncio
import sys
import types
from pathlib import Path
from decimal import Decimal

sys.path.append(str(Path(__file__).resolve().parents[1]))

# Stub sklearn to avoid heavy dependency during import
sklearn = types.ModuleType("sklearn")
linear_model = types.ModuleType("linear_model")

class _LR:
    def fit(self, *args, **kwargs):
        return None

    def predict(self, x):
        return [0] * len(x)


linear_model.LinearRegression = _LR
sklearn.linear_model = linear_model
sys.modules.setdefault("sklearn", sklearn)
sys.modules.setdefault("sklearn.linear_model", linear_model)

import strategies.funding_arbitrage as fa
from strategies.funding_arbitrage import Position, close_neutral_position
from risk import risk_control


async def _async_noop(*args, **kwargs):
    pass


def _patch_risk(monkeypatch):
    monkeypatch.setattr(risk_control, "update_position", lambda *_: None)
    monkeypatch.setattr(risk_control, "record_pnl", lambda *_: None)
    monkeypatch.setattr(risk_control, "mark_symbol_closed", lambda *_: None)


class DummyExchange:
    name = "dummy"

    async def place_order(self, symbol, side, quantity):
        await asyncio.sleep(0)
        return {"id": f"{side}_{quantity}", "fee": 0.0}

    async def get_order_status(self, order_id):
        await asyncio.sleep(0)
        return {"status": "FILLED"}

    async def cancel_order(self, order_id):  # pragma: no cover - noop for tests
        await asyncio.sleep(0)
        return {}


def test_concurrent_close(monkeypatch):
    _patch_risk(monkeypatch)
    monkeypatch.setattr("strategies.funding_arbitrage.save_positions", _async_noop)
    test_positions: dict = {}
    monkeypatch.setattr(fa, "positions", test_positions)
    test_positions["BTCUSDT"] = Position(
        entry_timestamp=0.0,
        entry_futures_price=1.0,
        entry_spot_price=1.0,
        entry_basis=0.0,
        entry_funding=0.0,
        quantity=1.0,
        initial_quantity=1.0,
        exchange="dummy",
    )
    exchange = DummyExchange()

    async def runner():
        async def close_task():
            await close_neutral_position(
                exchange, "BTCUSDT", Decimal("1"), Decimal("0"), final=True
            )

        await asyncio.gather(close_task(), close_task())

    asyncio.run(runner())
    assert "BTCUSDT" not in test_positions
