from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from config.models.trading_profile import TradingProfile
from data.exchanges import StreamBuffer
from data.exchanges.base import DepthStreamData
from domain.models import OrderBookSnapshot, OrderBookUpdate

from .base import AlarmThresholds, FallbackMode, StreamPipeline


@dataclass(frozen=True)
class _DepthPipelineProfile:
    buffer_size: int
    warning: int
    critical: int
    degradation_hold_s: float
    fallback: FallbackMode
    chronic_threshold: int
    chronic_window_s: float


_PROFILE_SETTINGS: dict[TradingProfile, _DepthPipelineProfile] = {
    TradingProfile.TOP: _DepthPipelineProfile(
        buffer_size=640,
        warning=160,
        critical=320,
        degradation_hold_s=20.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=3,
        chronic_window_s=20.0,
    ),
    TradingProfile.ALT: _DepthPipelineProfile(
        buffer_size=768,
        warning=256,
        critical=448,
        degradation_hold_s=40.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
    TradingProfile.LISTING: _DepthPipelineProfile(
        buffer_size=896,
        warning=384,
        critical=640,
        degradation_hold_s=60.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=5,
        chronic_window_s=45.0,
    ),
    TradingProfile.AUTO: _DepthPipelineProfile(
        buffer_size=768,
        warning=240,
        critical=448,
        degradation_hold_s=30.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
}


class DepthStreamPipeline(StreamPipeline[DepthStreamData]):
    @classmethod
    def buffer_size(cls, profile: TradingProfile) -> int:
        return _PROFILE_SETTINGS.get(profile, _PROFILE_SETTINGS[TradingProfile.AUTO]).buffer_size

    def __init__(
        self,
        *,
        name: str,
        buffer: StreamBuffer[DepthStreamData],
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
            aggregator=self._aggregate,
            degradation_hold_s=settings.degradation_hold_s,
            chronic_error_threshold=settings.chronic_threshold,
            chronic_error_window_s=settings.chronic_window_s,
            on_chronic_error=on_chronic_error,
            metrics_stream=name,
            metrics_symbol=metrics_symbol,
        )

    @staticmethod
    def _aggregate(current: DepthStreamData, incoming: DepthStreamData) -> DepthStreamData:
        if isinstance(current, OrderBookUpdate) and isinstance(incoming, OrderBookUpdate):
            first_id = min(current.first_update_id, incoming.first_update_id)
            last_id = max(current.last_update_id, incoming.last_update_id)
            return OrderBookUpdate(
                exchange=incoming.exchange,
                symbol=incoming.symbol,
                first_update_id=first_id,
                last_update_id=last_id,
                bids=incoming.bids,
                asks=incoming.asks,
                event_time=incoming.event_time,
            )
        if isinstance(incoming, OrderBookSnapshot):
            return incoming
        return incoming

*** End of File
