"""Симулятор позиции с сохранением состояния и управлением жизненным циклом SL/TP/BE."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from constants import SIMULATION_PRICE_COMPARISON_EPSILON, TP1_CLOSE_RATIO
from domain.abstract.position_simulator import PositionSimulator
from domain.enums.position_side import PositionSide
from domain.models.candle import Candle
from domain.models.position import Position
from domain.models.trade_result import TradeResult
from domain.models.trade_signal import TradeSignal
from domain.value_objects.price import Price
from domain.value_objects.volume import Volume
from simulation.exit_manager import ExitManager, ExitManagerConfig
from simulation.order_processor import OrderProcessor
from simulation.trade_classifier import TradeClassifier


@dataclass(slots=True)
class StatefulPositionSimulator(PositionSimulator):
    """Обрабатывает свечи по одной и закрывает позицию по консервативным приоритетам."""

    side: PositionSide
    order_processor: OrderProcessor
    trade_classifier: TradeClassifier
    exit_manager: ExitManager = field(default_factory=lambda: ExitManager(config=ExitManagerConfig(tp1_close_ratio=TP1_CLOSE_RATIO)))
    max_bars_in_trade: int | None = None

    position: Position | None = None
    _pending_signal: TradeSignal | None = None
    _pending_size: float = 0.0
    _realized_pnl: float = 0.0
    _closed_size: float = 0.0
    _last_exit_price: float = 0.0
    _bars_in_trade: int = 0

    # region Приватные

    def _open_from_pending_signal(self, candle: Candle) -> None:
        signal = self._pending_signal
        if signal is None:
            return

        fill = self.order_processor.execute_entry(candle.open.value, self.side, self._pending_size)
        self.position = Position(
            entry_price=Price(fill.price),
            entry_timestamp_ms=candle.timestamp_ms,
            size=Volume(self._pending_size),
            stop_loss=signal.stop_loss,
            take_profit_1=signal.take_profit_1,
            take_profit_2=signal.take_profit_2,
            breakout_timestamp_ms=signal.breakout_timestamp_ms,
            retest_timestamp_ms=signal.retest_timestamp_ms,
        )
        self._realized_pnl = -fill.commission
        self._closed_size = 0.0
        self._last_exit_price = fill.price
        self._bars_in_trade = 0
        self._pending_signal = None
        self._pending_size = 0.0

    def _process_long(self, candle: Candle) -> TradeResult | None:
        assert self.position is not None
        target, exit_at_be = self.exit_manager.process(
            candle=candle,
            position=self.position,
            side=self.side,
            close_leg=lambda size, price: self._close_leg(size=size, target_price=price),
            breakeven_price=self.order_processor.breakeven_price,
            update_stop=lambda stop: self.update_stop(stop),
        )
        if target is not None:
            return self._finalize(candle, target, exit_at_be=exit_at_be, exit_at_tp2=math.isclose(target, self.position.take_profit_2.value, abs_tol=SIMULATION_PRICE_COMPARISON_EPSILON))
        return None

    def _process_short(self, candle: Candle) -> TradeResult | None:
        assert self.position is not None
        target, exit_at_be = self.exit_manager.process(
            candle=candle,
            position=self.position,
            side=self.side,
            close_leg=lambda size, price: self._close_leg(size=size, target_price=price),
            breakeven_price=self.order_processor.breakeven_price,
            update_stop=lambda stop: self.update_stop(stop),
        )
        if target is not None:
            return self._finalize(candle, target, exit_at_be=exit_at_be, exit_at_tp2=math.isclose(target, self.position.take_profit_2.value, abs_tol=SIMULATION_PRICE_COMPARISON_EPSILON))
        return None

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
            exit_timestamp_ms=candle.timestamp_ms,
            result_type=result_type,
            pnl=self._realized_pnl,
        )
        self.position = None
        self._bars_in_trade = 0
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

        self._bars_in_trade += 1
        if self.max_bars_in_trade is not None and self._bars_in_trade >= self.max_bars_in_trade:
            return self._finalize(candle, candle.close.value, exit_at_be=False, exit_at_tp2=False)

        if self.side == PositionSide.LONG:
            return self._process_long(candle)
        return self._process_short(candle)

    def open_position(self, position: Position) -> None:
        """Устанавливает активную позицию. Предназначено для уже исполненных сделок."""
        self.position = position
        self._realized_pnl = 0.0
        self._closed_size = 0.0
        self._last_exit_price = position.entry_price.value
        self._bars_in_trade = 0

    def close_position(self, price: float, exit_timestamp_ms: int) -> TradeResult:
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
            exit_timestamp_ms=exit_timestamp_ms,
            result_type=result_type,
            pnl=self._realized_pnl,
        )
        self.position = None
        self._bars_in_trade = 0
        return trade_result

    def update_stop(self, new_stop: float) -> None:
        """Обновляет уровень стоп-лосса для активной позиции."""
        if self.position is None:
            msg = "Нет активной позиции для обновления стопа."
            raise RuntimeError(msg)
        self.position.stop_loss = Price(new_stop)
