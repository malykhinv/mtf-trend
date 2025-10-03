"""Finite state machine implementing the uptick trading strategy."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Optional, Tuple

from config.config import CONFIG

from .models import LogLine, Pressure, Signal, Side, Wall
from .models._timezone import ensure_belgrade_timezone


class StrategyState(str, Enum):
    SCANNING = "scanning"
    FOCUSED = "focused"
    IN_POSITION = "in_position"
    RESYNC = "resync"


class ResyncReason(str, Enum):
    SEQUENCE_GAP = "пропуск последовательности"
    SILENCE = "тишина канала"
    BACKPRESSURE = "переполнение очереди"


@dataclass(frozen=True)
class FeedStatus:
    has_sequence_gap: bool = False
    has_silence_timeout: bool = False
    has_queue_overflow: bool = False

    def resolve_reason(self) -> Optional[ResyncReason]:
        if self.has_sequence_gap:
            return ResyncReason.SEQUENCE_GAP
        if self.has_silence_timeout:
            return ResyncReason.SILENCE
        if self.has_queue_overflow:
            return ResyncReason.BACKPRESSURE
        return None


@dataclass(frozen=True)
class MarketObservation:
    timestamp: datetime
    symbol: str
    last_price: float
    tick_size: float
    pressure: Optional[Pressure]
    near_wall: Optional[Wall]
    opposite_wall_blocks: bool
    available_symbols: Tuple[str, ...]
    feed_status: FeedStatus

    def __post_init__(self) -> None:
        ensure_belgrade_timezone(self.timestamp)


SubscriptionHandler = Callable[[str], None]
FocusHandler = Callable[[str], None]
DefocusHandler = Callable[[], None]
PositionEntryHandler = Callable[[str, Signal, Wall], None]
PositionExitHandler = Callable[[str, str], None]
StopMoveHandler = Callable[[str, float], None]
TelegramHandler = Callable[[str], None]
LogWriter = Callable[[LogLine], None]
ResyncHandler = Callable[[ResyncReason], None]


@dataclass
class EventLogger:
    write: LogWriter

    def log(self, message: str, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        entry = LogLine(timestamp=timestamp, message=message, level="INFO")
        self.write(entry)


@dataclass
class SubscriptionManager:
    subscribe: SubscriptionHandler
    unsubscribe: SubscriptionHandler
    logger: EventLogger
    _active: Tuple[str, ...] = ()

    def update(self, symbols: Tuple[str, ...], timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        additions: Tuple[str, ...] = self._compute_additions(symbols)
        removals: Tuple[str, ...] = self._compute_removals(symbols)
        for symbol in additions:
            self.subscribe(symbol)
            self.logger.log(f"{timestamp:%H:%M:%S} Подписка на {symbol}.", timestamp)
        for symbol in removals:
            self.unsubscribe(symbol)
            self.logger.log(f"{timestamp:%H:%M:%S} Отписка от {symbol}.", timestamp)
        self._active = tuple(symbols)

    def _compute_additions(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        missing: list[str] = []
        for symbol in symbols:
            if not self._contains(self._active, symbol):
                missing.append(symbol)
        return tuple(missing)

    def _compute_removals(self, symbols: Tuple[str, ...]) -> Tuple[str, ...]:
        redundant: list[str] = []
        for symbol in self._active:
            if not self._contains(symbols, symbol):
                redundant.append(symbol)
        return tuple(redundant)

    @staticmethod
    def _contains(collection: Tuple[str, ...], item: str) -> bool:
        for value in collection:
            if value == item:
                return True
        return False


@dataclass
class FocusController:
    focus_symbol: FocusHandler
    defocus_symbol: DefocusHandler
    logger: EventLogger
    _current: Optional[str] = None

    def focus(self, symbol: str, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        if self._current == symbol:
            return
        if self._current is not None:
            self.defocus(timestamp)
        self.focus_symbol(symbol)
        self.logger.log(f"{timestamp:%H:%M:%S} Фокус на {symbol}.", timestamp)
        self._current = symbol

    def defocus(self, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        if self._current is None:
            return
        current = self._current
        self.defocus_symbol()
        self.logger.log(f"{timestamp:%H:%M:%S} Дефокус со {current}.", timestamp)
        self._current = None

    @property
    def current(self) -> Optional[str]:
        return self._current


@dataclass
class PositionController:
    enter_position: PositionEntryHandler
    exit_position: PositionExitHandler
    move_stop: StopMoveHandler
    logger: EventLogger

    def enter(self, symbol: str, signal: Signal, wall: Wall, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        self.enter_position(symbol, signal, wall)
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.logger.log(
            f"{timestamp:%H:%M:%S} Вход {direction} {symbol} по стене {wall.price:g}.",
            timestamp,
        )

    def exit(self, symbol: str, reason: str, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        self.exit_position(symbol, reason)
        self.logger.log(
            f"{timestamp:%H:%M:%S} Выход {symbol}. Причина: {reason}.", timestamp
        )

    def adjust_stop(self, symbol: str, price: float, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        self.move_stop(symbol, price)
        self.logger.log(
            f"{timestamp:%H:%M:%S} Перенос стопа {symbol} на {price:g}.", timestamp
        )


@dataclass
class TelegramNotifier:
    send_message: TelegramHandler

    def notify_uptick(self, symbol: str, signal: Signal, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.send_message(f"{timestamp:%H:%M:%S} Обнаружен аптик {symbol} {direction}.")

    def notify_entry(self, symbol: str, signal: Signal, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self.send_message(f"{timestamp:%H:%M:%S} Открыт {direction} {symbol}.")

    def notify_exit(self, symbol: str, reason: str, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        self.send_message(f"{timestamp:%H:%M:%S} Закрыт {symbol}. {reason}.")


class Strategy:
    def __init__(
        self,
        *,
        subscriptions: SubscriptionManager,
        focus: FocusController,
        position: PositionController,
        notifier: TelegramNotifier,
        logger: EventLogger,
        resync: ResyncHandler,
    ) -> None:
        self._subscriptions = subscriptions
        self._focus = focus
        self._position = position
        self._notifier = notifier
        self._logger = logger
        self._resync = resync
        self._state = StrategyState.SCANNING
        self._previous_state = StrategyState.SCANNING
        self._focused_signal = Signal.NONE
        self._focused_wall: Optional[Wall] = None
        self._last_scan_at: Optional[datetime] = None
        self._last_focus_signal_at: Optional[datetime] = None
        self._last_uptick_at: Optional[datetime] = None
        self._position_symbol: Optional[str] = None
        self._position_signal = Signal.NONE
        self._position_wall: Optional[Wall] = None
        self._odr_neutral_since: Optional[datetime] = None
        self._wall_drop_since: Optional[datetime] = None
        self._shift_candidate_price: Optional[float] = None
        self._shift_candidate_since: Optional[datetime] = None
        self._resync_reason: Optional[ResyncReason] = None
        self._resync_summary_start: Optional[datetime] = None
        self._resync_summary_count = 0

    @property
    def state(self) -> StrategyState:
        return self._state

    def process(self, observation: MarketObservation) -> StrategyState:
        self._maybe_refresh_subscriptions(observation)
        reason = observation.feed_status.resolve_reason()
        if reason is not None and self._state is not StrategyState.RESYNC:
            self._enter_resync(reason, observation.timestamp)
            return self._state
        if self._state is StrategyState.RESYNC:
            return self._state
        if self._state is StrategyState.SCANNING:
            self._handle_scanning(observation)
        elif self._state is StrategyState.FOCUSED:
            self._handle_focused(observation)
        elif self._state is StrategyState.IN_POSITION:
            self._handle_in_position(observation)
        return self._state

    def complete_resync(self, timestamp: datetime) -> StrategyState:
        ensure_belgrade_timezone(timestamp)
        if self._state is not StrategyState.RESYNC:
            return self._state
        message = f"{timestamp:%H:%M:%S} Ресинк завершён."
        self._logger.log(message, timestamp)
        self._state = self._previous_state
        self._resync_reason = None
        return self._state

    def _maybe_refresh_subscriptions(self, observation: MarketObservation) -> None:
        interval = timedelta(seconds=CONFIG.turnover.market_scan_interval_s)
        if self._last_scan_at is None or observation.timestamp - self._last_scan_at >= interval:
            self._subscriptions.update(observation.available_symbols, observation.timestamp)
            self._last_scan_at = observation.timestamp

    def _handle_scanning(self, observation: MarketObservation) -> None:
        if observation.pressure is None or observation.near_wall is None:
            return
        wall = observation.near_wall
        ratio = observation.pressure.imbalance_ratio
        if wall.side is Side.BID and ratio <= CONFIG.odr.focus_pre_odr_long:
            self._focus_on_symbol(observation.symbol, Signal.LONG, wall, observation.timestamp)
        elif wall.side is Side.ASK and ratio >= CONFIG.odr.focus_pre_odr_short:
            self._focus_on_symbol(observation.symbol, Signal.SHORT, wall, observation.timestamp)

    def _handle_focused(self, observation: MarketObservation) -> None:
        if self._focus.current != observation.symbol:
            return
        pressure = observation.pressure
        wall = observation.near_wall
        timestamp = observation.timestamp
        if pressure is not None and wall is not None:
            if self._focused_signal is Signal.LONG and wall.side is Side.BID:
                if pressure.imbalance_ratio <= CONFIG.odr.focus_pre_odr_long:
                    self._last_focus_signal_at = timestamp
                    self._focused_wall = wall
            elif self._focused_signal is Signal.SHORT and wall.side is Side.ASK:
                if pressure.imbalance_ratio >= CONFIG.odr.focus_pre_odr_short:
                    self._last_focus_signal_at = timestamp
                    self._focused_wall = wall
        if self._should_defocus(timestamp):
            self._focus.defocus(timestamp)
            self._focused_signal = Signal.NONE
            self._focused_wall = None
            self._state = StrategyState.SCANNING
            return
        if pressure is None or wall is None:
            return
        if observation.opposite_wall_blocks:
            return
        if self._focused_signal is Signal.LONG and wall.side is Side.BID:
            if pressure.imbalance_ratio <= CONFIG.odr.odr_in_long:
                self._enter_position(observation.symbol, wall, timestamp)
        elif self._focused_signal is Signal.SHORT and wall.side is Side.ASK:
            if pressure.imbalance_ratio >= CONFIG.odr.odr_in_short:
                self._enter_position(observation.symbol, wall, timestamp)

    def _handle_in_position(self, observation: MarketObservation) -> None:
        if self._position_symbol != observation.symbol:
            return
        timestamp = observation.timestamp
        pressure = observation.pressure
        if pressure is not None and self._is_odr_neutral(pressure):
            if self._odr_neutral_since is None:
                self._odr_neutral_since = timestamp
        else:
            self._odr_neutral_since = None
        if self._odr_neutral_since is not None:
            hold = timedelta(milliseconds=CONFIG.odr.odr_neutral_hold_ms)
            if timestamp - self._odr_neutral_since >= hold:
                self._exit_position("ODR нейтрален", timestamp)
                return
        wall = observation.near_wall
        if wall is not None:
            self._consider_wall_shift(wall, observation.tick_size, timestamp)
        if self._detect_wall_drop(wall, observation.tick_size, timestamp):
            grace = timedelta(milliseconds=CONFIG.wall_shift.vanish_grace_ms)
            if self._wall_drop_since is None:
                self._wall_drop_since = timestamp
            elif timestamp - self._wall_drop_since >= grace:
                self._exit_position("Стена исчезла", timestamp)
        else:
            self._wall_drop_since = None

    def _focus_on_symbol(
        self, symbol: str, signal: Signal, wall: Wall, timestamp: datetime
    ) -> None:
        self._focus.focus(symbol, timestamp)
        self._state = StrategyState.FOCUSED
        self._focused_signal = signal
        self._focused_wall = wall
        self._last_focus_signal_at = timestamp
        if self._should_notify_uptick(timestamp):
            self._notifier.notify_uptick(symbol, signal, timestamp)
            self._last_uptick_at = timestamp

    def _should_notify_uptick(self, timestamp: datetime) -> bool:
        cooldown = timedelta(minutes=CONFIG.telegram.uptick_cooldown_min)
        if self._last_uptick_at is None:
            return True
        return timestamp - self._last_uptick_at >= cooldown

    def _should_defocus(self, timestamp: datetime) -> bool:
        if self._last_focus_signal_at is None:
            return True
        timeout = timedelta(seconds=CONFIG.focus.defocus_timeout_s)
        return timestamp - self._last_focus_signal_at >= timeout

    def _enter_position(self, symbol: str, wall: Wall, timestamp: datetime) -> None:
        signal = self._focused_signal
        if signal is Signal.NONE:
            return
        self._position.enter(symbol, signal, wall, timestamp)
        self._notifier.notify_entry(symbol, signal, timestamp)
        self._state = StrategyState.IN_POSITION
        self._position_symbol = symbol
        self._position_signal = signal
        self._position_wall = wall
        self._odr_neutral_since = None
        self._wall_drop_since = None
        self._shift_candidate_price = None
        self._shift_candidate_since = None

    def _exit_position(self, reason: str, timestamp: datetime) -> None:
        symbol = self._position_symbol
        if symbol is None:
            return
        self._position.exit(symbol, reason, timestamp)
        self._notifier.notify_exit(symbol, reason, timestamp)
        self._state = StrategyState.SCANNING
        self._position_symbol = None
        self._position_signal = Signal.NONE
        self._position_wall = None
        self._odr_neutral_since = None
        self._wall_drop_since = None
        self._shift_candidate_price = None
        self._shift_candidate_since = None
        self._focus.defocus(timestamp)
        self._focused_signal = Signal.NONE
        self._focused_wall = None
        self._last_focus_signal_at = None

    def _is_odr_neutral(self, pressure: Pressure) -> bool:
        low = CONFIG.odr.odr_neutral_low
        high = CONFIG.odr.odr_neutral_high
        return low <= pressure.imbalance_ratio <= high

    def _detect_wall_drop(
        self,
        wall: Optional[Wall],
        tick_size: float,
        timestamp: datetime,
    ) -> bool:
        ensure_belgrade_timezone(timestamp)
        reference = self._position_wall
        signal = self._position_signal
        if reference is None or signal is Signal.NONE:
            return True
        if wall is None or wall.side is not reference.side:
            return True
        if tick_size <= 0.0:
            return True
        ticks = abs(wall.price - reference.price) / tick_size
        min_shift = CONFIG.wall_shift.shift_min_ticks
        if ticks >= float(min_shift) and self._is_shift_in_favor(signal, wall.price, reference.price):
            return False
        if ticks > 0.0:
            return True
        drop_threshold = 1.0 - CONFIG.wall_shift.vanish_drop
        if wall.quantity < reference.quantity * drop_threshold:
            return True
        self._position_wall = wall
        return False

    def _consider_wall_shift(
        self, wall: Wall, tick_size: float, timestamp: datetime
    ) -> None:
        reference = self._position_wall
        signal = self._position_signal
        if reference is None or signal is Signal.NONE:
            return
        if wall.side is not reference.side:
            return
        if tick_size <= 0.0:
            return
        ticks = abs(wall.price - reference.price) / tick_size
        if ticks < float(CONFIG.wall_shift.shift_min_ticks):
            self._shift_candidate_price = None
            self._shift_candidate_since = None
            return
        if not self._is_shift_in_favor(signal, wall.price, reference.price):
            self._shift_candidate_price = None
            self._shift_candidate_since = None
            return
        if self._shift_candidate_price != wall.price:
            self._shift_candidate_price = wall.price
            self._shift_candidate_since = wall.first_seen_at
        hold = timedelta(seconds=CONFIG.wall_shift.shift_hold_s)
        if self._shift_candidate_since is None:
            self._shift_candidate_since = wall.first_seen_at
        since = self._shift_candidate_since
        if since is None:
            return
        if timestamp - since < hold:
            return
        if self._position_symbol is None:
            return
        self._position.adjust_stop(self._position_symbol, wall.price, timestamp)
        self._position_wall = wall
        self._shift_candidate_price = None
        self._shift_candidate_since = None

    @staticmethod
    def _is_shift_in_favor(signal: Signal, new_price: float, old_price: float) -> bool:
        if signal is Signal.LONG:
            return new_price > old_price
        if signal is Signal.SHORT:
            return new_price < old_price
        return False

    def _enter_resync(self, reason: ResyncReason, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        self._resync(reason)
        self._previous_state = self._state
        self._state = StrategyState.RESYNC
        self._resync_reason = reason
        self._logger.log(
            f"{timestamp:%H:%M:%S} Ресинк книги. Причина: {reason.value}.", timestamp
        )
        self._track_resync_summary(timestamp)

    def _track_resync_summary(self, timestamp: datetime) -> None:
        ensure_belgrade_timezone(timestamp)
        if self._resync_summary_start is None:
            self._resync_summary_start = timestamp
        elapsed = timestamp - self._resync_summary_start
        if elapsed >= timedelta(hours=1):
            self._logger.log(
                f"{timestamp:%H:%M:%S} Ресинков за час: {self._resync_summary_count}.",
                timestamp,
            )
            self._resync_summary_start = timestamp
            self._resync_summary_count = 0
        self._resync_summary_count += 1


__all__ = [
    "StrategyState",
    "ResyncReason",
    "FeedStatus",
    "MarketObservation",
    "SubscriptionManager",
    "FocusController",
    "PositionController",
    "TelegramNotifier",
    "EventLogger",
    "Strategy",
]
