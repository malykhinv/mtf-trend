from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Optional

from utils import get_current_time
from .event_logger import EventLogger


@dataclass
class KillSwitch:
    logger: EventLogger
    funding_block: timedelta
    block_duration: timedelta
    stop_interval: timedelta
    max_stops: int
    max_drawdown: float
    position_fraction: float
    _block_until: Optional[datetime] = None
    _last_block_at: Optional[datetime] = None
    _stop_events: Deque[datetime] = field(default_factory=deque)
    _equity: float = 1.0
    _peak_equity: float = 1.0
    _funding_event_at: Optional[datetime] = None
    _block_reason: Optional[str] = None

    def is_blocked(self, timestamp: datetime) -> bool:
        if self._block_until is None:
            return False
        if timestamp >= self._block_until:
            self._block_until = None
            self._block_reason = None
            self._funding_event_at = None
            return False
        return True

    def blocked_until(self) -> Optional[datetime]:
        return self._block_until

    def block_reason(self) -> Optional[str]:
        return self._block_reason

    def record_entry(self, timestamp: datetime) -> None:
        self._purge_stops(timestamp)

    def record_exit(self, timestamp: datetime, is_stop: bool, pnl_fraction: float) -> None:
        if is_stop and self.max_stops > 0:
            self._stop_events.append(timestamp)
            self._purge_stops(timestamp)
            if len(self._stop_events) > self.max_stops:
                self._trigger_block("Частые стопы", timestamp)
        if pnl_fraction < 0.0:
            self._update_drawdown(pnl_fraction, timestamp)

    def handle_resync(self, timestamp: datetime) -> None:
        if self.funding_block <= timedelta(0):
            return
        block_until = timestamp + self.funding_block
        if self._block_until is None or block_until > self._block_until:
            self._block_until = block_until
        self._last_block_at = timestamp
        self._block_reason = "ожидание после ресинка"
        self._funding_event_at = None
        self.logger.log_error("Блокировка торгов: ожидание после ресинка.", timestamp)

    def handle_funding(self, funding_time: datetime) -> None:
        if self.funding_block <= timedelta(0):
            return
        now = get_current_time()
        start = funding_time - self.funding_block
        end = funding_time + self.funding_block
        if now < start or now > end:
            return
        if (
            self._funding_event_at == funding_time
            and self._block_until is not None
            and self._block_until >= end
        ):
            return
        if self._block_until is None or self._block_until < end:
            self._block_until = end
            update_reason = True
        else:
            update_reason = self._block_reason in (None, "окно фандинга")
        self._last_block_at = now
        self._funding_event_at = funding_time
        if update_reason:
            self._block_reason = "окно фандинга"
        funding_str = funding_time.strftime("%Y-%m-%d %H:%M:%S %Z")
        release = (self._block_until or end).strftime("%Y-%m-%d %H:%M:%S %Z")
        self.logger.log_error(
            (
                "Блокировка торгов: окно фандинга."
                f" Фандинг в {funding_str}, блокировка до {release}."
            ),
            now,
        )

    def _purge_stops(self, timestamp: datetime) -> None:
        if self.stop_interval <= timedelta(0):
            return
        cutoff = timestamp - self.stop_interval
        while self._stop_events and self._stop_events[0] < cutoff:
            self._stop_events.popleft()

    def _update_drawdown(self, pnl_fraction: float, timestamp: datetime) -> None:
        change = 1.0 + pnl_fraction * self.position_fraction
        if change <= 0.0:
            self._equity = 0.0
        else:
            self._equity *= change
        if self._equity > self._peak_equity:
            self._peak_equity = self._equity
            return
        if self._peak_equity <= 0.0:
            return
        drawdown = (self._peak_equity - self._equity) / self._peak_equity
        if drawdown >= self.max_drawdown > 0.0:
            self._trigger_block("Просадка превысила лимит", timestamp)

    def _trigger_block(self, reason: str, timestamp: datetime) -> None:
        if self.block_duration <= timedelta(0):
            return
        block_until = timestamp + self.block_duration
        if self._block_until is None or block_until > self._block_until:
            self._block_until = block_until
        self._last_block_at = timestamp
        self._block_reason = reason
        self._funding_event_at = None
        self.logger.log_error(f"Блокировка торгов: {reason}.", timestamp)


__all__ = ["KillSwitch"]
