from __future__ import annotations

from dataclasses import dataclass


REJECTION_FUNNEL_VERSION = "strategy_rejection_funnel_v1"
REJECTION_FUNNEL_TEMPORAL_CONTRACT = "artifact_driven_lineage_no_future_features"


@dataclass(frozen=True, slots=True)
class RejectionFunnelRow:
    funnel_version: str
    run_id: str
    strategy_name: str
    strategy_version: str
    strategy_contract_version: str
    target_horizon_minutes: int | str
    stage: str
    source_artifact: str
    row_key: str
    event_id: str
    symbol: str
    snapshot_time_ms: int | str
    status: str
    reason_code: str
    reason_detail: str
    upstream_stage: str
    downstream_stage: str
    row_count: int
    temporal_contract: str = REJECTION_FUNNEL_TEMPORAL_CONTRACT

    def __post_init__(self) -> None:
        if not self.funnel_version:
            raise ValueError("funnel_version is required")
        if not self.stage:
            raise ValueError("stage is required")
        if self.status not in {"INCLUDED", "EXCLUDED", "SKIPPED"}:
            raise ValueError("status must be INCLUDED, EXCLUDED, or SKIPPED")
        if self.status == "EXCLUDED" and not self.reason_code:
            raise ValueError("excluded funnel rows require reason_code")
        if self.row_count < 0:
            raise ValueError("row_count must be non-negative")
