from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from config.models.exchange_name import ExchangeName
from config.models.trading_profile import TradingProfile
from data.exchanges import ResyncReason as StreamResyncReason
from domain.strategy.resync import FeedStatus


@dataclass
class RuntimeGuards:
    exchange: Optional[ExchangeName] = None
    profile: Optional[TradingProfile] = None

    def set_exchange(self, value: ExchangeName) -> None:
        if self.exchange is None:
            self.exchange = value
            return
        if self.exchange is not value:
            raise RuntimeError("multiple exchanges configured")

    def set_profile(self, value: TradingProfile) -> None:
        if self.profile is None:
            self.profile = value
            return
        if self.profile is not value:
            raise RuntimeError("multiple profiles configured")


@dataclass
class FeedMonitor:
    has_sequence_gap: bool = False
    has_silence_timeout: bool = False
    has_queue_overflow: bool = False
    has_connection_loss: bool = False
    _degraded_streams: dict[str, bool] = field(default_factory=dict, repr=False)

    def flag(self, reason: StreamResyncReason) -> None:
        if reason is StreamResyncReason.SEQUENCE_GAP:
            self.has_sequence_gap = True
        elif reason is StreamResyncReason.CONNECTION_LOST:
            self.has_connection_loss = True
        elif reason is StreamResyncReason.QUEUE_OVERFLOW:
            self.has_queue_overflow = True
        else:
            self.has_silence_timeout = True

    def snapshot(self) -> FeedStatus:
        return FeedStatus(
            has_sequence_gap=self.has_sequence_gap,
            has_connection_loss=self.has_connection_loss,
            has_silence_timeout=self.has_silence_timeout,
            has_queue_overflow=self.has_queue_overflow,
            degraded_streams=tuple(sorted(self._degraded_streams)),
        )

    def clear(self) -> None:
        self.has_sequence_gap = False
        self.has_connection_loss = False
        self.has_silence_timeout = False
        self.has_queue_overflow = False
        self._degraded_streams.clear()

    def set_degraded(self, stream: str, degraded: bool) -> bool:
        key = stream.lower()
        if degraded:
            changed = not self._degraded_streams.get(key, False)
            self._degraded_streams[key] = True
            return changed
        if self._degraded_streams.pop(key, None):
            return True
        return False


GUARDS = RuntimeGuards()


__all__ = ["FeedMonitor", "GUARDS", "RuntimeGuards"]
