from __future__ import annotations

from dataclasses import dataclass


class GovernanceContractError(ValueError):
    """Raised when research governance rows violate the MVP1 contract."""


@dataclass(frozen=True, slots=True)
class ResearchLedgerRow:
    ledger_version: str
    protocol_freeze_id: str
    created_at_utc: str
    research_stage: str
    decision: str
    artifact: str
    rationale: str
    holdout_access_allowed_after_freeze: bool
    final_holdout_start_date: str
    final_holdout_end_date: str

    def __post_init__(self) -> None:
        for field_name in (
            "ledger_version",
            "protocol_freeze_id",
            "created_at_utc",
            "research_stage",
            "decision",
            "artifact",
            "rationale",
            "final_holdout_start_date",
            "final_holdout_end_date",
        ):
            if not getattr(self, field_name):
                raise GovernanceContractError(f"{field_name} is required")


@dataclass(frozen=True, slots=True)
class HoldoutAccessLogRow:
    access_id: str
    protocol_freeze_id: str
    accessed_at_utc: str
    actor: str
    reason: str
    artifact: str
    final_holdout_start_date: str
    final_holdout_end_date: str
    access_approved: bool

    def __post_init__(self) -> None:
        for field_name in (
            "access_id",
            "protocol_freeze_id",
            "accessed_at_utc",
            "actor",
            "reason",
            "artifact",
            "final_holdout_start_date",
            "final_holdout_end_date",
        ):
            if not getattr(self, field_name):
                raise GovernanceContractError(f"{field_name} is required")
