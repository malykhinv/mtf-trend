"""Общий риск-менеджер: размер позиции, фильтры риска и лимит портфеля."""

from __future__ import annotations

from dataclasses import dataclass

from constants import STRATEGY_PRICE_EPSILON
from domain.enums.position_side import PositionSide
from domain.models.position import Position
from domain.models.trade_signal import TradeSignal


@dataclass(frozen=True, slots=True)
class RiskConfig:
    deposit: float
    risk_per_trade_pct: float
    portfolio_risk_limit: float
    legacy_r_trade: float | None = None
    min_stop_atr_ratio: float = 0.3


@dataclass(slots=True)
class RiskManager:
    config: RiskConfig

    def risk_amount(self) -> float:
        if self.config.legacy_r_trade is not None:
            return self.config.legacy_r_trade
        return self.config.deposit * self.config.risk_per_trade_pct

    @staticmethod
    def per_unit_risk(*, entry_price: float, stop_loss: float, side: PositionSide) -> float:
        if side == PositionSide.LONG:
            return max(entry_price - stop_loss, STRATEGY_PRICE_EPSILON)
        return max(stop_loss - entry_price, STRATEGY_PRICE_EPSILON)

    def calc_position_size(self, *, signal: TradeSignal) -> float:
        risk = self.per_unit_risk(
            entry_price=signal.entry_price.value,
            stop_loss=signal.stop_loss.value,
            side=signal.position_side,
        )
        return self.risk_amount() / risk

    def check_stop_distance_by_atr(self, *, signal: TradeSignal, atr_bg: float) -> bool:
        if atr_bg <= 0:
            return True
        stop_distance = self.per_unit_risk(
            entry_price=signal.entry_price.value,
            stop_loss=signal.stop_loss.value,
            side=signal.position_side,
        )
        return stop_distance >= self.config.min_stop_atr_ratio * atr_bg

    def total_open_risk(self, positions: list[tuple[Position, PositionSide]]) -> float:
        return sum(
            self.per_unit_risk(
                entry_price=position.entry_price.value,
                stop_loss=position.stop_loss.value,
                side=side,
            )
            * position.size.value
            for position, side in positions
        )

    def can_open_with_portfolio_limit(self, *, active_positions: list[tuple[Position, PositionSide]], signal: TradeSignal) -> bool:
        del signal
        current_risk = self.total_open_risk(active_positions)
        new_trade_risk = self.risk_amount()
        portfolio_risk_limit = self.config.portfolio_risk_limit * self.risk_amount()
        return (current_risk + new_trade_risk) <= portfolio_risk_limit
