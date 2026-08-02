"""Contracts for event-scoped high-resolution data requested after selection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from anomaly_science.contracts.time import enforce_snapshot_contract, validate_timestamp_ms

HighResolutionSource = Literal["candles_1s", "aggtrades"]


class EventEnrichmentContractError(ValueError):
    """Raised when event-scoped enrichment could contaminate event selection."""


@dataclass(frozen=True, slots=True)
class CausalEventSelection:
    event_id: str
    symbol: str
    selection_snapshot_time_ms: int
    selection_feature_cutoff_time_ms: int
    selection_granularity_ms: int
    selection_schema_version: str

    def __post_init__(self) -> None:
        if not self.event_id or not self.symbol or not self.selection_schema_version:
            raise EventEnrichmentContractError(
                "event_id, symbol, and selection_schema_version are required"
            )
        validate_timestamp_ms(
            self.selection_snapshot_time_ms,
            field_name="selection_snapshot_time_ms",
        )
        validate_timestamp_ms(
            self.selection_feature_cutoff_time_ms,
            field_name="selection_feature_cutoff_time_ms",
        )
        enforce_snapshot_contract(
            snapshot_time_ms=self.selection_snapshot_time_ms,
            feature_cutoff_time_ms=self.selection_feature_cutoff_time_ms,
        )
        if self.selection_granularity_ms < 60_000:
            raise EventEnrichmentContractError(
                "event selection must be fixed before sub-minute enrichment is available"
            )


@dataclass(frozen=True, slots=True)
class EventScopedEnrichmentRequest:
    request_version: str
    selection: CausalEventSelection
    source: HighResolutionSource
    enrichment_granularity_ms: int
    requested_start_time_ms: int
    requested_end_time_ms_exclusive: int

    def __post_init__(self) -> None:
        if not self.request_version:
            raise EventEnrichmentContractError("request_version is required")
        if self.enrichment_granularity_ms <= 0:
            raise EventEnrichmentContractError("enrichment_granularity_ms must be positive")
        if self.enrichment_granularity_ms >= self.selection.selection_granularity_ms:
            raise EventEnrichmentContractError(
                "enrichment granularity must be finer than selection granularity"
            )
        validate_timestamp_ms(
            self.requested_start_time_ms,
            field_name="requested_start_time_ms",
        )
        validate_timestamp_ms(
            self.requested_end_time_ms_exclusive,
            field_name="requested_end_time_ms_exclusive",
        )
        if self.requested_start_time_ms >= self.requested_end_time_ms_exclusive:
            raise EventEnrichmentContractError(
                "requested_start_time_ms must be before requested_end_time_ms_exclusive"
            )


__all__ = [
    "CausalEventSelection",
    "EventEnrichmentContractError",
    "EventScopedEnrichmentRequest",
    "HighResolutionSource",
]
