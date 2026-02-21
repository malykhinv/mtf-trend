"""Переиспользуемая механика TP1 -> BE+ -> TP2/trailing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from constants import TP1_CLOSE_RATIO
from domain.enums.position_side import PositionSide
from domain.models.candle import Candle
from domain.models.position import Position


@dataclass(frozen=True, slots=True)
class ExitManagerConfig:
    tp1_close_ratio: float = TP1_CLOSE_RATIO
    be_plus_offset_ratio: float = 0.0
    trailing_offset_ratio: float | None = None


@dataclass(slots=True)
class ExitManager:
    config: ExitManagerConfig

    def process(
        self,
        *,
        candle: Candle,
        position: Position,
        side: PositionSide,
        close_leg: Callable[[float, float], None],
        breakeven_price: Callable[[float, PositionSide], float],
        update_stop: Callable[[float], None],
    ) -> tuple[float | None, bool]:
        if side == PositionSide.LONG:
            return self._process_long(
                candle=candle,
                position=position,
                close_leg=close_leg,
                breakeven_price=breakeven_price,
                update_stop=update_stop,
            )
        return self._process_short(
            candle=candle,
            position=position,
            close_leg=close_leg,
            breakeven_price=breakeven_price,
            update_stop=update_stop,
        )

    def _process_long(
        self,
        *,
        candle: Candle,
        position: Position,
        close_leg: Callable[[float, float], None],
        breakeven_price: Callable[[float, PositionSide], float],
        update_stop: Callable[[float], None],
    ) -> tuple[float | None, bool]:
        if candle.low.value <= position.stop_loss.value:
            return position.stop_loss.value, position.sl_moved_to_be

        if not position.tp1_done and candle.high.value >= position.take_profit_1.value:
            tp1_size = position.size.value * self.config.tp1_close_ratio
            close_leg(tp1_size, position.take_profit_1.value)
            position.tp1_done = True
            position.sl_moved_to_be = True
            be = breakeven_price(position.entry_price.value, PositionSide.LONG)
            be_plus = be * (1 + self.config.be_plus_offset_ratio)
            update_stop(be_plus)
            if candle.low.value <= position.stop_loss.value:
                return position.stop_loss.value, True

        self._maybe_update_trailing_stop(candle=candle, position=position, side=PositionSide.LONG, update_stop=update_stop)

        if candle.low.value <= position.stop_loss.value:
            return position.stop_loss.value, True
        if candle.high.value >= position.take_profit_2.value:
            return position.take_profit_2.value, False
        return None, False

    def _process_short(
        self,
        *,
        candle: Candle,
        position: Position,
        close_leg: Callable[[float, float], None],
        breakeven_price: Callable[[float, PositionSide], float],
        update_stop: Callable[[float], None],
    ) -> tuple[float | None, bool]:
        if candle.high.value >= position.stop_loss.value:
            return position.stop_loss.value, position.sl_moved_to_be

        if not position.tp1_done and candle.low.value <= position.take_profit_1.value:
            tp1_size = position.size.value * self.config.tp1_close_ratio
            close_leg(tp1_size, position.take_profit_1.value)
            position.tp1_done = True
            position.sl_moved_to_be = True
            be = breakeven_price(position.entry_price.value, PositionSide.SHORT)
            be_plus = be * (1 - self.config.be_plus_offset_ratio)
            update_stop(be_plus)
            if candle.high.value >= position.stop_loss.value:
                return position.stop_loss.value, True

        self._maybe_update_trailing_stop(candle=candle, position=position, side=PositionSide.SHORT, update_stop=update_stop)

        if candle.high.value >= position.stop_loss.value:
            return position.stop_loss.value, True
        if candle.low.value <= position.take_profit_2.value:
            return position.take_profit_2.value, False
        return None, False

    def _maybe_update_trailing_stop(
        self,
        *,
        candle: Candle,
        position: Position,
        side: PositionSide,
        update_stop: Callable[[float], None],
    ) -> None:
        trailing = self.config.trailing_offset_ratio
        if trailing is None or not position.tp1_done:
            return
        if side == PositionSide.LONG:
            candidate = candle.high.value * (1 - trailing)
            if candidate > position.stop_loss.value:
                update_stop(candidate)
            return
        candidate = candle.low.value * (1 + trailing)
        if candidate < position.stop_loss.value:
            update_stop(candidate)
