from dataclasses import dataclass

from .exchange_name import ExchangeName
from .trading_profile import TradingProfile


@dataclass(frozen=True)
class GeneralSettings:
    exchange: ExchangeName
    profile: TradingProfile
    loop_interval_ms: int
    ws_silence_timeout_ms: int
    binance_depth_update_ms: int
    odr_smooth_samples: int
    recent_band_s: int
    recent_band_s_vol_boost: int
    vol_spike_mult: float
    orderbook_snapshot_timeout_s: float
    enable_detailed_diagnostics: bool
    trade_gap_threshold: int


__all__ = ["GeneralSettings"]
