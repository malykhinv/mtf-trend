import pathlib
import sys
from decimal import Decimal
import types

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Подменяем зависимость sklearn, чтобы избежать тяжёлой установки
linear_model_stub = types.SimpleNamespace(LinearRegression=object)
sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
sys.modules.setdefault("sklearn", sklearn_stub)
sys.modules.setdefault("sklearn.linear_model", linear_model_stub)

from strategies import funding_arbitrage as fa
from exchanges import BaseExchange


class PartialFillExchange(BaseExchange):
    name = "stub"

    def __init__(self) -> None:
        self.orders: list[tuple[str, str, float]] = []
        self.cancelled: list[str] = []

    async def fetch_funding(self, symbol: str) -> float:  # pragma: no cover - unused
        return 0.0

    async def place_order(self, symbol: str, side: str, quantity: float, price: float | None = None) -> dict:
        self.orders.append(("perp", side, quantity))
        return {"id": "perp1"}

    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:  # pragma: no cover - unused
        return {}

    async def get_balance(self) -> dict:  # pragma: no cover - unused
        return {}

    async def place_spot_order(self, symbol: str, side: str, quantity: float, price: float | None = None) -> dict:
        self.orders.append(("spot", side, quantity))
        return {"id": "spot1"}

    async def get_spot_orderbook(self, symbol: str, depth: int = 5) -> dict:  # pragma: no cover - unused
        return {}

    async def get_spot_balance(self) -> dict:  # pragma: no cover - unused
        return {}

    async def fetch_funding_history(self, symbol: str, hours: int = 8, limit: int = 3) -> list[float]:  # pragma: no cover - unused
        return []

    async def get_stats(self, symbol: str) -> dict:  # pragma: no cover - unused
        return {"volume_24h": 0, "open_interest": 0}

    async def get_futures_symbols(self) -> list[str]:  # pragma: no cover - unused
        return []

    async def get_spot_symbols(self) -> list[str]:  # pragma: no cover - unused
        return []

    async def get_ohlc(self, symbol: str, interval: str = "15m", limit: int = 1) -> list[dict]:  # pragma: no cover - unused
        return [{"open": 0, "close": 0, "high": 0, "low": 0}]

    async def get_order_status(self, order_id: str) -> dict:
        if order_id == "spot1":
            return {"status": "FILLED", "order_id": order_id}
        return {"status": "PARTIALLY_FILLED", "order_id": order_id}

    async def cancel_order(self, symbol: str, order_id: str) -> dict:
        self.cancelled.append((symbol, order_id))
        return {"status": "CANCELED", "order_id": order_id}


@pytest.mark.asyncio
async def test_open_neutral_position_partial_fill(monkeypatch: pytest.MonkeyPatch) -> None:
    exchange = PartialFillExchange()

    # Настраиваем окружение
    monkeypatch.setattr(fa, "CONFIG", {"bot": {"deposit_size": 1000}, "thresholds": {}})
    monkeypatch.setattr(fa, "WHITELISTS", {exchange.name: ["BTCUSDT"]})
    async def _true(*args, **kwargs):
        return True

    async def _false(*args, **kwargs):
        return False

    async def _noop(*args, **kwargs):
        return None

    monkeypatch.setattr(fa.risk_control, "try_open_position", _true)
    monkeypatch.setattr(fa.risk_control, "update_position", _noop)
    monkeypatch.setattr(fa.risk_control, "mark_symbol_closed", _noop)

    metrics = fa.MarketMetrics(
        funding_rate=Decimal("0"),
        spread=Decimal("0"),
        liquidity=Decimal("0"),
        volatility=0.0,
        spot_price=Decimal("100"),
        futures_price=Decimal("100"),
        volume=Decimal("0"),
        open_interest=Decimal("0"),
        spot_slippage=Decimal("0"),
        futures_slippage=Decimal("0"),
        slippage=Decimal("0"),
        basis=Decimal("0"),
    )

    async def _fake_metrics(symbol: str, trade_size: float, exchange: BaseExchange) -> fa.MarketMetrics:
        return metrics

    monkeypatch.setattr(fa, "get_market_metrics", _fake_metrics)

    quantity = Decimal("1")
    with pytest.raises(RuntimeError):
        await fa.open_neutral_position(exchange, "BTCUSDT", quantity)

    # Первая попытка покупки спота, затем продажа для отката
    assert exchange.orders == [
        ("spot", "BUY", 1.0),
        ("perp", "SELL", 1.0),
        ("spot", "SELL", 1.0),
    ]
    assert exchange.cancelled == [("BTCUSDT", "perp1")]
