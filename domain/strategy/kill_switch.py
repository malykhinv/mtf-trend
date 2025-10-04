from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Deque, Optional

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

    def is_blocked(self, timestamp: datetime) -> bool:
        if self._block_until is None:
            return False
        if timestamp >= self._block_until:
            self._block_until = None
            return False
        return True

    def blocked_until(self) -> Optional[datetime]:
        return self._block_until

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
        self.logger.log("Блокировка торгов: ожидание после ресинка.", timestamp)

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
        self.logger.log(f"Блокировка торгов: {reason}.", timestamp)


__all__ = ["KillSwitch"]
