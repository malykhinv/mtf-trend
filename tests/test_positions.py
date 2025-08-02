import json
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

from strategies.funding_arbitrage import (
    Position,
    positions,
    save_positions,
    load_positions,
)
from risk import risk_control


def _patch_risk(monkeypatch):
    monkeypatch.setattr(risk_control, "update_position", lambda *_: None)
    monkeypatch.setattr(risk_control, "mark_symbol_open", lambda *_: None)


def test_save_and_load_roundtrip(tmp_path, monkeypatch):
    _patch_risk(monkeypatch)
    file_path = tmp_path / "open_positions.json"
    positions.clear()
    pos = Position(
        entry_timestamp=1.0,
        entry_futures_price=2.0,
        entry_spot_price=1.5,
        entry_basis=0.5,
        entry_funding=0.01,
        quantity=3.0,
        initial_quantity=3.0,
        commissions=Decimal("0.1"),
        last_funding_timestamp=1.0,
        exchange="binance",
    )
    positions["BTCUSDT"] = pos
    asyncio.run(save_positions(file_path))
    positions.clear()
    asyncio.run(load_positions(file_path))
    assert "BTCUSDT" in positions
    loaded = positions["BTCUSDT"]
    assert isinstance(loaded, Position)
    assert loaded.quantity == 3.0
    assert loaded.commissions == Decimal("0.1")
    assert isinstance(loaded.pnl, Decimal)
    assert loaded.pnl == Decimal(0)


def test_load_positions_validation(tmp_path, monkeypatch):
    _patch_risk(monkeypatch)
    data = {
        "GOOD": {"entry_timestamp": 1.0, "quantity": 1.0, "commissions": 0.0},
        "BAD": {"entry_timestamp": 1.0},
    }
    file_path = tmp_path / "open_positions.json"
    file_path.write_text(json.dumps(data))
    positions.clear()
    asyncio.run(load_positions(file_path))
    assert "GOOD" in positions
    assert "BAD" not in positions


def test_decimal_accumulation():
    pos = Position(
        entry_timestamp=0.0,
        entry_futures_price=0.0,
        entry_spot_price=0.0,
        entry_basis=0.0,
        entry_funding=0.0,
        quantity=0.0,
        initial_quantity=0.0,
        commissions=Decimal(0),
        last_funding_timestamp=0.0,
        exchange="",
    )
    for _ in range(10):
        pos.commissions += Decimal("0.1")
        pos.funding_accrued += Decimal("0.1")
    assert pos.commissions == Decimal("1.0")
    assert pos.funding_accrued == Decimal("1.0")
