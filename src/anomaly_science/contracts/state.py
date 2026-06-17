from __future__ import annotations

from dataclasses import dataclass

from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms


@dataclass(frozen=True, slots=True)
class AnomalyState1mRow:
    event_id: str
    symbol: str
    state_time_ms: int
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    minutes_since_event_start: int
    minutes_since_detection: int
    event_alive: bool
    running_high_asof_t: float
    running_high_time_asof_t_ms: int
    running_low_asof_t: float
    running_low_time_asof_t_ms: int
    time_since_running_high_minutes: int
    current_close: float
    current_return_from_start: float
    distance_to_running_high: float
    distance_to_running_low: float
    distance_to_structural_low: float | None
    distance_to_structural_high: float | None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.state_time_ms, field_name="state_time_ms")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if self.state_time_ms != self.snapshot_time_ms:
            raise MarketDataContractError("state_time_ms must equal snapshot_time_ms for MVP online 1m state rows")
        if self.minutes_since_event_start < 0 or self.minutes_since_detection < 0:
            raise MarketDataContractError("minutes_since_* fields must be non-negative")
        if self.running_high_asof_t <= 0 or self.running_low_asof_t <= 0 or self.current_close <= 0:
            raise MarketDataContractError("price fields must be positive")
        if self.running_low_asof_t > self.running_high_asof_t:
            raise MarketDataContractError("running_low_asof_t must be <= running_high_asof_t")
        validate_timestamp_ms(self.running_high_time_asof_t_ms, field_name="running_high_time_asof_t_ms")
        validate_timestamp_ms(self.running_low_time_asof_t_ms, field_name="running_low_time_asof_t_ms")
        if self.running_high_time_asof_t_ms > self.state_time_ms:
            raise MarketDataContractError("running_high_time_asof_t_ms must be <= state_time_ms")
        if self.running_low_time_asof_t_ms > self.state_time_ms:
            raise MarketDataContractError("running_low_time_asof_t_ms must be <= state_time_ms")
