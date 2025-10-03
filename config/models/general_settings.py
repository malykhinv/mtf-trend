"""Configuration model for general settings."""
from dataclasses import dataclass

from .exchange_name import ExchangeName
from .trading_profile import TradingProfile


@dataclass(frozen=True)
class GeneralSettings:
    exchange: ExchangeName
    profile: TradingProfile
    loop_interval_ms: int
    odr_smooth_samples: int
    recent_band_s: int
    recent_band_s_vol_boost: int
    vol_spike_mult: float


__all__ = ["GeneralSettings"]
