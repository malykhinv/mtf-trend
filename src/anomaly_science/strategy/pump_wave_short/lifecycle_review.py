"""Build the Stage-0 desk queue for sleep -> pump waves -> dump morphology.

The queue reuses the already validated sleep/pump candidate artifact.  Candidate
membership depends only on that causal first-wave proposal; the long right-hand
chart tail is display-only and is never read when selecting cards.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.annotation.desk.labels import LabelStore
from anomaly_science.annotation.schemas import LEVEL_LABEL_SCHEMA_VERSION


SOURCE_CANDIDATES = Path(".output/results/triple_tap_v1/sleep_pump_review/candidates.parquet")
REVIEW_DIR = Path(".output/results/pump_wave_short_v1/lifecycle_review")
IS_END_EXCLUSIVE_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)
HOUR_MS = 3_600_000
LIFECYCLE_CANDIDATE_SCHEMA_VERSION = "pump_lifecycle_candidate_v1"
LIFECYCLE_PROTOCOL_FREEZE_ID = "pump_lifecycle_stage0_20250802_v1"
SOURCE_POPULATION_SCOPE = "materialized_high_score_review_frame_max500_max3_per_symbol_tf"

SOURCE_COLUMNS: tuple[str, ...] = (
    "event_id",
    "symbol",
    "tf",
    "proposal_time_ms",
    "pump_start_ms",
    "culmination_ms",
    "pump_low_price",
    "culmination_price",
    "pump_pct",
    "sleep_range_pct",
    "sleep_directional_drift_pct",
    "sleep_base_price",
    "base_to_high_pct",
    "pump_path_efficiency",
    "pump_retrace_share",
    "pump_single_bar_range_share",
    "pump_upper_wick_share",
    "pump_over_sleep_vol",
    "pump_over_sleep_trades",
)

FORBIDDEN_SOURCE_COLUMNS: frozenset[str] = frozenset(
    {
        "outcome",
        "future_return",
        "dump_return",
        "dump_time_ms",
        "entry_ms",
        "exit_ms",
        "pnl",
        "win",
        "label",
    }
)


@dataclass(frozen=True, slots=True)
class LifecycleReviewConfig:
    is_end_exclusive_ms: int = IS_END_EXCLUSIVE_MS
    review_lead_hours: int = 6
    review_tail_hours: int = 72
    minimum_tail_hours: int = 24
    pilot_per_month: int = 10
    sampling_salt: str = LIFECYCLE_PROTOCOL_FREEZE_ID

    def __post_init__(self) -> None:
        if self.is_end_exclusive_ms <= 0:
            raise ValueError("is_end_exclusive_ms must be positive")
        if self.review_lead_hours <= 0 or self.review_tail_hours <= 0:
            raise ValueError("review windows must be positive")
        if not 0 < self.minimum_tail_hours <= self.review_tail_hours:
            raise ValueError("minimum_tail_hours must be within the review tail")
        if self.pilot_per_month <= 0:
            raise ValueError("pilot_per_month must be positive")
        if not self.sampling_salt:
            raise ValueError("sampling_salt is required")


def _stable_rank(event_id: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}|{event_id}".encode("utf-8")).hexdigest()


def _lifecycle_event_id(source_event_id: str) -> str:
    digest = hashlib.blake2b(f"pump_lifecycle_v1|{source_event_id}".encode("utf-8"), digest_size=10).hexdigest()
    return f"pumplifecycle_{digest}"


def build_lifecycle_population(
    source: pd.DataFrame,
    *,
    config: LifecycleReviewConfig = LifecycleReviewConfig(),
) -> pd.DataFrame:
    """Transform causal sleep/pump proposals without looking at their future tail."""

    forbidden = sorted(FORBIDDEN_SOURCE_COLUMNS & set(source.columns))
    if forbidden:
        raise ValueError(f"forbidden future/outcome columns in source: {forbidden}")
    missing = sorted(set(SOURCE_COLUMNS) - set(source.columns))
    if missing:
        raise ValueError(f"missing sleep/pump source columns: {missing}")

    frame = source.loc[:, SOURCE_COLUMNS].copy()
    numeric = [column for column in SOURCE_COLUMNS if column not in {"event_id", "symbol", "tf"}]
    for column in numeric:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if frame["event_id"].duplicated().any():
        raise ValueError("sleep/pump source event_id must be unique")
    if not (frame["pump_start_ms"] < frame["culmination_ms"]).all():
        raise ValueError("source pump must run forward in time")
    if not (frame["culmination_ms"] <= frame["proposal_time_ms"]).all():
        raise ValueError("proposal_time_ms must not precede the confirmed culmination")

    maximum_anchor_ms = config.is_end_exclusive_ms - config.minimum_tail_hours * HOUR_MS
    frame = frame.loc[frame["culmination_ms"] < maximum_anchor_ms].copy()
    frame["source_event_id"] = frame["event_id"].astype(str)
    frame["event_id"] = frame["source_event_id"].map(_lifecycle_event_id)
    frame["candidate_schema_version"] = LIFECYCLE_CANDIDATE_SCHEMA_VERSION
    frame["feature_cutoff_time_ms"] = frame["proposal_time_ms"].astype("int64")
    frame["selection_snapshot_time_ms"] = frame["proposal_time_ms"].astype("int64")
    frame["anchor_time_ms"] = frame["culmination_ms"].astype("int64")
    frame["review_start_ms"] = (
        frame["pump_start_ms"].astype("int64") - config.review_lead_hours * HOUR_MS
    ).clip(lower=0)
    frame["sleep_start_ms"] = frame["review_start_ms"].astype("int64")
    frame["sleep_end_ms"] = frame["pump_start_ms"].astype("int64")
    frame["review_end_ms"] = (
        frame["culmination_ms"].astype("int64") + config.review_tail_hours * HOUR_MS
    ).clip(upper=config.is_end_exclusive_ms - 1)
    frame["review_month"] = pd.to_datetime(frame["culmination_ms"], unit="ms", utc=True).dt.strftime("%Y-%m")
    frame["selection_hash"] = frame["source_event_id"].map(lambda value: _stable_rank(value, config.sampling_salt))
    frame["untouched_2026_row_used"] = False
    frame = frame.sort_values(["culmination_ms", "symbol", "event_id"]).reset_index(drop=True)

    if not (frame["feature_cutoff_time_ms"] <= frame["selection_snapshot_time_ms"]).all():
        raise AssertionError("feature cutoff exceeds selection snapshot")
    if not (frame["selection_snapshot_time_ms"] < frame["review_end_ms"]).all():
        raise AssertionError("review tail must start after the selection snapshot")
    if not frame["review_end_ms"].lt(config.is_end_exclusive_ms).all():
        raise AssertionError("review tail crossed into 2026")
    return frame


def sample_lifecycle_review_queue(
    population: pd.DataFrame,
    *,
    config: LifecycleReviewConfig = LifecycleReviewConfig(),
) -> pd.DataFrame:
    """Select a deterministic, month-balanced pilot without market outcomes."""

    if population.empty:
        return population.copy()
    selected = (
        population.sort_values(["review_month", "selection_hash", "event_id"])
        .groupby("review_month", sort=True, group_keys=False)
        .head(config.pilot_per_month)
        .copy()
    )
    selected["review_phase"] = "pilot"
    return selected.sort_values(["culmination_ms", "symbol", "event_id"]).reset_index(drop=True)


def _seed_mark(row: dict[str, Any]) -> dict[str, Any]:
    wave = {
        "wave_ordinal": 1,
        "start_ms": int(row["pump_start_ms"]),
        "start_price": float(row["pump_low_price"]),
        "culmination_ms": int(row["culmination_ms"]),
        "culmination_price": float(row["culmination_price"]),
    }
    setup = {
        "family": "unknown",
        "quality": "bad",
        "notes": "BOT: sleep -> wave 1 only. Add pump W2/W3 manually; sideways is automatic; mark dump/short structure later.",
        "has_level": False,
        "has_pump_transition": True,
        "has_structure_break": False,
        "level_price": None,
        "level_start_ms": None,
        "level_end_ms": None,
        "level_broken": None,
        "level_touch_times_ms": None,
        "pump_start_ms": wave["start_ms"],
        "pump_start_price": wave["start_price"],
        "culmination_ms": wave["culmination_ms"],
        "culmination_price": wave["culmination_price"],
        "pump_waves": [wave],
        "sideways_segments": [],
        "structure_break_ms": None,
        "structure_break_price": None,
        "structure_swing_low_ms": None,
        "structure_swing_low_price": None,
        "entry_ms": None,
        "entry_price": None,
        "entry_auto": None,
        "entry_source": None,
        "exit_ms": None,
        "exit_price": None,
        "sl_price": None,
        "sl_ms": None,
        "sl_hit_ms": None,
        "sl_auto": None,
        "sl_source": None,
        "zigzag_points": [],
        "zones": [],
    }
    return {
        "event_id": str(row["event_id"]),
        "symbol": str(row["symbol"]),
        "tf": str(row["tf"]),
        "source_event_id": str(row["source_event_id"]),
        "source_event_ids": [str(row["source_event_id"])],
        "selected_tf": str(row["tf"]),
        "has_level": False,
        "has_pump_transition": True,
        "setups": [setup],
        "source": "bot_pump_lifecycle_detector",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
        "saved_at_ms": 0,
    }


def build_lifecycle_review(
    *,
    source_path: Path = SOURCE_CANDIDATES,
    review_dir: Path = REVIEW_DIR,
    config: LifecycleReviewConfig = LifecycleReviewConfig(),
) -> pd.DataFrame:
    source = pd.read_parquet(source_path)
    population = build_lifecycle_population(source, config=config)
    pilot = sample_lifecycle_review_queue(population, config=config)
    if pilot.empty:
        raise ValueError("no lifecycle review candidates")

    labels_path = review_dir / "review_labels.jsonl"
    if labels_path.exists() and labels_path.stat().st_size:
        effective = LabelStore(labels_path).read_effective()
        missing = set(effective) - set(pilot["event_id"].astype(str))
        if missing:
            raise ValueError("refusing to orphan existing lifecycle labels")

    review_dir.mkdir(parents=True, exist_ok=True)
    population.to_parquet(review_dir / "population.parquet", index=False)
    pilot.to_parquet(review_dir / "candidates.parquet", index=False)
    marks = "".join(
        json.dumps(_seed_mark(row), ensure_ascii=False, separators=(",", ":")) + "\n"
        for row in pilot.to_dict("records")
    )
    (review_dir / "marks.jsonl").write_text(marks, encoding="utf-8")
    labels_path.touch(exist_ok=True)
    report = {
        "protocol_freeze_id": LIFECYCLE_PROTOCOL_FREEZE_ID,
        "source_path": str(source_path),
        "source_population_scope": SOURCE_POPULATION_SCOPE,
        "representative_prevalence_allowed": False,
        "population_rows": int(len(population)),
        "pilot_rows": int(len(pilot)),
        "pilot_by_month": {str(k): int(v) for k, v in pilot.groupby("review_month").size().items()},
        "unique_symbols_population": int(population["symbol"].nunique()),
        "untouched_2026_rows_used": 0,
    }
    (review_dir / "report.json").write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    return pilot


def main() -> None:
    parser = argparse.ArgumentParser(description="Build sleep -> pump waves -> dump visual review queue.")
    parser.add_argument("--source", type=Path, default=SOURCE_CANDIDATES)
    parser.add_argument("--review-dir", type=Path, default=REVIEW_DIR)
    parser.add_argument("--pilot-per-month", type=int, default=10)
    args = parser.parse_args()
    config = LifecycleReviewConfig(pilot_per_month=args.pilot_per_month)
    pilot = build_lifecycle_review(source_path=args.source, review_dir=args.review_dir, config=config)
    print(f"pump lifecycle pilot={len(pilot)} -> {args.review_dir}")


if __name__ == "__main__":
    main()
