import asyncio
import sys
import types
from pathlib import Path

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
from strategies.funding_arbitrage import Position
from risk import risk_control


async def _noop(*args, **kwargs):
    return None


def _patch_risk(monkeypatch):
    monkeypatch.setattr(risk_control, "update_position", _noop)
    monkeypatch.setattr(risk_control, "mark_symbol_open", _noop)


def test_parallel_load_save(tmp_path, monkeypatch):
    _patch_risk(monkeypatch)
    file_path = tmp_path / "open_positions.json"
    fa.positions.clear()
    fa.positions["BTCUSDT"] = Position(
        entry_timestamp=0.0,
        entry_futures_price=1.0,
        entry_spot_price=1.0,
        entry_basis=0.0,
        entry_funding=0.0,
        quantity=1.0,
        initial_quantity=1.0,
        exchange="test",
    )
    asyncio.run(fa.save_positions(file_path))

    async def writer():
        for _ in range(5):
            async with fa.positions_lock:
                fa.positions["BTCUSDT"].quantity += 1.0
                await fa.save_positions(file_path)

    async def reader():
        for _ in range(5):
            await fa.load_positions(file_path)
    async def runner():
        await asyncio.gather(writer(), reader())

    asyncio.run(runner())
    assert "BTCUSDT" in fa.positions
    assert isinstance(fa.positions["BTCUSDT"], Position)
