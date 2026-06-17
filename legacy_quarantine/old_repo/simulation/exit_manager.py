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
    atr_trailing_after_tp1: bool = False


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
        if not position.tp1_done and candle.low.value <= position.stop_loss.value:
            return position.stop_loss.value, False

        if not position.tp1_done and candle.high.value >= position.take_profit_1.value:
            tp1_ratio = position.tp1_close_ratio if position.tp1_close_ratio is not None else self.config.tp1_close_ratio
            tp1_size = position.size.value * tp1_ratio
            close_leg(tp1_size, position.take_profit_1.value)
            position.tp1_done = True
            position.sl_moved_to_be = True
            if self.config.atr_trailing_after_tp1:
                update_stop(position.entry_price.value * 1.001)
                position.highest_close_since_tp1 = candle.close.value
            else:
                be = breakeven_price(position.entry_price.value, PositionSide.LONG)
                update_stop(be * (1 + self.config.be_plus_offset_ratio))

        if not position.tp1_done:
            return None, False

        self._maybe_update_trailing_stop(candle=candle, position=position, side=PositionSide.LONG, update_stop=update_stop)
        if candle.high.value >= position.take_profit_2.value:
            return position.take_profit_2.value, False
        if candle.low.value <= position.stop_loss.value:
            return position.stop_loss.value, True
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
        if not position.tp1_done and candle.high.value >= position.stop_loss.value:
            return position.stop_loss.value, False

        if not position.tp1_done and candle.low.value <= position.take_profit_1.value:
            tp1_ratio = position.tp1_close_ratio if position.tp1_close_ratio is not None else self.config.tp1_close_ratio
            tp1_size = position.size.value * tp1_ratio
            close_leg(tp1_size, position.take_profit_1.value)
            position.tp1_done = True
            position.sl_moved_to_be = True
            if self.config.atr_trailing_after_tp1:
                update_stop(position.entry_price.value * 0.999)
                position.lowest_close_since_tp1 = candle.close.value
            else:
                be = breakeven_price(position.entry_price.value, PositionSide.SHORT)
                update_stop(be * (1 - self.config.be_plus_offset_ratio))

        if not position.tp1_done:
            return None, False

        self._maybe_update_trailing_stop(candle=candle, position=position, side=PositionSide.SHORT, update_stop=update_stop)
        if candle.low.value <= position.take_profit_2.value:
            return position.take_profit_2.value, False
        if candle.high.value >= position.stop_loss.value:
            return position.stop_loss.value, True
        return None, False

    def _maybe_update_trailing_stop(
        self,
        *,
        candle: Candle,
        position: Position,
        side: PositionSide,
        update_stop: Callable[[float], None],
    ) -> None:
        if self.config.atr_trailing_after_tp1 and position.tp1_done and position.atr_bg is not None and position.atr_bg > 0:
            if side == PositionSide.LONG:
                if position.highest_close_since_tp1 is None:
                    position.highest_close_since_tp1 = candle.close.value
                else:
                    position.highest_close_since_tp1 = max(position.highest_close_since_tp1, candle.close.value)
                candidate = position.highest_close_since_tp1 - position.atr_bg
                if candidate > position.stop_loss.value:
                    update_stop(candidate)
            else:
                if position.lowest_close_since_tp1 is None:
                    position.lowest_close_since_tp1 = candle.close.value
                else:
                    position.lowest_close_since_tp1 = min(position.lowest_close_since_tp1, candle.close.value)
                candidate = position.lowest_close_since_tp1 + position.atr_bg
                if candidate < position.stop_loss.value:
                    update_stop(candidate)
            return

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
