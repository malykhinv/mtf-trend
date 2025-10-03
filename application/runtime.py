from __future__ import annotations

from dataclasses import dataclass
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

    def flag(self, reason: StreamResyncReason) -> None:
        if reason is StreamResyncReason.SEQUENCE_GAP:
            self.has_sequence_gap = True
        elif reason is StreamResyncReason.QUEUE_OVERFLOW:
            self.has_queue_overflow = True
        else:
            self.has_silence_timeout = True

    def snapshot(self) -> FeedStatus:
        return FeedStatus(
            has_sequence_gap=self.has_sequence_gap,
            has_silence_timeout=self.has_silence_timeout,
            has_queue_overflow=self.has_queue_overflow,
        )

    def clear(self) -> None:
        self.has_sequence_gap = False
        self.has_silence_timeout = False
        self.has_queue_overflow = False


GUARDS = RuntimeGuards()


__all__ = ["FeedMonitor", "GUARDS", "RuntimeGuards"]
