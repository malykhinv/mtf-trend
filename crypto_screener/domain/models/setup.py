from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.swing import Swing
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.domain.models.trade_levels import TradeLevels


@dataclass
class Setup(ABC):
    name: str = field(init=False)
    is_filled: bool = field(init=False)
    is_trade: bool = field(init=False)

    # Поля конструктора.
    symbol: str
    timeframe: Timeframe
    bars: list[Bar]
    main_low_swing: Optional[Swing]
    main_high_swing: Optional[Swing]
    cascade_swings: Optional[list[Swing]]
    resistance_swings: Optional[list[Swing]]
    support_swings: Optional[list[Swing]]


@dataclass
class Unfilled(Setup):
    name: str = field(init=False, default="Undefined")
    is_filled: bool = field(init=False, default=False)
    is_trade: bool = field(init=False, default=False)


@dataclass
class Capture(Setup):
    name: str = field(init=False, default="Capture")
    is_filled: bool = field(init=False, default=True)
    is_trade: bool = field(init=False, default=False)


@dataclass
class Buy(Setup):
    name: str = field(init=False, default="Buy")
    is_filled: bool = field(init=False, default=True)
    is_trade: bool = field(init=False, default=True)

    trade_levels: TradeLevels

    @property
    def take_profit_price(self) -> float:
        return self.trade_levels.take_profit_price

    @property
    def stop_loss_price(self) -> float:
        return self.trade_levels.stop_loss_price

    @property
    def entry_price(self) -> float:
        return self.trade_levels.entry_price

    @property
    def partial_close_price(self) -> Optional[float]:
        return self.trade_levels.partial_close_price

    @property
    def breakeven_price(self) -> Optional[float]:
        return self.trade_levels.breakeven_price
