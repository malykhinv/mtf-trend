"""Outcome-free Stage-0 audit for pump-lifecycle expert labels."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.annotation.desk.labels import LabelStore
from anomaly_science.annotation.review_questions import validate_review_answers
from anomaly_science.annotation.schemas import validate_label_payload
from anomaly_science.strategy.pump_wave_short.lifecycle_review import (
    IS_END_EXCLUSIVE_MS,
    LIFECYCLE_PROTOCOL_FREEZE_ID,
    PUMP_LIFECYCLE_REVIEW_QUESTIONS,
    REVIEW_DIR,
)


LABEL_AUDIT_FREEZE_ID = "pump_lifecycle_stage0_label_audit_20260803_v1"
PILOT_IDENTITY_COLUMNS: tuple[str, ...] = (
    "event_id",
    "source_event_id",
    "symbol",
    "tf",
    "culmination_ms",
    "selection_hash",
)
# Set only after the exact outcome-free 70-card pilot is materialized.  The
# canonical identity hash is independent of parquet serialization metadata.
REGISTERED_PILOT_IDENTITY_SHA256 = "83392c169ce9a6ac966d7276d8be16d2368927d73b3cc1c7da3862c8c172f64f"


@dataclass(frozen=True, slots=True)
class LifecycleLabelAuditConfig:
    minimum_substantive_note_characters: int = 20
    minimum_multiwave_examples: int = 20
    minimum_non_multiwave_examples: int = 20

    def __post_init__(self) -> None:
        if self.minimum_substantive_note_characters <= 0:
            raise ValueError("minimum_substantive_note_characters must be positive")
        if self.minimum_multiwave_examples <= 0 or self.minimum_non_multiwave_examples <= 0:
            raise ValueError("class-support minima must be positive")


@dataclass(frozen=True, slots=True)
class LifecycleLabelAuditReport:
    status: str
    protocol_freeze_id: str
    label_audit_freeze_id: str
    pilot_identity_sha256: str
    pilot_identity_matches_freeze: bool
    candidate_rows: int
    effective_label_rows: int
    completion_fraction: float
    unknown_label_event_ids: tuple[str, ...]
    invalid_label_event_ids: tuple[str, ...]
    non_manual_label_event_ids: tuple[str, ...]
    incomplete_codebook_event_ids: tuple[str, ...]
    inconsistent_codebook_event_ids: tuple[str, ...]
    insufficient_notes_event_ids: tuple[str, ...]
    wave_count_histogram: dict[str, int]
    multiwave_examples: int
    non_multiwave_examples: int
    untouched_2026_rows_used: int
    gates: dict[str, bool]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def pilot_identity_sha256(candidates: pd.DataFrame) -> str:
    missing = sorted(set(PILOT_IDENTITY_COLUMNS) - set(candidates.columns))
    if missing:
        raise ValueError(f"pilot identity columns missing: {missing}")
    frame = candidates.loc[:, PILOT_IDENTITY_COLUMNS].copy()
    if frame["event_id"].astype(str).duplicated().any():
        raise ValueError("candidate event_id must be unique")
    frame = frame.sort_values("event_id")
    rows = []
    for row in frame.to_dict("records"):
        normalized = {
            key: int(value) if key == "culmination_ms" else str(value)
            for key, value in row.items()
        }
        rows.append(json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True))
    return hashlib.sha256(("\n".join(rows) + "\n").encode("utf-8")).hexdigest()


def _maximum_wave_count(label: dict[str, Any]) -> int:
    if label.get("no_setup") is True:
        return 0
    counts = []
    for setup in label.get("setups") or []:
        waves = setup.get("pump_waves")
        if isinstance(waves, list):
            counts.append(len(waves))
        elif bool(setup.get("has_pump_transition")):
            counts.append(1)
    return max(counts, default=0)


def _has_substantive_note(label: dict[str, Any], minimum_characters: int) -> bool:
    note = str(label.get("review_notes") or "").strip()
    return len(note) >= minimum_characters and not note.upper().startswith("BOT:")


def _codebook_is_consistent(label: dict[str, Any], wave_count: int) -> bool:
    answers = label.get("review_answers") or {}
    separation = str(answers.get("wave_separation") or "")
    if wave_count >= 2:
        return separation != "not_applicable"
    return separation == "not_applicable"


def audit_lifecycle_labels(
    candidates: pd.DataFrame,
    labels: dict[str, dict[str, Any]],
    *,
    config: LifecycleLabelAuditConfig = LifecycleLabelAuditConfig(),
) -> LifecycleLabelAuditReport:
    fingerprint = pilot_identity_sha256(candidates)
    candidate_ids = set(candidates["event_id"].astype(str))
    label_ids = set(map(str, labels))
    unknown_ids = tuple(sorted(label_ids - candidate_ids))
    effective_ids = candidate_ids & label_ids

    invalid: list[str] = []
    non_manual: list[str] = []
    incomplete_codebook: list[str] = []
    inconsistent_codebook: list[str] = []
    insufficient_notes: list[str] = []
    wave_counts: list[int] = []
    for event_id in sorted(effective_ids):
        label = labels[event_id]
        try:
            validate_label_payload(label)
        except (TypeError, ValueError):
            invalid.append(event_id)
            continue
        if str(label.get("source") or "") != "browser_level_labeler":
            non_manual.append(event_id)
        try:
            validate_review_answers(label, PUMP_LIFECYCLE_REVIEW_QUESTIONS)
        except (TypeError, ValueError):
            incomplete_codebook.append(event_id)
        if not _has_substantive_note(label, config.minimum_substantive_note_characters):
            insufficient_notes.append(event_id)
        wave_count = _maximum_wave_count(label)
        if not _codebook_is_consistent(label, wave_count):
            inconsistent_codebook.append(event_id)
        wave_counts.append(wave_count)

    histogram = {str(count): wave_counts.count(count) for count in sorted(set(wave_counts))}
    multiwave = sum(count >= 2 for count in wave_counts)
    non_multiwave = sum(count < 2 for count in wave_counts)
    completion = len(effective_ids) / len(candidate_ids) if candidate_ids else 0.0
    untouched_2026 = int((pd.to_numeric(candidates["review_end_ms"], errors="raise") >= IS_END_EXCLUSIVE_MS).sum())
    gates = {
        "pilot_identity": fingerprint == REGISTERED_PILOT_IDENTITY_SHA256,
        "all_candidates_reviewed": effective_ids == candidate_ids,
        "no_unknown_labels": not unknown_ids,
        "schema_valid": not invalid,
        "manual_source_only": not non_manual,
        "codebook_complete": not incomplete_codebook,
        "codebook_consistent": not inconsistent_codebook,
        "substantive_notes_complete": not insufficient_notes,
        "multiwave_support": multiwave >= config.minimum_multiwave_examples,
        "non_multiwave_support": non_multiwave >= config.minimum_non_multiwave_examples,
        "is_only": untouched_2026 == 0,
    }
    hard_quality_gates = (
        "pilot_identity",
        "all_candidates_reviewed",
        "no_unknown_labels",
        "schema_valid",
        "manual_source_only",
        "codebook_complete",
        "codebook_consistent",
        "substantive_notes_complete",
        "is_only",
    )
    if not gates["pilot_identity"]:
        status = "PILOT_IDENTITY_MISMATCH"
    elif not gates["all_candidates_reviewed"]:
        status = "ANNOTATION_INCOMPLETE"
    elif not all(gates[name] for name in hard_quality_gates):
        status = "ANNOTATION_QUALITY_GATES_FAIL"
    elif not gates["multiwave_support"] or not gates["non_multiwave_support"]:
        status = "EXPAND_OUTCOME_FREE_PILOT_FOR_CLASS_SUPPORT"
    else:
        status = "READY_FOR_BLINDED_RELIABILITY_REVIEW"

    return LifecycleLabelAuditReport(
        status=status,
        protocol_freeze_id=LIFECYCLE_PROTOCOL_FREEZE_ID,
        label_audit_freeze_id=LABEL_AUDIT_FREEZE_ID,
        pilot_identity_sha256=fingerprint,
        pilot_identity_matches_freeze=gates["pilot_identity"],
        candidate_rows=len(candidate_ids),
        effective_label_rows=len(effective_ids),
        completion_fraction=completion,
        unknown_label_event_ids=unknown_ids,
        invalid_label_event_ids=tuple(invalid),
        non_manual_label_event_ids=tuple(non_manual),
        incomplete_codebook_event_ids=tuple(incomplete_codebook),
        inconsistent_codebook_event_ids=tuple(inconsistent_codebook),
        insufficient_notes_event_ids=tuple(insufficient_notes),
        wave_count_histogram=histogram,
        multiwave_examples=multiwave,
        non_multiwave_examples=non_multiwave,
        untouched_2026_rows_used=untouched_2026,
        gates=gates,
    )


def run_lifecycle_label_audit(
    *,
    review_dir: Path = REVIEW_DIR,
    output_dir: Path | None = None,
    config: LifecycleLabelAuditConfig = LifecycleLabelAuditConfig(),
) -> LifecycleLabelAuditReport:
    candidates = pd.read_parquet(review_dir / "candidates.parquet")
    labels = LabelStore(review_dir / "review_labels.jsonl").read_effective()
    report = audit_lifecycle_labels(candidates, labels, config=config)
    target = output_dir or review_dir / "label_audit"
    target.mkdir(parents=True, exist_ok=True)
    (target / "report.json").write_text(
        json.dumps(report.as_dict(), indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit pump-lifecycle Stage-0 expert labels.")
    parser.add_argument("--review-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument("--output-dir", type=Path, default=None)
    args = parser.parse_args()
    report = run_lifecycle_label_audit(review_dir=args.review_dir, output_dir=args.output_dir)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
