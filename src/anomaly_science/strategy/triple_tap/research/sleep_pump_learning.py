"""Materialise expert sleep→pump annotations as an auditable learning dataset.

This module intentionally stops before model fitting.  The first labelled queue
was selected by a broad discovery rule, so it can measure and improve that
rule inside its review population, but it cannot calibrate a deployment-rate
probability.  A separately sampled, untouched holdout is required for that.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.annotation.desk.labels import LabelStore


LEARNING_VERSION = "sleep_pump_expert_learning_v2_2026-07-13"
REVIEW_DIR = Path(".output/results/triple_tap_v1/sleep_pump_review")


@dataclass(frozen=True, slots=True)
class SleepPumpLearningSummary:
    candidate_count: int
    reviewed_count: int
    unreviewed_count: int
    positive_count: int
    negative_count: int
    positive_rate: float
    manual_setup_count: int
    structural_setup_count: int


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pump_setups(label: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        setup
        for setup in label.get("setups", [])
        if bool(setup.get("has_pump_transition"))
        and setup.get("pump_start_ms") is not None
        and setup.get("culmination_ms") is not None
    ]


def _is_manual_setup(setup: dict[str, Any]) -> bool:
    """Seed annotations retain their explicit bot provenance in their note."""

    return "BOT sleep" not in str(setup.get("notes") or "")


def build_learning_frame(
    candidates: pd.DataFrame,
    labels: dict[str, dict[str, Any]],
) -> pd.DataFrame:
    """Join effective annotations to immutable candidate-time features.

    ``expert_has_sleep_pump`` is a card-level label.  A card may legitimately
    contain several structural ideas; the detector task only asks whether the
    expert retained at least one valid sleep→pump transition.
    """

    if candidates["event_id"].duplicated().any():
        raise ValueError("sleep-pump candidates must have unique event_id values")
    candidate_by_id = candidates.set_index("event_id", drop=False)
    rows: list[dict[str, Any]] = []
    seen_candidate_ids: set[str] = set()

    for label_event_id, label in labels.items():
        candidate_id = str(label.get("source_event_id") or "")
        if candidate_id not in candidate_by_id.index:
            raise ValueError(f"label {label_event_id} references unknown candidate {candidate_id!r}")
        if candidate_id in seen_candidate_ids:
            raise ValueError(f"multiple effective labels reference candidate {candidate_id!r}")
        seen_candidate_ids.add(candidate_id)

        setups = [setup for setup in label.get("setups", []) if isinstance(setup, dict)]
        pumps = _pump_setups(label)
        manual_setups = [setup for setup in setups if _is_manual_setup(setup)]
        structural_setups = [
            setup
            for setup in setups
            if bool(setup.get("has_level")) or bool(setup.get("has_structure_break"))
        ]
        candidate = candidate_by_id.loc[candidate_id].to_dict()
        final_pump = pumps[-1] if pumps else None
        candidate_geometry_retained = bool(
            any(
                int(setup["pump_start_ms"]) == int(candidate["pump_start_ms"])
                and int(setup["culmination_ms"]) == int(candidate["culmination_ms"])
                for setup in pumps
            )
        )
        if not pumps:
            annotation_outcome = "rejected"
        elif candidate_geometry_retained and len(pumps) == 1:
            annotation_outcome = "accepted_as_proposed"
        else:
            annotation_outcome = "retained_with_expert_geometry"

        row = dict(candidate)
        row.update(
            {
                "learning_version": LEARNING_VERSION,
                "label_event_id": str(label_event_id),
                "label_saved_at_ms": int(label.get("saved_at_ms") or 0),
                # All model inputs in this frame are frozen at proposal time.
                "feature_cutoff_time_ms": int(candidate["proposal_time_ms"]),
                # The reviewer saw this much context; this marks the target's
                # observation horizon and prevents treating it as a trade signal.
                "label_observation_end_ms": int(candidate["review_end_ms"]),
                "expert_has_sleep_pump": bool(pumps),
                "expert_setup_count": len(setups),
                "expert_pump_count": len(pumps),
                "expert_manual_setup_count": len(manual_setups),
                "expert_structural_setup_count": len(structural_setups),
                "expert_families_json": json.dumps(
                    [str(setup.get("family") or "unknown") for setup in setups],
                    separators=(",", ":"),
                ),
                "expert_qualities_json": json.dumps(
                    [str(setup.get("quality") or "bad") for setup in setups],
                    separators=(",", ":"),
                ),
                "annotation_outcome": annotation_outcome,
                "final_pump_start_ms": None if final_pump is None else int(final_pump["pump_start_ms"]),
                "final_culmination_ms": None if final_pump is None else int(final_pump["culmination_ms"]),
            }
        )
        rows.append(row)

    return pd.DataFrame(rows).sort_values(["proposal_time_ms", "symbol", "event_id"]).reset_index(drop=True)


def _summary(frame: pd.DataFrame, candidate_count: int) -> SleepPumpLearningSummary:
    reviewed = len(frame)
    positive = int(frame["expert_has_sleep_pump"].sum())
    return SleepPumpLearningSummary(
        candidate_count=candidate_count,
        reviewed_count=reviewed,
        unreviewed_count=candidate_count - reviewed,
        positive_count=positive,
        negative_count=reviewed - positive,
        positive_rate=positive / reviewed if reviewed else 0.0,
        manual_setup_count=int((frame["expert_manual_setup_count"] > 0).sum()),
        structural_setup_count=int((frame["expert_structural_setup_count"] > 0).sum()),
    )


def _audit_markdown(summary: SleepPumpLearningSummary) -> str:
    return "\n".join(
        [
            "# Sleep→pump expert-learning audit",
            "",
            f"- Learning version: `{LEARNING_VERSION}`",
            f"- Candidate queue: {summary.candidate_count}",
            f"- Expert-reviewed: {summary.reviewed_count}",
            f"- Unreviewed: {summary.unreviewed_count}",
            f"- Expert retained a sleep→pump: {summary.positive_count} ({summary.positive_rate:.1%})",
            f"- Expert rejected the candidate: {summary.negative_count}",
            f"- Cards with manual setup work: {summary.manual_setup_count}",
            f"- Cards with a level or structure-break: {summary.structural_setup_count}",
            "",
            "## Target contract",
            "",
            "`expert_has_sleep_pump = true` iff any final setup on the reviewed card retains a pump transition with both endpoints. The target is deliberately independent of family, level, structure break, entry, stop, EV, and forward return.",
            "",
            "## Time contract",
            "",
            "Features are the immutable discovery features with `feature_cutoff_time_ms = proposal_time_ms`. The reviewer had chart context through `label_observation_end_ms`; therefore this artifact is a retrospective detector-learning dataset, not a deployment-ready online signal or a trading model.",
            "",
            "## Scope limit",
            "",
            "The 500-card queue was selected by the current high-recall discovery policy. Its retained/rejected rate is valid for that review population only. Do not turn it into a market-base-rate probability or tune a production threshold without a separately sampled, untouched holdout.",
            "",
        ]
    )


def materialize_sleep_pump_learning_dataset(
    *,
    candidates_path: Path = REVIEW_DIR / "candidates.parquet",
    labels_path: Path = REVIEW_DIR / "review_labels.jsonl",
    output_dir: Path = REVIEW_DIR / "learning_v1",
) -> SleepPumpLearningSummary:
    """Write the labelled dataset, manifest, and scientific audit."""

    candidates = pd.read_parquet(candidates_path)
    labels = LabelStore(labels_path).read_effective()
    frame = build_learning_frame(candidates, labels)
    summary = _summary(frame, candidate_count=len(candidates))
    output_dir.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output_dir / "labeled_events.parquet", index=False)
    manifest = {
        "learning_version": LEARNING_VERSION,
        "summary": asdict(summary),
        "source": {
            "candidates_path": str(candidates_path),
            "candidates_sha256": _sha256(candidates_path),
            "labels_path": str(labels_path),
            "labels_sha256": _sha256(labels_path),
        },
        "target_contract": "any final setup retains a valid pump transition",
        "feature_cutoff_contract": "all candidate features are frozen at proposal_time_ms",
        "deployment_status": "not a deployment or probability-calibration artifact",
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    (output_dir / "audit.md").write_text(_audit_markdown(summary), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialise sleep→pump expert annotations for research.")
    parser.add_argument("--candidates", type=Path, default=REVIEW_DIR / "candidates.parquet")
    parser.add_argument("--labels", type=Path, default=REVIEW_DIR / "review_labels.jsonl")
    parser.add_argument("--output-dir", type=Path, default=REVIEW_DIR / "learning_v1")
    args = parser.parse_args()
    summary = materialize_sleep_pump_learning_dataset(
        candidates_path=args.candidates,
        labels_path=args.labels,
        output_dir=args.output_dir,
    )
    print(f"sleep-pump learning dataset: {summary.reviewed_count} reviewed / {summary.candidate_count} candidates")


if __name__ == "__main__":
    main()
