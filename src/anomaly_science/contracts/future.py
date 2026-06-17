from __future__ import annotations

import math
from dataclasses import dataclass

from .market import MarketDataContractError
from .time import enforce_snapshot_contract, validate_timestamp_ms

BARRIER_RESOLUTION_NONE = "none"
BARRIER_RESOLUTION_STOP_LOSS_FIRST = "stop_loss_first"
VALID_BARRIER_RESOLUTIONS = frozenset((BARRIER_RESOLUTION_NONE, BARRIER_RESOLUTION_STOP_LOSS_FIRST))


@dataclass(frozen=True, slots=True)
class FuturePathRow:
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    future_start_time_ms: int
    atr_window_minutes: int = 1440
    ATR_1d_asof_t: float | None = None
    ATR_1d_pct_asof_t: float | None = None
    double_barrier_k_continuation: float | None = None
    double_barrier_k_fade: float | None = None
    future_return_5m: float | None = None
    future_return_15m: float | None = None
    future_return_30m: float | None = None
    future_return_60m: float | None = None
    future_return_120m: float | None = None
    future_max_5m: float | None = None
    future_max_15m: float | None = None
    future_max_30m: float | None = None
    future_max_60m: float | None = None
    future_max_120m: float | None = None
    future_min_5m: float | None = None
    future_min_15m: float | None = None
    future_min_30m: float | None = None
    future_min_60m: float | None = None
    future_min_120m: float | None = None
    future_return_atr_5m: float | None = None
    future_return_atr_15m: float | None = None
    future_return_atr_30m: float | None = None
    future_return_atr_60m: float | None = None
    future_return_atr_120m: float | None = None
    future_max_atr_5m: float | None = None
    future_max_atr_15m: float | None = None
    future_max_atr_30m: float | None = None
    future_max_atr_60m: float | None = None
    future_max_atr_120m: float | None = None
    future_min_atr_5m: float | None = None
    future_min_atr_15m: float | None = None
    future_min_atr_30m: float | None = None
    future_min_atr_60m: float | None = None
    future_min_atr_120m: float | None = None
    intracandle_double_barrier_hit_5m: bool | None = None
    intracandle_double_barrier_hit_15m: bool | None = None
    intracandle_double_barrier_hit_30m: bool | None = None
    intracandle_double_barrier_hit_60m: bool | None = None
    intracandle_double_barrier_hit_120m: bool | None = None
    barrier_resolution_5m: str | None = None
    barrier_resolution_15m: str | None = None
    barrier_resolution_30m: str | None = None
    barrier_resolution_60m: str | None = None
    barrier_resolution_120m: str | None = None
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
        if self.atr_window_minutes <= 0:
            raise MarketDataContractError("atr_window_minutes must be positive")
        self._validate_optional_positive_float("ATR_1d_asof_t")
        self._validate_optional_positive_float("ATR_1d_pct_asof_t")
        if (self.ATR_1d_asof_t is None) != (self.ATR_1d_pct_asof_t is None):
            raise MarketDataContractError("ATR_1d_asof_t and ATR_1d_pct_asof_t must be present or missing together")
        self._validate_optional_positive_float("double_barrier_k_continuation")
        self._validate_optional_positive_float("double_barrier_k_fade")
        if (self.double_barrier_k_continuation is None) != (self.double_barrier_k_fade is None):
            raise MarketDataContractError("double barrier ATR thresholds must be present or missing together")
        for horizon in (5, 15, 30, 60, 120):
            self._validate_barrier_resolution(horizon)
        for field_name in ("time_to_new_high_minutes", "time_to_structural_break_minutes"):
            value = getattr(self, field_name)
            if value is not None and value < 0:
                raise MarketDataContractError(f"{field_name} must be non-negative when present")
        for field_name in (
            "future_return_5m",
            "future_return_15m",
            "future_return_30m",
            "future_return_60m",
            "future_return_120m",
            "future_max_5m",
            "future_max_15m",
            "future_max_30m",
            "future_max_60m",
            "future_max_120m",
            "future_min_5m",
            "future_min_15m",
            "future_min_30m",
            "future_min_60m",
            "future_min_120m",
            "future_return_atr_5m",
            "future_return_atr_15m",
            "future_return_atr_30m",
            "future_return_atr_60m",
            "future_return_atr_120m",
            "future_max_atr_5m",
            "future_max_atr_15m",
            "future_max_atr_30m",
            "future_max_atr_60m",
            "future_max_atr_120m",
            "future_min_atr_5m",
            "future_min_atr_15m",
            "future_min_atr_30m",
            "future_min_atr_60m",
            "future_min_atr_120m",
        ):
            self._validate_optional_finite_float(field_name)

    def _validate_barrier_resolution(self, horizon_minutes: int) -> None:
        hit = getattr(self, f"intracandle_double_barrier_hit_{horizon_minutes}m")
        resolution = getattr(self, f"barrier_resolution_{horizon_minutes}m")
        if hit is None:
            if resolution is not None:
                raise MarketDataContractError(
                    f"barrier_resolution_{horizon_minutes}m must be missing when double-barrier hit is unknown"
                )
            return
        if not isinstance(hit, bool):
            raise MarketDataContractError(f"intracandle_double_barrier_hit_{horizon_minutes}m must be bool when present")
        if resolution not in VALID_BARRIER_RESOLUTIONS:
            raise MarketDataContractError(f"barrier_resolution_{horizon_minutes}m has unknown value: {resolution!r}")
        if hit and resolution != BARRIER_RESOLUTION_STOP_LOSS_FIRST:
            raise MarketDataContractError(
                f"barrier_resolution_{horizon_minutes}m must be stop_loss_first when double-barrier hit is true"
            )
        if not hit and resolution != BARRIER_RESOLUTION_NONE:
            raise MarketDataContractError(
                f"barrier_resolution_{horizon_minutes}m must be none when double-barrier hit is false"
            )

    def _validate_optional_positive_float(self, field_name: str) -> None:
        value = getattr(self, field_name)
        if value is not None and (not math.isfinite(value) or value <= 0):
            raise MarketDataContractError(f"{field_name} must be positive and finite when present")

    def _validate_optional_finite_float(self, field_name: str) -> None:
        value = getattr(self, field_name)
        if value is not None and not math.isfinite(value):
            raise MarketDataContractError(f"{field_name} must be finite when present")
