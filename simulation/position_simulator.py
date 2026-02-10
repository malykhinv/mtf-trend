"""Симулятор позиции с сохранением состояния и управлением жизненным циклом SL/TP/BE."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime

from constants import SIMULATION_PRICE_COMPARISON_EPSILON, TP1_CLOSE_RATIO
from domain.abstract.position_simulator import PositionSimulator
from domain.enums.position_side import PositionSide
from domain.models.candle import Candle
from domain.models.position import Position
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume
from simulation.order_processor import OrderProcessor
from simulation.trade_classifier import TradeClassifier
from utils.formatters import datetime_to_timezone


@dataclass(slots=True)
class StatefulPositionSimulator(PositionSimulator):
    """Обрабатывает свечи по одной и закрывает позицию по консервативным приоритетам."""

    side: PositionSide
    order_processor: OrderProcessor
    trade_classifier: TradeClassifier
    simulation_timezone: str

    position: Position | None = None
    _pending_signal: TradeSignal | None = None
    _pending_size: float = 0.0
    _realized_pnl: float = 0.0
    _closed_size: float = 0.0
    _last_exit_price: float = 0.0

    # region Приватные

    def _open_from_pending_signal(self, candle: Candle) -> None:
        signal = self._pending_signal
        if signal is None:
            return

        fill = self.order_processor.execute_entry(candle.open.value, self.side, self._pending_size)
        self.position = Position(
            entry_price=Price(fill.price),
            entry_time=datetime_to_timezone(candle.timestamp, self.simulation_timezone),
            size=Volume(self._pending_size),
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
        )
        self._realized_pnl = -fill.commission
        self._closed_size = 0.0
        self._last_exit_price = fill.price
        self._pending_signal = None
        self._pending_size = 0.0

    def _process_long(self, candle: Candle) -> TradeResult | None:
        assert self.position is not None

        if candle.low.value <= self.position.stop_loss.value:
            return self._finalize(candle, self.position.stop_loss.value, exit_at_be=self.position.sl_moved_to_be)

        if not self.position.tp1_done and candle.high.value >= self.position.take_profit_1.value:
            self._take_tp1()
            if candle.low.value <= self.position.stop_loss.value:
                return self._finalize(candle, self.position.stop_loss.value, exit_at_be=True)

        if self.position.tp1_done and candle.low.value <= self.position.stop_loss.value:
            return self._finalize(candle, self.position.stop_loss.value, exit_at_be=True)

        if candle.high.value >= self.position.take_profit_2.value:
            return self._finalize(candle, self.position.take_profit_2.value, exit_at_tp2=True)

        return None

    def _process_short(self, candle: Candle) -> TradeResult | None:
        assert self.position is not None

        if candle.high.value >= self.position.stop_loss.value:
            return self._finalize(candle, self.position.stop_loss.value, exit_at_be=self.position.sl_moved_to_be)

        if not self.position.tp1_done and candle.low.value <= self.position.take_profit_1.value:
            self._take_tp1()
            if candle.high.value >= self.position.stop_loss.value:
                return self._finalize(candle, self.position.stop_loss.value, exit_at_be=True)

        if self.position.tp1_done and candle.high.value >= self.position.stop_loss.value:
            return self._finalize(candle, self.position.stop_loss.value, exit_at_be=True)

        if candle.low.value <= self.position.take_profit_2.value:
            return self._finalize(candle, self.position.take_profit_2.value, exit_at_tp2=True)

        return None

    def _take_tp1(self) -> None:
        assert self.position is not None
        tp1_size = self.position.size.value * TP1_CLOSE_RATIO
        self._close_leg(size=tp1_size, target_price=self.position.take_profit_1.value)
        self.position.tp1_done = True
        self.position.sl_moved_to_be = True
        self.position.stop_loss = Price(self.order_processor.breakeven_price(self.position.entry_price.value, self.side))

    def _close_leg(self, *, size: float, target_price: float) -> None:
        assert self.position is not None

        fill = self.order_processor.execute_exit(target_price, self.side, size)
        if self.side == PositionSide.LONG:
            gross = (fill.price - self.position.entry_price.value) * size
        else:
            gross = (self.position.entry_price.value - fill.price) * size

        self._realized_pnl += gross - fill.commission
        self._closed_size += size
        self._last_exit_price = fill.price

    def _finalize(self, candle: Candle, target_price: float, *, exit_at_be: bool = False, exit_at_tp2: bool = False) -> TradeResult:
        assert self.position is not None

        remaining_size = self.position.size.value - self._closed_size
        if remaining_size > 0:
            self._close_leg(size=remaining_size, target_price=target_price)

        result_type = self.trade_classifier.classify_result_type(
            tp1_done=self.position.tp1_done,
            exit_at_breakeven=exit_at_be,
            exit_at_tp2=exit_at_tp2,
        )
        trade_result = self.trade_classifier.build_result(
            position=self.position,
            exit_price=self._last_exit_price,
            exit_time=datetime_to_timezone(candle.timestamp, self.simulation_timezone),
            result_type=result_type,
            pnl=self._realized_pnl,
        )
        self.position = None
        return trade_result

    # endregion Приватные

    def register_signal(self, signal: TradeSignal, size: float) -> None:
        """Сохраняет сигнал; позиция откроется рыночным ордером на открытии следующей свечи."""
        self._pending_signal = signal
        self._pending_size = size

    def process_candle(self, candle: Candle) -> TradeResult | None:
        """Открывает отложенный сигнал и обрабатывает активную позицию на текущей свече."""
        if self.position is None and self._pending_signal is not None:
            self._open_from_pending_signal(candle)

        if self.position is None:
            return None

        if self.side == PositionSide.LONG:
            return self._process_long(candle)
        return self._process_short(candle)

    def open_position(self, position: Position) -> None:
        """Устанавливает активную позицию. Предназначено для уже исполненных сделок."""
        self.position = position
        self._realized_pnl = 0.0
        self._closed_size = 0.0
        self._last_exit_price = position.entry_price.value

    def close_position(self, price: float, exit_time: datetime) -> TradeResult:
        """Закрывает остаток позиции по заданной цене и времени, затем возвращает классифицированный результат сделки."""
        if self.position is None:
            msg = "Нет активной позиции для закрытия."
            raise RuntimeError(msg)

        remaining_size = self.position.size.value - self._closed_size
        if remaining_size > 0:
            self._close_leg(size=remaining_size, target_price=price)

        result_type = self.trade_classifier.classify_result_type(
            tp1_done=self.position.tp1_done,
            exit_at_breakeven=self.position.sl_moved_to_be
            and math.isclose(price, self.position.stop_loss.value, abs_tol=SIMULATION_PRICE_COMPARISON_EPSILON),
            exit_at_tp2=math.isclose(price, self.position.take_profit_2.value, abs_tol=SIMULATION_PRICE_COMPARISON_EPSILON),
        )
        trade_result = self.trade_classifier.build_result(
            position=self.position,
            exit_price=self._last_exit_price,
            exit_time=datetime_to_timezone(exit_time, self.simulation_timezone),
            result_type=result_type,
            pnl=self._realized_pnl,
        )
        self.position = None
        return trade_result

    def update_stop(self, new_stop: float) -> None:
        """Обновляет уровень стоп-лосса для активной позиции."""
        if self.position is None:
            msg = "Нет активной позиции для обновления стопа."
            raise RuntimeError(msg)
        self.position.stop_loss = Price(new_stop)
