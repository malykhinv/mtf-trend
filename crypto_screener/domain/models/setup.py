from abc import ABC
from dataclasses import dataclass, field
from typing import Optional

from crypto_screener.domain.models.swing import Swing


@dataclass(frozen=True)
class Setup(ABC):
    name: str = field(init=False)

    # Поля конструктора.
    main_high_swing: Optional[Swing]
    cascade_swings: list[Swing]
    resistance_swings: list[Swing]
    support_swings: list[Swing]


@dataclass(frozen=True)
class Capture(Setup):
    name: str = field(init=False, default="Capture")


@dataclass(frozen=True)
class Buy(Setup):
    name: str = field(init=False, default="Buy")
    take_profit_price: float
    stop_loss_price: float
    partial_close_price: Optional[float]
    breakeven_price: Optional[float]

    def __post_init__(self) -> None:
        take_profit_price = self.take_profit_price
        stop_loss_price = self.stop_loss_price
        partial_close_price = self.partial_close_price
        breakeven_price = self.breakeven_price

        # Проверка соответствия опциональных полей.
        if partial_close_price is None and breakeven_price is not None:
            raise ValueError("Не задана цена PC.")
        if partial_close_price is not None and breakeven_price is None:
            raise ValueError("Не задана цена BE.")

        # Проверка знака.
        if take_profit_price <= 0:
            raise ValueError(f"Цена TP должна быть положительной ({take_profit_price}).")
        if stop_loss_price <= 0:
            raise ValueError(f"Цена SL должна быть положительной ({stop_loss_price}).")
        if partial_close_price is not None and partial_close_price <= 0:
            raise ValueError(f"Цена PC должна быть положительной ({partial_close_price}).")
        if breakeven_price is not None and breakeven_price <= 0:
            raise ValueError(f"Цена BE должна быть положительной ({breakeven_price}).")

        # Проверка соотношений.
        if partial_close_price is not None and breakeven_price is not None:
            if not stop_loss_price < breakeven_price < partial_close_price < take_profit_price:
                raise ValueError("Неправильное соотношение цен SL -> BE -> PC -> TP.")
        else:
            if not stop_loss_price < take_profit_price:
                raise ValueError("Неправильное соотношение цен SL -> TP.")
