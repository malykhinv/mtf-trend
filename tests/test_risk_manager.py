from domain.enums.position_side import PositionSide
from domain.models.position import Position
from domain.models.position_signal import PositionSignal as TradeSignal
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume
from simulation.risk_manager import RiskConfig, RiskManager


def _build_signal() -> TradeSignal:
    return TradeSignal(
        formation_timestamp_ms=1,
        entry_price=Price(100.0),
        entry_timestamp_ms=2,
        stop_loss=Price(99.0),
        take_profit_1=Price(102.0),
        take_profit_2=Price(104.0),
        position_side=PositionSide.LONG,
        symbol="BTC/USDT",
    )


def test_position_size_uses_two_percent_of_deposit() -> None:
    manager = RiskManager(
        RiskConfig(
            deposit=1_000.0,
            risk_per_position_pct=0.02,
            portfolio_risk_limit=3.0,
        )
    )

    size = manager.calc_position_size(signal=_build_signal())

    assert size == 20.0


def test_portfolio_risk_limit_scales_by_risk_unit() -> None:
    manager = RiskManager(
        RiskConfig(
            deposit=1_000.0,
            risk_per_position_pct=0.02,
            portfolio_risk_limit=3.0,
        )
    )
    active_positions = [
        (
            Position(
                entry_price=Price(100.0),
                entry_timestamp_ms=1,
                size=Volume(20.0),
                stop_loss=Price(99.0),
                take_profit_1=Price(102.0),
                take_profit_2=Price(104.0),
            ),
            PositionSide.LONG,
        ),
        (
            Position(
                entry_price=Price(100.0),
                entry_timestamp_ms=2,
                size=Volume(20.0),
                stop_loss=Price(99.0),
                take_profit_1=Price(102.0),
                take_profit_2=Price(104.0),
            ),
            PositionSide.LONG,
        ),
    ]

    assert manager.can_open_with_portfolio_limit(active_positions=active_positions, signal=_build_signal())

    active_positions.append(
        (
            Position(
                entry_price=Price(100.0),
                entry_timestamp_ms=3,
                size=Volume(20.0),
                stop_loss=Price(99.0),
                take_profit_1=Price(102.0),
                take_profit_2=Price(104.0),
            ),
            PositionSide.LONG,
        )
    )

    assert not manager.can_open_with_portfolio_limit(active_positions=active_positions, signal=_build_signal())
