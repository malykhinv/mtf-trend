from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.models.trading_profile import TradingProfile
from data.exchanges import StreamBuffer
from domain.models import Trade

from .base import AlarmThresholds, FallbackMode, StreamPipeline


@dataclass(frozen=True)
class _TradePipelineProfile:
    buffer_size: int
    warning: int
    critical: int
    degradation_hold_s: float
    fallback: FallbackMode
    chronic_threshold: int
    chronic_window_s: float


_PROFILE_SETTINGS: dict[TradingProfile, _TradePipelineProfile] = {
    TradingProfile.TOP: _TradePipelineProfile(
        buffer_size=512,
        warning=120,
        critical=240,
        degradation_hold_s=20.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=3,
        chronic_window_s=20.0,
    ),
    TradingProfile.ALT: _TradePipelineProfile(
        buffer_size=640,
        warning=200,
        critical=320,
        degradation_hold_s=35.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
    TradingProfile.LISTING: _TradePipelineProfile(
        buffer_size=768,
        warning=256,
        critical=384,
        degradation_hold_s=50.0,
        fallback=FallbackMode.SKIP,
        chronic_threshold=5,
        chronic_window_s=45.0,
    ),
    TradingProfile.AUTO: _TradePipelineProfile(
        buffer_size=640,
        warning=200,
        critical=320,
        degradation_hold_s=30.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
}


class TradesStreamPipeline(StreamPipeline[Trade]):
    @classmethod
    def buffer_size(cls, profile: TradingProfile) -> int:
        return _PROFILE_SETTINGS.get(profile, _PROFILE_SETTINGS[TradingProfile.AUTO]).buffer_size

    def __init__(
        self,
        *,
        name: str,
        buffer: StreamBuffer[Trade],
        profile: TradingProfile,
        on_chronic_error,
        metrics_symbol: Optional[str] = None,
    ) -> None:
        settings = _PROFILE_SETTINGS.get(profile, _PROFILE_SETTINGS[TradingProfile.AUTO])
        super().__init__(
            name=name,
            buffer=buffer,
            thresholds=AlarmThresholds(settings.warning, settings.critical),
            fallback_mode=settings.fallback,
            aggregator=self._aggregate if settings.fallback is FallbackMode.AGGREGATE else None,
            degradation_hold_s=settings.degradation_hold_s,
            chronic_error_threshold=settings.chronic_threshold,
            chronic_error_window_s=settings.chronic_window_s,
            on_chronic_error=on_chronic_error,
            metrics_stream=name,
            metrics_symbol=metrics_symbol,
        )

    @staticmethod
    def _aggregate(current: Trade, incoming: Trade) -> Trade:
        return Trade(
            trade_id=incoming.trade_id,
            exchange=incoming.exchange,
            symbol=incoming.symbol,
            executed_at=incoming.executed_at,
            price=incoming.price,
            quantity=incoming.quantity,
            side=incoming.side,
        )

*** End of File
