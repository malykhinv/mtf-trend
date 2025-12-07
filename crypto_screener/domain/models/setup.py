from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

from crypto_screener.domain.models.setup_data import SetupData
from crypto_screener.domain.models.trade_levels import TradeLevels


@dataclass
class Setup(ABC):
    name: str = field(init=False)
    is_filled: bool = field(init=False)
    is_trade: bool = field(init=False)

    # Поля конструктора.
    data: SetupData


@dataclass
class Unfilled(Setup):
    name: str = field(init=False, default="Unfilled")
    is_filled: bool = field(init=False, default=False)
    is_trade: bool = field(init=False, default=False)


@dataclass
class Capture(Setup):
    name: str = field(init=False, default="Capture")
    is_filled: bool = field(init=False, default=True)
    is_trade: bool = field(init=False, default=False)


@dataclass
class Trade(Setup):
    name: str = field(init=False, default="Trade")
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
