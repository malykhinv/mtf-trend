import logging
import pathlib
import sys

import types

from decimal import Decimal

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub ``ai.parameter_optimizer`` to avoid heavy dependencies during import
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

from strategies.funding_arbitrage import (
    MarketMetrics,
    check_entry_conditions,
    check_exit_conditions,
    get_market_metrics,
)


@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    async def dummy_fetch_funding_history(symbol, hours=8, limit=3):
        return []

    async def dummy_get_orderbook(symbol, depth=5):
        return {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]}

    async def dummy_get_spot_orderbook(symbol, depth=5):
        return {"bids": [[100.0, 1.0]], "asks": [[101.0, 1.0]]}

    async def dummy_get_stats(symbol):
        return {"volume_24h": 0, "open_interest": 0}

    async def dummy_get_ohlc(symbol, interval="15m", limit=1):
        return [{"open": 1, "close": 1}]

    monkeypatch.setattr(
        "strategies.funding_arbitrage.fetch_funding_history",
        dummy_fetch_funding_history,
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_orderbook", dummy_get_orderbook
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_spot_orderbook",
        dummy_get_spot_orderbook,
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_stats", dummy_get_stats
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_ohlc", dummy_get_ohlc
    )
    async def _is_open(symbol):
        return False
    monkeypatch.setattr(
        "strategies.funding_arbitrage.risk_control.is_symbol_open", _is_open
    )


@pytest.mark.asyncio
async def test_slippage_zero_volume() -> None:
    metrics = await get_market_metrics("BTCUSDT", trade_size=0)
    assert metrics.spot_slippage.is_infinite()
    assert metrics.futures_slippage.is_infinite()
    assert metrics.slippage.is_infinite()


@pytest.mark.asyncio
async def test_slippage_negative_volume() -> None:
    metrics = await get_market_metrics("BTCUSDT", trade_size=-1)
    assert metrics.spot_slippage.is_infinite()
    assert metrics.futures_slippage.is_infinite()
    assert metrics.slippage.is_infinite()


@pytest.mark.asyncio
async def test_slippage_zero_mid(monkeypatch: pytest.MonkeyPatch) -> None:
    async def zero_price_orderbook(symbol, depth=5):
        return {"bids": [[0.0, 1.0]], "asks": [[0.0, 1.0]]}

    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_orderbook", zero_price_orderbook
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_spot_orderbook", zero_price_orderbook
    )

    metrics = await get_market_metrics("BTCUSDT", trade_size=1)
    assert metrics.spot_slippage.is_infinite()
    assert metrics.futures_slippage.is_infinite()
    assert metrics.slippage.is_infinite()


@pytest.mark.asyncio
async def test_returns_none_on_empty_orderbook(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_orderbook(symbol, depth=5):
        return {"bids": [], "asks": []}

    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_orderbook", empty_orderbook
    )
    monkeypatch.setattr(
        "strategies.funding_arbitrage.get_spot_orderbook", empty_orderbook
    )
    metrics = await get_market_metrics("BTCUSDT", trade_size=1)
    assert metrics is None


@pytest.mark.asyncio
async def test_returns_none_on_missing_stats(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_stats(symbol):
        return {}

    monkeypatch.setattr("strategies.funding_arbitrage.get_stats", empty_stats)
    metrics = await get_market_metrics("BTCUSDT", trade_size=1)
    assert metrics is None


@pytest.mark.asyncio
async def test_returns_none_on_missing_ohlc(monkeypatch: pytest.MonkeyPatch) -> None:
    async def empty_ohlc(symbol, interval="15m", limit=1):
        return []

    monkeypatch.setattr("strategies.funding_arbitrage.get_ohlc", empty_ohlc)
    metrics = await get_market_metrics("BTCUSDT", trade_size=1)
    assert metrics is None


@pytest.mark.asyncio
async def test_check_entry_conditions_none_metrics(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        result = await check_entry_conditions("BTCUSDT", Decimal("1"), None, {})
    assert not result
    assert "incomplete" in caplog.text.lower()


def test_exit_on_non_finite_volatility() -> None:
    metrics = MarketMetrics(
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        float("inf"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        0.0,
        0.0,
        0.0,
        0.0,
    )
    assert check_exit_conditions(metrics, {})
    metrics.volatility = float("nan")
    assert check_exit_conditions(metrics, {})


def _sample_metrics() -> MarketMetrics:
    return MarketMetrics(
        Decimal("0.1"),
        Decimal("0"),
        Decimal("1"),
        0.0,
        Decimal("100"),
        Decimal("100"),
        Decimal("100"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        0.0,
    )


def test_check_entry_conditions_invalid_thresholds(caplog: pytest.LogCaptureFixture) -> None:
    metrics = _sample_metrics()
    thresholds = {"funding_rate": float("nan"), "liquidity": float("nan")}
    with caplog.at_level(logging.WARNING):
        result = check_entry_conditions("BTCUSDT", Decimal("1"), metrics, thresholds)
    assert result
    assert "Invalid threshold" in caplog.text


def test_check_entry_conditions_non_finite_metrics(caplog: pytest.LogCaptureFixture) -> None:
    metrics = _sample_metrics()
    metrics.spread = Decimal("NaN")
    with caplog.at_level(logging.WARNING):
        result = check_entry_conditions("BTCUSDT", Decimal("1"), metrics, {})
    assert not result
    assert "non-finite spread" in caplog.text


def test_check_entry_conditions_invalid_quantity(caplog: pytest.LogCaptureFixture) -> None:
    metrics = _sample_metrics()
    quantity = Decimal("NaN")
    with caplog.at_level(logging.WARNING):
        result = check_entry_conditions("BTCUSDT", quantity, metrics, {})
    assert not result
    assert "invalid quantity" in caplog.text
