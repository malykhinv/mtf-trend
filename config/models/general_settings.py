from dataclasses import dataclass

from .exchange_name import ExchangeName
from .trading_profile import TradingProfile


@dataclass(frozen=True)
class GeneralSettings:
    exchange: ExchangeName
    profile: TradingProfile
    loop_interval_ms: int
    ws_silence_timeout_ms: int
    depth_stream_interval_ms: int
    depth_snapshot_limit: int
    odr_smooth_samples: int
    recent_band_s: int
    recent_band_s_vol_boost: int
    vol_spike_mult: float


__all__ = ["GeneralSettings"]
