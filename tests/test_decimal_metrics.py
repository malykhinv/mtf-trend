import sys
import pathlib
import types
from decimal import Decimal

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub heavy dependencies
ai_module = types.ModuleType("ai")
parameter_optimizer = types.ModuleType("parameter_optimizer")

def load_thresholds(defaults):
    return defaults

async def periodic_optimization():
    return None

parameter_optimizer.load_thresholds = load_thresholds
parameter_optimizer.periodic_optimization = periodic_optimization
ai_module.parameter_optimizer = parameter_optimizer
sys.modules["ai"] = ai_module
sys.modules["ai.parameter_optimizer"] = parameter_optimizer

from strategies import funding_arbitrage as fa


@pytest.mark.asyncio
async def test_get_market_metrics_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dummy_fetch_funding_history(symbol, hours=8, limit=3):
        return [0.01]

    async def dummy_get_orderbook(symbol, depth=5):
        return {"bids": [[100.0, 5.0]], "asks": [[101.0, 5.0]]}

    async def dummy_get_spot_orderbook(symbol, depth=5):
        return {"bids": [[99.0, 5.0]], "asks": [[100.0, 5.0]]}

    async def dummy_get_stats(symbol):
        return {"volume_24h": 1000, "open_interest": 100}

    async def dummy_get_ohlc(symbol, interval="15m", limit=1):
        return [{"open": 100.0, "close": 100.0}]

    monkeypatch.setattr(fa, "fetch_funding_history", dummy_fetch_funding_history)
    monkeypatch.setattr(fa, "get_orderbook", dummy_get_orderbook)
    monkeypatch.setattr(fa, "get_spot_orderbook", dummy_get_spot_orderbook)
    monkeypatch.setattr(fa, "get_stats", dummy_get_stats)
    monkeypatch.setattr(fa, "get_ohlc", dummy_get_ohlc)

    metrics = await fa.get_market_metrics("BTCUSDT", trade_size=1)
    assert isinstance(metrics.spread, Decimal)
    assert isinstance(metrics.liquidity, Decimal)
    assert isinstance(metrics.spot_price, Decimal)
    assert isinstance(metrics.futures_price, Decimal)
    assert isinstance(metrics.volume, Decimal)
    assert isinstance(metrics.open_interest, Decimal)
    assert metrics.spread == Decimal("1")
    assert metrics.liquidity == Decimal("10")
    assert metrics.spot_price == Decimal("99.5")
    assert metrics.futures_price == Decimal("100.5")
    assert metrics.volume == Decimal("1000")
    assert metrics.open_interest == Decimal("10050")


@pytest.mark.asyncio
async def test_monitor_neutral_position_decimal(monkeypatch: pytest.MonkeyPatch) -> None:
    close_called = {}

    async def dummy_close(exchange, symbol, quantity, pnl, final=True):
        close_called["quantity"] = quantity
        close_called["pnl"] = pnl
        return {"commission": 0}

    async def dummy_log_trade(*args, **kwargs):
        return None

    async def dummy_save_positions(*args, **kwargs):
        return None

    monkeypatch.setattr(fa, "close_neutral_position", dummy_close)
    monkeypatch.setattr(fa, "log_trade", dummy_log_trade)
    monkeypatch.setattr(fa, "save_positions", dummy_save_positions)
    monkeypatch.setattr(fa.risk_control, "record_pnl", lambda *a, **k: None)
    monkeypatch.setattr(fa.risk_control, "update_position", lambda *a, **k: None)
    monkeypatch.setattr(fa.risk_control, "mark_symbol_closed", lambda *a, **k: None)

    metrics = fa.MarketMetrics(
        funding_rate=Decimal("0.01"),
        spread=Decimal("0"),
        liquidity=Decimal("1"),
        volatility=0.0,
        spot_price=Decimal("100"),
        futures_price=Decimal("101"),
        volume=Decimal("1000"),
        open_interest=Decimal("500"),
        spot_slippage=Decimal("0"),
        futures_slippage=Decimal("0"),
        slippage=Decimal("0"),
        basis=Decimal("0"),
    )

    async def fake_get_market_metrics(symbol, qty, exchange):
        return metrics

    monkeypatch.setattr(fa, "get_market_metrics", fake_get_market_metrics)

    entry = fa.Position(
        entry_timestamp=0.0,
        entry_futures_price=Decimal("100.0"),
        entry_spot_price=Decimal("100.0"),
        entry_basis=Decimal("0.0"),
        entry_funding=Decimal("0.01"),
        quantity=Decimal("1.0"),
        initial_quantity=Decimal("1.0"),
    )
    fa.positions["BTCUSDT"] = entry

    await fa.monitor_neutral_position(
        types.SimpleNamespace(),
        "BTCUSDT",
        1.0,
        {"funding_rate": 0.02},
        poll_interval=0,
    )

    assert isinstance(close_called["quantity"], Decimal)
    assert isinstance(close_called["pnl"], Decimal)
    assert close_called["pnl"] == Decimal("1")

    fa.positions.clear()
