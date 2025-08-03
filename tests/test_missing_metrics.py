import sys
import pathlib
from decimal import Decimal
import types

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub sklearn to avoid heavy dependency
linear_model_stub = types.SimpleNamespace(LinearRegression=object)
sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
sys.modules.setdefault("sklearn", sklearn_stub)
sys.modules.setdefault("sklearn.linear_model", linear_model_stub)

from strategies import funding_arbitrage as fa
from exchanges import BaseExchange


class NoCallExchange(BaseExchange):
    name = "stub"

    def __init__(self) -> None:
        self.orders = []

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
async def test_open_neutral_position_skips_when_no_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = NoCallExchange()

    monkeypatch.setattr(fa, "CONFIG", {"bot": {"deposit_size": 1000}, "thresholds": {}})
    monkeypatch.setattr(fa, "WHITELISTS", {exchange.name: ["BTCUSDT"]})
    monkeypatch.setattr(fa.risk_control, "try_open_position", lambda *_: True)
    monkeypatch.setattr(fa.risk_control, "update_position", lambda *_: None)
    monkeypatch.setattr(fa.risk_control, "mark_symbol_closed", lambda *_: None)

    async def _metrics_none(symbol: str, trade_size: float, exchange: BaseExchange) -> fa.MarketMetrics | None:
        return None

    monkeypatch.setattr(fa, "get_market_metrics", _metrics_none)

    with pytest.raises(RuntimeError):
        await fa.open_neutral_position(exchange, "BTCUSDT", Decimal("1"))

    assert exchange.orders == []
