import logging
from decimal import Decimal
from pathlib import Path
import sys

import pytest

sys.path.append(str(Path(__file__).resolve().parents[1]))
import main
from strategies import funding_arbitrage as fa


def test_parse_deposit_value_invalid(caplog):
    with caplog.at_level(logging.ERROR):
        assert main._parse_deposit_value("oops") is None
    assert "Некорректное значение депозита" in caplog.text


@pytest.mark.asyncio
async def test_open_neutral_position_invalid_deposit(monkeypatch, caplog):
    class DummyExchange:
        name = "dummy"

    async def fake_metrics(symbol, quantity, exchange):
        class Metrics:
            futures_price = Decimal("100")
        return Metrics()

    monkeypatch.setattr(fa, "get_market_metrics", fake_metrics)

    config = {"bot": {"deposit_size": "oops"}, "thresholds": {}}
    whitelists = {"dummy": ["BTCUSDT"]}
    quantity = Decimal("1")
    exchange = DummyExchange()

    with caplog.at_level(logging.ERROR):
        with pytest.raises(RuntimeError):
            await fa.open_neutral_position(
                exchange, "BTCUSDT", quantity, config, whitelists
            )
    assert "Некорректное значение депозита" in caplog.text
