from __future__ import annotations

from dataclasses import dataclass

from .market import MarketDataContractError
from .time import validate_timestamp_ms


@dataclass(frozen=True, slots=True)
class AnomalyEvent:
    event_id: str
    symbol: str
    event_start_time_ms: int
    event_detection_time_ms: int
    seed_time_ms: int
    seed_open: float
    seed_high: float
    seed_low: float
    seed_close: float
    initial_move_pct: float
    initial_volume_zscore: float | None
    initial_quote_volume_zscore: float | None
    initial_trade_count_zscore: float | None
    trigger_component: str
    trigger_components: tuple[str, ...]
    detector_version: str
    technical_noise_shock: bool = False
    raw_candle_gap_minutes: float | None = None
    excluded_by_data_quality_gate: bool = False
    daily_return_asof_t: float | None = None
    trade_count_market_percentile_asof_t: float | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.event_start_time_ms, field_name="event_start_time_ms")
        validate_timestamp_ms(self.event_detection_time_ms, field_name="event_detection_time_ms")
        validate_timestamp_ms(self.seed_time_ms, field_name="seed_time_ms")
        if self.event_detection_time_ms < self.event_start_time_ms:
            raise MarketDataContractError("event_detection_time_ms must be >= event_start_time_ms")
        if self.seed_time_ms < self.event_start_time_ms:
            raise MarketDataContractError("seed_time_ms must be >= event_start_time_ms")
        if not self.trigger_component:
            raise MarketDataContractError("trigger_component is required")
        if not self.trigger_components:
            raise MarketDataContractError("trigger_components is required")
        if self.trigger_component != self.trigger_components[0]:
            raise MarketDataContractError("trigger_component must equal first trigger_components item")
        if any(not component for component in self.trigger_components):
            raise MarketDataContractError("trigger_components must not contain empty values")
        if len(set(self.trigger_components)) != len(self.trigger_components):
            raise MarketDataContractError("trigger_components must not contain duplicates")
        if not self.detector_version:
            raise MarketDataContractError("detector_version is required")
        if self.raw_candle_gap_minutes is not None and self.raw_candle_gap_minutes <= 0:
            raise MarketDataContractError("raw_candle_gap_minutes must be positive when present")
        if self.technical_noise_shock and not self.excluded_by_data_quality_gate:
            raise MarketDataContractError("technical_noise_shock events must be excluded by data-quality gate")
        for field_name in ("daily_return_asof_t", "trade_count_market_percentile_asof_t"):
            value = getattr(self, field_name)
            if value is not None and (not isinstance(value, (int, float)) or value != value):
                raise MarketDataContractError(f"{field_name} must be finite when present")
        if self.trade_count_market_percentile_asof_t is not None and not 0.0 <= self.trade_count_market_percentile_asof_t <= 1.0:
            raise MarketDataContractError("trade_count_market_percentile_asof_t must be in [0, 1] when present")
        for field_name in ("seed_open", "seed_high", "seed_low", "seed_close"):
            value = getattr(self, field_name)
            if value <= 0:
                raise MarketDataContractError(f"{field_name} must be positive")
        if self.seed_high < max(self.seed_open, self.seed_close):
            raise MarketDataContractError("seed_high must be >= max(seed_open, seed_close)")
        if self.seed_low > min(self.seed_open, self.seed_close):
            raise MarketDataContractError("seed_low must be <= min(seed_open, seed_close)")
