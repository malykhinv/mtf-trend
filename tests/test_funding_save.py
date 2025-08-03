import asyncio
import types
import sys
import pathlib
import time
from decimal import Decimal

import pytest

# Stub sklearn to avoid heavy dependency
linear_model_stub = types.SimpleNamespace(LinearRegression=object)
sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))
sys.modules.setdefault("sklearn", sklearn_stub)
sys.modules.setdefault("sklearn.linear_model", linear_model_stub)

import strategies.funding_arbitrage as fa


@pytest.mark.asyncio
async def test_funding_saved_on_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = fa.Position(
        entry_timestamp=0.0,
        entry_futures_price=100.0,
        entry_spot_price=100.0,
        entry_basis=0.0,
        entry_funding=0.0,
        quantity=1.0,
        initial_quantity=1.0,
        pnl=Decimal(0),
        funding_accrued=Decimal(0),
        commissions=Decimal(0),
        last_funding_timestamp=time.time() - 1,
        exchange="stub",
    )
    fa.positions["BTCUSDT"] = entry

    metrics = types.SimpleNamespace(
        futures_price=Decimal("100"),
        spot_price=Decimal("100"),
        funding_rate=Decimal("0.01"),
        basis=Decimal("0"),
    )

    async def fake_get_market_metrics(symbol, qty, exchange):
        return metrics

    monkeypatch.setattr(fa, "get_market_metrics", fake_get_market_metrics)
    monkeypatch.setattr(fa, "check_exit_conditions", lambda m, t: False)

    fa.CONFIG = {"bot": {"funding_save_interval": 0, "funding_save_threshold": 0}}

    saved: dict[str, Decimal] = {}

    async def fake_save_positions(path=None):
        saved["funding"] = fa.positions["BTCUSDT"].funding_accrued

    monkeypatch.setattr(fa, "save_positions", fake_save_positions)

    async def run():
        await fa.monitor_neutral_position(
            exchange=types.SimpleNamespace(name="stub"),
            symbol="BTCUSDT",
            quantity=1.0,
            exit_thresholds={},
            poll_interval=0,
        )

    task = asyncio.create_task(run())
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert saved["funding"] > Decimal(0)
    fa.positions.clear()
