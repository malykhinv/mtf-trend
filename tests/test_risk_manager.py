import asyncio
import logging
import pathlib
import sys

import httpx
import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from config.profiles.balanced import make_profile_config_balanced
from domain.models.metrics import PumpWindow
from domain.models.trading import PositionPlan
from domain.services.risk_manager import RiskManager


class DummyHTTPClient:
    def __init__(self, filters):
        self._filters = filters

    async def get(self, url, params=None):
        class Resp:
            def __init__(self, filters):
                self._filters = filters

            def raise_for_status(self):
                pass

            def json(self):
                return {"symbols": [{"filters": self._filters}]}

        return Resp(self._filters)

    async def aclose(self):
        pass


class ErrorHTTPClient:
    async def get(self, url, params=None):
        raise httpx.HTTPError("boom")

    async def aclose(self):
        pass


def _make_plan(symbol="BTCUSDT", entry_price=200.0, quantity=1.0):
    return PositionPlan(
        symbol=symbol,
        entry_price=entry_price,
        stop_loss=190.0,
        take_profit1=180.0,
        take_profit2=170.0,
        trail_start=175.0,
        trail_distance=5.0,
        quantity=quantity,
        tp1_qty=0.4,
        tp2_qty=0.3,
        tail_qty=0.3,
        window_high=210.0,
    )


def test_build_plan_logs_http_error(caplog):
    cfg = make_profile_config_balanced()
    risk = RiskManager(cfg, http_client=ErrorHTTPClient())
    window = PumpWindow(high=110.0, low=100.0, start_ts=0, end_ts=0)
    with caplog.at_level(logging.ERROR):
        plan = asyncio.run(risk.build_plan("BTCUSDT", 105.0, window))
    assert plan is None
    assert "Failed to fetch symbol filters" in caplog.text
    asyncio.run(risk.aclose())


def test_build_plan_logs_levels(caplog):
    cfg = make_profile_config_balanced()
    filters = [
        {"filterType": "LOT_SIZE", "stepSize": "0.001"},
        {"filterType": "PRICE_FILTER", "tickSize": "0.01"},
    ]
    risk = RiskManager(cfg, http_client=DummyHTTPClient(filters))
    window = PumpWindow(high=110.0, low=100.0, start_ts=0, end_ts=0)
    with caplog.at_level(logging.INFO):
        plan = asyncio.run(risk.build_plan("BTCUSDT", 105.0, window))
    assert plan is not None
    text = caplog.text
    assert f"{plan.stop_loss:.4f}" in text
    assert f"{plan.take_profit1:.4f}" in text
    assert f"{plan.take_profit2:.4f}" in text
    assert f"{plan.trail_start:.4f}" in text
    assert f"{plan.trail_distance:.4f}" in text
    asyncio.run(risk.aclose())


def test_allow_trade_logs_margin_and_reason(caplog):
    cfg = make_profile_config_balanced()
    risk = RiskManager(cfg)
    plan = _make_plan()
    with caplog.at_level(logging.INFO):
        allowed = risk.allow_trade(plan)
    assert not allowed
    text = caplog.text
    assert f"{plan.entry_price * plan.quantity:.2f}" in text
    assert "per-trade limit" in text
