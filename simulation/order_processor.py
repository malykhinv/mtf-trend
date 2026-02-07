"""Order execution and transaction cost calculations for simulation."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.position_side import PositionSide


@dataclass(frozen=True, slots=True)
class Fill:
    """Single order fill snapshot."""

    price: float
    commission: float


@dataclass(frozen=True, slots=True)
class OrderProcessor:
    """Calculates execution prices and trading costs."""

    commission_rate: float
    slippage: float

    def execute_entry(self, candle_open: float, side: PositionSide, size: float) -> Fill:
        """Execute market entry at candle open with adverse slippage and commission."""
        is_buy = side == PositionSide.LONG
        fill_price = self._apply_slippage(candle_open, is_buy=is_buy)
        return Fill(price=fill_price, commission=self._commission(fill_price * size))

    def execute_exit(self, target_price: float, side: PositionSide, size: float) -> Fill:
        """Execute market exit at target level with adverse slippage and commission."""
        is_buy = side == PositionSide.SHORT
        fill_price = self._apply_slippage(target_price, is_buy=is_buy)
        return Fill(price=fill_price, commission=self._commission(fill_price * size))

    def breakeven_price(self, entry_price: float, side: PositionSide) -> float:
        """Calculate breakeven level including commissions and slippage reserve."""
        reserve = 2 * self.commission_rate + self.slippage
        if side == PositionSide.LONG:
            return entry_price * (1 + reserve)
        return entry_price * (1 - reserve)

    def _apply_slippage(self, price: float, *, is_buy: bool) -> float:
        multiplier = 1 + self.slippage if is_buy else 1 - self.slippage
        return price * multiplier

    def _commission(self, notional: float) -> float:
        return notional * self.commission_rate
