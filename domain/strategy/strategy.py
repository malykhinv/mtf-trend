from __future__ import annotations
from datetime import datetime, timedelta
from typing import Optional

from config.config import CONFIG

from domain.models import Pressure, Signal, Side, Wall

from .event_logger import EventLogger
from .focus import FocusController
from .kill_switch import KillSwitch
from .observation import MarketObservation
from .position import PositionController
from .resync import ResyncReason
from .state import StrategyState
from .subscription import SubscriptionManager
from .telegram import TelegramNotifier
from .types import ResyncHandler


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
        safety: KillSwitch,
    ) -> None:
        self._subscriptions = subscriptions
        self._focus = focus
        self._position = position
        self._notifier = notifier
        self._logger = logger
        self._resync = resync
        self._safety = safety
        self._state = StrategyState.SCANNING
        self._previous_state = StrategyState.SCANNING
        self._focused_signal = Signal.NONE
        self._focused_wall: Optional[Wall] = None
        self._last_scan_at: Optional[datetime] = None
        self._last_focus_signal_at: Optional[datetime] = None
        self._last_uptick_at: dict[str, datetime] = {}
        self._last_trade_at: dict[str, datetime] = {}
        self._last_stop_move_at: dict[str, datetime] = {}
        self._last_uptick_loss_at: dict[str, datetime] = {}
        self._position_symbol: Optional[str] = None
        self._position_signal = Signal.NONE
        self._position_wall: Optional[Wall] = None
        self._position_entry_price: Optional[float] = None
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
        if self._state is not StrategyState.RESYNC:
            return self._state
        self._logger.log("Ресинк завершён.", timestamp)
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
            if observation.volume_spike:
                self._focus_on_symbol(
                    observation.symbol,
                    Signal.LONG,
                    wall,
                    observation.timestamp,
                    observation.volume_ratio,
                )
        elif wall.side is Side.ASK and ratio >= CONFIG.odr.focus_pre_odr_short:
            if observation.volume_spike:
                self._focus_on_symbol(
                    observation.symbol,
                    Signal.SHORT,
                    wall,
                    observation.timestamp,
                    observation.volume_ratio,
                )

    def _handle_focused(self, observation: MarketObservation) -> None:
        symbol = observation.symbol
        if self._focus.current != symbol:
            return
        pressure = observation.pressure
        wall = observation.near_wall
        timestamp = observation.timestamp
        if pressure is not None and wall is not None and observation.volume_spike:
            if self._focused_signal is Signal.LONG and wall.side is Side.BID:
                if pressure.imbalance_ratio <= CONFIG.odr.focus_pre_odr_long:
                    self._last_focus_signal_at = timestamp
                    self._focused_wall = wall
            elif self._focused_signal is Signal.SHORT and wall.side is Side.ASK:
                if pressure.imbalance_ratio >= CONFIG.odr.focus_pre_odr_short:
                    self._last_focus_signal_at = timestamp
                    self._focused_wall = wall
        if self._should_defocus(timestamp):
            self._logger.log(
                f"Аптик {symbol} потерян: таймаут сигнала.",
                timestamp,
            )
            self._focus.defocus(timestamp)
            self._focused_signal = Signal.NONE
            self._focused_wall = None
            self._state = StrategyState.SCANNING
            self._last_uptick_loss_at[symbol] = timestamp
            return
        if pressure is None or wall is None:
            return
        if observation.opposite_wall_blocks:
            return
        if not observation.volume_spike:
            return
        if self._focused_signal is Signal.LONG and wall.side is Side.BID:
            if pressure.imbalance_ratio <= CONFIG.odr.odr_in_long:
                self._enter_position(
                    observation.symbol,
                    wall,
                    timestamp,
                    observation.last_price,
                )
        elif self._focused_signal is Signal.SHORT and wall.side is Side.ASK:
            if pressure.imbalance_ratio >= CONFIG.odr.odr_in_short:
                self._enter_position(
                    observation.symbol,
                    wall,
                    timestamp,
                    observation.last_price,
                )

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
                self._exit_position(
                    "ODR нейтрален",
                    timestamp,
                    observation.last_price,
                )
                return
        wall = observation.near_wall
        if wall is not None:
            self._consider_wall_shift(wall, observation.tick_size, timestamp)
        if self._detect_wall_drop(wall, observation.tick_size):
            grace = timedelta(milliseconds=CONFIG.wall_shift.vanish_grace_ms)
            if self._wall_drop_since is None:
                self._wall_drop_since = timestamp
            elif timestamp - self._wall_drop_since >= grace:
                self._exit_position(
                    "Стена исчезла",
                    timestamp,
                    observation.last_price,
                )
        else:
            self._wall_drop_since = None

    def _focus_on_symbol(
        self,
        symbol: str,
        signal: Signal,
        wall: Wall,
        timestamp: datetime,
        volume_ratio: float,
    ) -> None:
        self._focus.focus(symbol, timestamp)
        self._state = StrategyState.FOCUSED
        self._focused_signal = signal
        self._focused_wall = wall
        self._last_focus_signal_at = timestamp
        direction = "лонг" if signal is Signal.LONG else "шорт"
        self._logger.log(
            (
                f"Аптик {symbol} {direction}. "
                f"Стена {wall.price:g} объём {wall.notional:g}. "
                f"Всплеск объёма x{volume_ratio:.2f}."
            ),
            timestamp,
        )
        if self._should_notify_uptick(symbol, timestamp):
            self._notifier.notify_uptick(symbol, signal, timestamp)
            self._last_uptick_at[symbol] = timestamp

    def _should_notify_uptick(self, symbol: str, timestamp: datetime) -> bool:
        cooldown = timedelta(minutes=CONFIG.telegram.uptick_cooldown_min)
        last_uptick = self._last_uptick_at.get(symbol)
        if last_uptick is None:
            return True
        if timestamp - last_uptick >= cooldown:
            return True
        return self._has_recent_activity(symbol, timestamp, cooldown)

    def _has_recent_activity(
        self, symbol: str, timestamp: datetime, window: timedelta
    ) -> bool:
        lower_bound = timestamp - window
        last_uptick = self._last_uptick_at.get(symbol)
        if last_uptick is not None and last_uptick > lower_bound:
            lower_bound = last_uptick
        events = (
            self._last_trade_at.get(symbol),
            self._last_stop_move_at.get(symbol),
            self._last_uptick_loss_at.get(symbol),
        )
        for moment in events:
            if moment is not None and lower_bound <= moment <= timestamp:
                return True
        return False

    def _should_defocus(self, timestamp: datetime) -> bool:
        if self._last_focus_signal_at is None:
            return True
        timeout = timedelta(seconds=CONFIG.focus.defocus_timeout_s)
        return timestamp - self._last_focus_signal_at >= timeout

    def _enter_position(
        self,
        symbol: str,
        wall: Wall,
        timestamp: datetime,
        last_price: float,
    ) -> None:
        signal = self._focused_signal
        if signal is Signal.NONE:
            return
        if self._safety.is_blocked(timestamp):
            blocked_until = self._safety.blocked_until()
            reason = self._safety.block_reason()
            if blocked_until is not None:
                message = f"Вход в {symbol} заблокирован до {blocked_until:%H:%M:%S}"
            else:
                message = f"Вход в {symbol} заблокирован"
            if reason:
                message = f"{message} ({reason})."
            else:
                message = f"{message}."
            self._logger.log(message, timestamp)
            return
        self._position.enter(symbol, signal, wall, timestamp)
        self._safety.record_entry(timestamp)
        self._last_trade_at[symbol] = timestamp
        self._notifier.notify_entry(symbol, signal, timestamp)
        self._state = StrategyState.IN_POSITION
        self._position_symbol = symbol
        self._position_signal = signal
        self._position_wall = wall
        self._position_entry_price = last_price if last_price > 0.0 else None
        self._odr_neutral_since = None
        self._wall_drop_since = None
        self._shift_candidate_price = None
        self._shift_candidate_since = None

    def _exit_position(self, reason: str, timestamp: datetime, price: float) -> None:
        symbol = self._position_symbol
        if symbol is None:
            return
        self._position.exit(symbol, reason, timestamp)
        pnl_fraction = self._compute_pnl_fraction(price)
        is_stop = "стоп" in reason.lower() or "stop" in reason.lower()
        self._safety.record_exit(timestamp, is_stop, pnl_fraction)
        self._last_trade_at[symbol] = timestamp
        self._notifier.notify_exit(symbol, reason, timestamp)
        self._state = StrategyState.SCANNING
        self._position_symbol = None
        self._position_signal = Signal.NONE
        self._position_wall = None
        self._position_entry_price = None
        self._odr_neutral_since = None
        self._wall_drop_since = None
        self._shift_candidate_price = None
        self._shift_candidate_since = None
        self._focus.defocus(timestamp)
        self._focused_signal = Signal.NONE
        self._focused_wall = None
        self._last_focus_signal_at = None
        self._last_uptick_loss_at[symbol] = timestamp

    @staticmethod
    def _is_odr_neutral(pressure: Pressure) -> bool:
        low = CONFIG.odr.odr_neutral_low
        high = CONFIG.odr.odr_neutral_high
        return low <= pressure.imbalance_ratio <= high

    def _detect_wall_drop(
        self,
        wall: Optional[Wall],
        tick_size: float,
    ) -> bool:
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
        symbol = self._position_symbol
        if symbol is None:
            return
        self._position.adjust_stop(symbol, wall.price, timestamp)
        self._last_stop_move_at[symbol] = timestamp
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
        self._resync(reason)
        self._previous_state = self._state
        self._state = StrategyState.RESYNC
        self._resync_reason = reason
        self._logger.log(
            f"Ресинк книги. Причина: {reason.value}.", timestamp
        )
        self._track_resync_summary(timestamp)
        self._safety.handle_resync(timestamp)

    def _track_resync_summary(self, timestamp: datetime) -> None:
        if self._resync_summary_start is None:
            self._resync_summary_start = timestamp
        elapsed = timestamp - self._resync_summary_start
        if elapsed >= timedelta(hours=1):
            self._logger.log(
                f"Ресинков за час: {self._resync_summary_count}.",
                timestamp,
            )
            self._resync_summary_start = timestamp
            self._resync_summary_count = 0
        self._resync_summary_count += 1

    def _compute_pnl_fraction(self, exit_price: float) -> float:
        entry_price = self._position_entry_price
        signal = self._position_signal
        if entry_price is None or entry_price <= 0.0:
            return 0.0
        if exit_price <= 0.0:
            return 0.0
        if signal is Signal.LONG:
            return (exit_price - entry_price) / entry_price
        if signal is Signal.SHORT:
            return (entry_price - exit_price) / entry_price
        return 0.0


__all__ = ["Strategy"]
