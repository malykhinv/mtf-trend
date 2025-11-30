from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TradeLevels:
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    partial_close_price: Optional[float] = None
    breakeven_price: Optional[float] = None

    def __post_init__(self) -> None:
        take_profit_price = self.take_profit_price
        stop_loss_price = self.stop_loss_price
        entry_price = self.entry_price
        partial_close_price = self.partial_close_price
        breakeven_price = self.breakeven_price

        if partial_close_price is None and breakeven_price is not None:
            raise ValueError("Не задана цена PC.")
        if partial_close_price is not None and breakeven_price is None:
            raise ValueError("Не задана цена BE.")

        if take_profit_price <= 0:
            raise ValueError(f"Цена TP должна быть положительной ({take_profit_price}).")
        if stop_loss_price <= 0:
            raise ValueError(f"Цена SL должна быть положительной ({stop_loss_price}).")
        if entry_price <= 0:
            raise ValueError(f"Цена входа должна быть положительной ({entry_price}).")
        if partial_close_price is not None and partial_close_price <= 0:
            raise ValueError(f"Цена PC должна быть положительной ({partial_close_price}).")
        if breakeven_price is not None and breakeven_price <= 0:
            raise ValueError(f"Цена BE должна быть положительной ({breakeven_price}).")

        if partial_close_price is not None and breakeven_price is not None:
            if not stop_loss_price < entry_price < breakeven_price < partial_close_price < take_profit_price:
                raise ValueError("Неправильное соотношение цен SL -> BE -> PC -> TP.")
        else:
            if not stop_loss_price < entry_price < take_profit_price:
                raise ValueError("Неправильное соотношение цен SL -> TP.")
