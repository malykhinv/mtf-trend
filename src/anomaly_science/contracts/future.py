from __future__ import annotations

from dataclasses import dataclass

from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms


@dataclass(frozen=True, slots=True)
class FuturePathRow:
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int
    future_return_5m: float | None = None
    future_return_15m: float | None = None
    future_return_30m: float | None = None
    future_return_60m: float | None = None
    future_max_5m: float | None = None
    future_max_15m: float | None = None
    future_max_30m: float | None = None
    future_max_60m: float | None = None
    future_min_5m: float | None = None
    future_min_15m: float | None = None
    future_min_30m: float | None = None
    future_min_60m: float | None = None
    reclaimed_running_high_30m: bool | None = None
    reclaimed_running_high_60m: bool | None = None
    broke_structural_low_30m: bool | None = None
    broke_structural_low_60m: bool | None = None
    time_to_new_high_minutes: int | None = None
    time_to_structural_break_minutes: int | None = None

    def __post_init__(self) -> None:
        if not self.event_id:
            raise MarketDataContractError("event_id is required")
        if not self.symbol:
            raise MarketDataContractError("symbol is required")
        validate_timestamp_ms(self.snapshot_time_ms, field_name="snapshot_time_ms")
        validate_timestamp_ms(self.feature_cutoff_time_ms, field_name="feature_cutoff_time_ms")
        validate_timestamp_ms(self.future_start_time_ms, field_name="future_start_time_ms")
        enforce_snapshot_contract(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
            future_start_time_ms=self.future_start_time_ms,
        )
        for field_name in ("time_to_new_high_minutes", "time_to_structural_break_minutes"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise MarketDataContractError(f"{field_name} must be non-negative when present")
