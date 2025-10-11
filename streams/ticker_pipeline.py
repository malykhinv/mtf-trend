from __future__ import annotations

from dataclasses import dataclass

from config.models.trading_profile import TradingProfile
from data.exchanges import StreamBuffer
from data.exchanges.base import BestBidAsk

from .base import AlarmThresholds, FallbackMode, StreamPipeline


@dataclass(frozen=True)
class _TickerPipelineProfile:
    buffer_size: int
    warning: int
    critical: int
    degradation_hold_s: float
    fallback: FallbackMode
    chronic_threshold: int
    chronic_window_s: float


_PROFILE_SETTINGS: dict[TradingProfile, _TickerPipelineProfile] = {
    TradingProfile.TOP: _TickerPipelineProfile(
        buffer_size=512,
        warning=128,
        critical=240,
        degradation_hold_s=20.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=3,
        chronic_window_s=20.0,
    ),
    TradingProfile.ALT: _TickerPipelineProfile(
        buffer_size=640,
        warning=192,
        critical=320,
        degradation_hold_s=35.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
    TradingProfile.LISTING: _TickerPipelineProfile(
        buffer_size=768,
        warning=256,
        critical=384,
        degradation_hold_s=45.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=5,
        chronic_window_s=45.0,
    ),
    TradingProfile.AUTO: _TickerPipelineProfile(
        buffer_size=640,
        warning=192,
        critical=320,
        degradation_hold_s=30.0,
        fallback=FallbackMode.AGGREGATE,
        chronic_threshold=4,
        chronic_window_s=30.0,
    ),
}


class BookTickerStreamPipeline(StreamPipeline[BestBidAsk]):
    @classmethod
    def buffer_size(cls, profile: TradingProfile) -> int:
        return _PROFILE_SETTINGS.get(profile, _PROFILE_SETTINGS[TradingProfile.AUTO]).buffer_size

    def __init__(
        self,
        *,
        name: str,
        buffer: StreamBuffer[BestBidAsk],
        profile: TradingProfile,
        on_chronic_error,
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
        )

    @staticmethod
    def _aggregate(current: BestBidAsk, incoming: BestBidAsk) -> BestBidAsk:
        return BestBidAsk(
            exchange=incoming.exchange,
            symbol=incoming.symbol,
            bid_price=incoming.bid_price,
            bid_quantity=incoming.bid_quantity,
            ask_price=incoming.ask_price,
            ask_quantity=incoming.ask_quantity,
            event_time=incoming.event_time,
        )

*** End of File
