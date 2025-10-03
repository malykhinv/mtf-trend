from dataclasses import dataclass

from .balance_source import BalanceSource
from .margin_mode import MarginMode
from .stop_trigger import StopTrigger


@dataclass(frozen=True)
class PositionSettings:
    position_fraction: float
    position_min_usdt: int
    balance_refresh_h: int
    balance_source: BalanceSource
    margin_mode: MarginMode
    leverage: int
    stop_trigger: StopTrigger


__all__ = ["PositionSettings"]
