import sys
import pathlib
import asyncio
import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

from domain.services.trade_manager import TradeManager
from domain.services.risk_manager import RiskManager
from domain.services.symbol_registry import SymbolRegistry
from domain.models.trading import PositionPlan
from domain.models.state import SymbolState
from domain.models.enums import Side, BotState
from config.profiles.balanced import make_profile_config_balanced


class FailingTrader:
    async def place(self, order):
        raise RuntimeError("failed")


def test_open_position_releases_margin_on_error():
    cfg = make_profile_config_balanced()
    risk = RiskManager(cfg)
    registry = SymbolRegistry()
    registry.put(SymbolState(symbol="BTCUSDT", state=BotState.IDLE))
    tm = TradeManager(FailingTrader(), risk, registry)
    plan = PositionPlan(
        symbol="BTCUSDT",
        entry_price=200.0,
        stop_loss=190.0,
        take_profit1=180.0,
        take_profit2=170.0,
        trail_start=175.0,
        trail_distance=5.0,
        quantity=0.5,
        tp1_qty=0.2,
        tp2_qty=0.2,
        tail_qty=0.1,
        window_high=210.0,
    )

    assert risk.allow_trade(plan)
    with pytest.raises(RuntimeError):
        asyncio.run(tm.open_position(plan, Side.SHORT))
    assert risk._open_risk_usdt == 0.0
