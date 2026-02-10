"""Исполнение ордеров и расчет транзакционных издержек в симуляции."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide
from simulation.models.fill import Fill


@dataclass(frozen=True, slots=True)
class OrderProcessor:
    """Рассчитывает цены исполнения и торговые издержки."""

    commission_rate: float
    slippage: float

    # region Приватные

    def _apply_slippage(self, price: float, *, is_buy: bool) -> float:
        multiplier = 1 + self.slippage if is_buy else 1 - self.slippage
        return price * multiplier

    def _commission(self, notional: float) -> float:
        return notional * self.commission_rate

    # endregion Приватные

    def execute_entry(self, candle_open: float, side: PositionSide, size: float) -> Fill:
        """Исполняет вход по рынку на открытии свечи с неблагоприятным проскальзыванием и комиссией."""
        is_buy = side == PositionSide.LONG
        fill_price = self._apply_slippage(candle_open, is_buy=is_buy)
        return Fill(price=fill_price, commission=self._commission(fill_price * size))

    def execute_exit(self, target_price: float, side: PositionSide, size: float) -> Fill:
        """Исполняет выход по рынку на целевом уровне с неблагоприятным проскальзыванием и комиссией."""
        is_buy = side == PositionSide.SHORT
        fill_price = self._apply_slippage(target_price, is_buy=is_buy)
        return Fill(price=fill_price, commission=self._commission(fill_price * size))

    def breakeven_price(self, entry_price: float, side: PositionSide) -> float:
        """Вычисляет уровень безубытка с учетом комиссий и резерва на проскальзывание."""
        reserve = 2 * self.commission_rate + self.slippage
        if side == PositionSide.LONG:
            return entry_price * (1 + reserve)
        return entry_price * (1 - reserve)
