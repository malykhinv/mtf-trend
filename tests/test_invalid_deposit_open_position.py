import pathlib
import sys
import types
import logging
from decimal import Decimal

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub sklearn to avoid heavy dependency
linear_model_stub = types.SimpleNamespace(LinearRegression=object)
sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
sys.modules.setdefault("sklearn", sklearn_stub)
sys.modules.setdefault("sklearn.linear_model", linear_model_stub)

from strategies import funding_arbitrage as fa
from exchanges import BaseExchange


class DummyExchange(BaseExchange):
    name = "stub"

    def __init__(self) -> None:
        self.orders: list[tuple[str, str, float]] = []

    async def fetch_funding(self, symbol: str) -> float:
        return 0.0

    async def place_order(self, symbol: str, side: str, quantity: float, price: float | None = None) -> dict:
        self.orders.append(("perp", side, quantity))
        return {"id": "perp1"}

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        return {}

    async def get_balance(self) -> dict:
        return {}

    async def place_spot_order(self, symbol: str, side: str, quantity: float, price: float | None = None) -> dict:
        self.orders.append(("spot", side, quantity))
        return {"id": "spot1"}

    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:
        return {}

    async def get_spot_balance(self) -> dict:
        return {}

    async def fetch_funding_history(self, symbol: str, hours: int = 8, limit: int = 3) -> list[float]:
        return []

    async def get_stats(self, symbol: str) -> dict:
        return {"volume_24h": 0, "open_interest": 0}

    async def get_futures_symbols(self) -> list[str]:
        return []

    async def get_spot_symbols(self) -> list[str]:
        return []

    async def get_ohlc(self, symbol: str, interval: str = "15m", limit: int = 1) -> list[dict]:
        return [{"open": 0, "close": 0, "high": 0, "low": 0}]


@pytest.mark.asyncio
@pytest.mark.parametrize("deposit", [float("inf"), -100])
async def test_open_neutral_position_rejects_invalid_deposit(monkeypatch, caplog, deposit):
    exchange = DummyExchange()

    metrics = fa.MarketMetrics(
        funding_rate=Decimal("0"),
        spread=Decimal("0"),
        liquidity=Decimal("0"),
        volatility=0.0,
        spot_price=Decimal("100"),
        futures_price=Decimal("100"),
        volume=Decimal("0"),
        open_interest=Decimal("0"),
        spot_slippage=0.0,
        futures_slippage=0.0,
        slippage=0.0,
        basis=0.0,
    )

    async def _metrics(symbol: str, trade_size: float, exch: BaseExchange):
        return metrics

    monkeypatch.setattr(fa, "CONFIG", {"bot": {"deposit_size": deposit}, "thresholds": {}})
    monkeypatch.setattr(fa, "WHITELISTS", {exchange.name: ["BTCUSDT"]})
    monkeypatch.setattr(fa, "get_market_metrics", _metrics)

    caplog.set_level(logging.ERROR)
    with pytest.raises(RuntimeError):
        await fa.open_neutral_position(exchange, "BTCUSDT", Decimal("1"))

    assert "Некорректное значение депозита" in caplog.text
    assert exchange.orders == []
