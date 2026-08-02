"""Build an outcome-blind desk queue for recurrent pump-wave validation."""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow.parquet as pq

from anomaly_science.annotation.desk.labels import LabelStore
from anomaly_science.annotation.schemas import (
    ANNOTATION_CANDIDATE_SCHEMA_VERSION,
    LEVEL_LABEL_SCHEMA_VERSION,
)
from anomaly_science.artifacts.manifest import (
    build_manifest,
    sha256_file,
    write_manifest,
)


PUMP_WAVE_CANDIDATE_SCHEMA_VERSION = "recurrent_pump_wave_candidate_v1"
PUMP_WAVE_PROTOCOL_FREEZE_ID = "recurrent_pump_wave_stage0_20250802_v1"
SOURCE_ONLINE_SCHEMA_VERSION = "pump_fade_online_state_v4"
MINUTE_MS = 60_000
IS_END_EXCLUSIVE_MS = int(pd.Timestamp("2026-01-01T00:00:00Z").timestamp() * 1_000)

SOURCE_COLUMNS: tuple[str, ...] = (
    "online_state_schema_version",
    "event_id",
    "recurrence_chain_id",
    "symbol",
    "ignition_time_ms",
    "snapshot_time_ms",
    "feature_cutoff_time_ms",
    "is_nature_anchor",
    "base_level",
    "anchor_high",
    "current_close",
    "pump_size",
    "turnover",
    "atr_mult",
    "act_now",
    "act_now_over_peak",
    "session",
    "n_prior_24h",
    "n_prior_48h",
    "min_since_last_prior",
)

FORBIDDEN_FUTURE_COLUMNS: frozenset[str] = frozenset(
    {
        "y",
        "label",
        "label_available",
        "label_schema_version",
        "resolution_time_ms",
        "nature_resolution_time_ms",
        "event_peak",
        "event_peak_time_ms",
        "event_end_time_ms",
        "future_start_time_ms",
        "future_return",
        "outcome",
    }
)


@dataclass(frozen=True, slots=True)
class PumpWaveReviewConfig:
    is_end_exclusive_ms: int = IS_END_EXCLUSIVE_MS
    wave_ordinals: tuple[int, ...] = (2, 3)
    per_month_ordinal: int = 30
    review_lead_minutes: int = 4 * 60
    review_tail_minutes: int = 24 * 60
    chart_timeframe: str = "5m"
    sampling_salt: str = PUMP_WAVE_PROTOCOL_FREEZE_ID

    def __post_init__(self) -> None:
        if self.is_end_exclusive_ms <= 0:
            raise ValueError("is_end_exclusive_ms must be positive")
        if not self.wave_ordinals or any(value < 2 for value in self.wave_ordinals):
            raise ValueError("wave_ordinals must contain ordinals of at least two")
        if len(set(self.wave_ordinals)) != len(self.wave_ordinals):
            raise ValueError("wave_ordinals must be unique")
        if self.per_month_ordinal <= 0:
            raise ValueError("per_month_ordinal must be positive")
        if self.review_lead_minutes <= 0 or self.review_tail_minutes <= 0:
            raise ValueError("review context windows must be positive")
        if not self.chart_timeframe:
            raise ValueError("chart_timeframe is required")
        if not self.sampling_salt:
            raise ValueError("sampling_salt is required")


def load_online_source(path: Path) -> pd.DataFrame:
    """Read only the preregistered causal column allowlist."""

    available = set(pq.ParquetFile(path).schema_arrow.names)
    missing = sorted(set(SOURCE_COLUMNS) - available)
    if missing:
        raise ValueError(f"online-state source is missing columns: {missing}")
    forbidden = sorted(FORBIDDEN_FUTURE_COLUMNS & available)
    if forbidden:
        raise ValueError(f"online-state source contains forbidden future columns: {forbidden}")
    return pd.read_parquet(path, columns=list(SOURCE_COLUMNS))


def build_wave_population(
    online_states: pd.DataFrame,
    *,
    config: PumpWaveReviewConfig | None = None,
) -> pd.DataFrame:
    """Return all causal ordinal-two/three events before the IS boundary."""

    cfg = config or PumpWaveReviewConfig()
    missing = sorted(set(SOURCE_COLUMNS) - set(online_states.columns))
    if missing:
        raise ValueError(f"online-state frame is missing columns: {missing}")
    forbidden = sorted(FORBIDDEN_FUTURE_COLUMNS & set(online_states.columns))
    if forbidden:
        raise ValueError(f"online-state frame contains forbidden future columns: {forbidden}")

    work = online_states.loc[:, SOURCE_COLUMNS].copy()
    schemas = set(work["online_state_schema_version"].dropna().astype(str).unique())
    if schemas != {SOURCE_ONLINE_SCHEMA_VERSION}:
        raise ValueError(f"online-state schema mismatch: {sorted(schemas)}")
    for column in ("ignition_time_ms", "snapshot_time_ms", "feature_cutoff_time_ms"):
        work[column] = pd.to_numeric(work[column], errors="raise").astype("int64")
    if (work["feature_cutoff_time_ms"] > work["snapshot_time_ms"]).any():
        raise ValueError("source feature cutoff exceeds snapshot time")
    if (work["ignition_time_ms"] > work["snapshot_time_ms"]).any():
        raise ValueError("source ignition occurs after snapshot time")

    anchors = work.loc[
        work["is_nature_anchor"].astype(bool)
        & work["snapshot_time_ms"].lt(cfg.is_end_exclusive_ms)
        & work["feature_cutoff_time_ms"].lt(cfg.is_end_exclusive_ms)
    ].copy()
    if anchors.empty:
        raise ValueError("no causal nature anchors exist inside the IS boundary")
    if anchors["event_id"].duplicated().any():
        raise ValueError("nature-anchor event_id must be unique")
    anchors = anchors.sort_values(
        ["recurrence_chain_id", "ignition_time_ms", "event_id"], kind="mergesort"
    ).reset_index(drop=True)
    anchors["wave_ordinal"] = anchors.groupby("recurrence_chain_id", sort=False).cumcount() + 1

    rows: list[dict[str, Any]] = []
    for _, chain in anchors.groupby("recurrence_chain_id", sort=False):
        records = chain.to_dict(orient="records")
        for index, current in enumerate(records):
            ordinal = index + 1
            if ordinal not in cfg.wave_ordinals:
                continue
            first = records[0]
            previous = records[index - 1]
            second = records[1]
            snapshot_ms = int(current["snapshot_time_ms"])
            month = pd.Timestamp(snapshot_ms, unit="ms", tz="UTC").strftime("%Y-%m")
            source_event_id = str(current["event_id"])
            rows.append(
                {
                    "candidate_schema_version": ANNOTATION_CANDIDATE_SCHEMA_VERSION,
                    "pump_wave_schema_version": PUMP_WAVE_CANDIDATE_SCHEMA_VERSION,
                    "protocol_freeze_id": PUMP_WAVE_PROTOCOL_FREEZE_ID,
                    "event_id": _candidate_id(source_event_id, ordinal),
                    "source_event_id": source_event_id,
                    "recurrence_chain_id": str(current["recurrence_chain_id"]),
                    "symbol": str(current["symbol"]),
                    "tf": cfg.chart_timeframe,
                    "review_start_ms": max(
                        0,
                        int(first["ignition_time_ms"]) - cfg.review_lead_minutes * MINUTE_MS,
                    ),
                    "review_end_ms": min(
                        cfg.is_end_exclusive_ms - 1,
                        snapshot_ms + cfg.review_tail_minutes * MINUTE_MS,
                    ),
                    "anchor_time_ms": snapshot_ms,
                    "selection_snapshot_time_ms": snapshot_ms,
                    "feature_cutoff_time_ms": int(current["feature_cutoff_time_ms"]),
                    "wave_ordinal": ordinal,
                    "sampling_month": month,
                    "sampling_stratum": f"{month}|wave_{ordinal}",
                    "sampling_hash": _sampling_hash(source_event_id, cfg.sampling_salt),
                    "wave1_event_id": str(first["event_id"]),
                    "wave1_ignition_ms": int(first["ignition_time_ms"]),
                    "wave1_snapshot_ms": int(first["snapshot_time_ms"]),
                    "wave1_base_price": float(first["base_level"]),
                    "wave1_anchor_price": float(first["anchor_high"]),
                    "wave2_event_id": str(second["event_id"]),
                    "wave2_ignition_ms": int(second["ignition_time_ms"]),
                    "wave2_snapshot_ms": int(second["snapshot_time_ms"]),
                    "wave2_base_price": float(second["base_level"]),
                    "wave2_anchor_price": float(second["anchor_high"]),
                    "current_wave_ignition_ms": int(current["ignition_time_ms"]),
                    "current_wave_snapshot_ms": snapshot_ms,
                    "current_wave_base_price": float(current["base_level"]),
                    "current_wave_anchor_price": float(current["anchor_high"]),
                    "previous_wave_ignition_ms": int(previous["ignition_time_ms"]),
                    "previous_wave_snapshot_ms": int(previous["snapshot_time_ms"]),
                    "previous_wave_anchor_price": float(previous["anchor_high"]),
                    "minutes_since_previous_wave": (
                        int(current["ignition_time_ms"]) - int(previous["ignition_time_ms"])
                    )
                    / MINUTE_MS,
                    "pump_pct": float(current["anchor_high"] / first["base_level"] - 1.0),
                    "current_event_pump_size": float(current["pump_size"]),
                    "current_vs_wave1_anchor": float(
                        current["anchor_high"] / first["anchor_high"] - 1.0
                    ),
                    "current_vs_previous_anchor": float(
                        current["anchor_high"] / previous["anchor_high"] - 1.0
                    ),
                    "current_turnover": float(current["turnover"]),
                    "current_atr_multiple": float(current["atr_mult"]),
                    "current_activity": float(current["act_now"]),
                    "current_activity_vs_peak": float(current["act_now_over_peak"]),
                    "session": str(current["session"]),
                    "n_prior_24h_at_snapshot": int(current["n_prior_24h"]),
                    "n_prior_48h_at_snapshot": int(current["n_prior_48h"]),
                    "source_min_since_last_prior": float(current["min_since_last_prior"]),
                    "review_tail_is_presentation_only": True,
                    "untouched_2026_row_used": False,
                }
            )
    population = pd.DataFrame(rows)
    if population.empty:
        raise ValueError("no requested recurrent pump-wave candidates found")
    if population["event_id"].duplicated().any():
        raise ValueError("pump-wave candidate event_id must be unique")
    if (population["feature_cutoff_time_ms"] > population["selection_snapshot_time_ms"]).any():
        raise ValueError("candidate feature cutoff exceeds selection snapshot")
    if population["review_end_ms"].ge(cfg.is_end_exclusive_ms).any():
        raise ValueError("candidate review tail crosses the IS boundary")
    return population.sort_values(
        ["selection_snapshot_time_ms", "symbol", "wave_ordinal"], kind="mergesort"
    ).reset_index(drop=True)


def sample_wave_review_queue(
    population: pd.DataFrame,
    *,
    config: PumpWaveReviewConfig | None = None,
) -> pd.DataFrame:
    """Take the frozen outcome-blind hash sample for the human desk."""

    cfg = config or PumpWaveReviewConfig()
    required = {"sampling_stratum", "sampling_hash", "event_id"}
    missing = sorted(required - set(population.columns))
    if missing:
        raise ValueError(f"wave population is missing sampling columns: {missing}")
    queue = (
        population.sort_values(
            ["sampling_stratum", "sampling_hash", "event_id"], kind="mergesort"
        )
        .groupby("sampling_stratum", sort=True, group_keys=False)
        .head(cfg.per_month_ordinal)
        .sort_values(["selection_snapshot_time_ms", "symbol", "wave_ordinal"], kind="mergesort")
        .reset_index(drop=True)
    )
    queue["review_sample_selected"] = True
    return queue


def run_wave_review_build(
    *,
    input_path: Path,
    output_dir: Path,
    config: PumpWaveReviewConfig | None = None,
    refresh_existing_labels: bool = False,
) -> Path:
    cfg = config or PumpWaveReviewConfig()
    source = load_online_source(input_path)
    population = build_wave_population(source, config=cfg)
    queue = sample_wave_review_queue(population, config=cfg)

    labels_path = output_dir / "review_labels.jsonl"
    if labels_path.is_file() and labels_path.stat().st_size:
        if not refresh_existing_labels:
            raise FileExistsError(
                f"refusing to replace an annotated queue without refresh approval: {labels_path}"
            )
        existing = LabelStore(labels_path).read_effective()
        missing_ids = set(existing) - set(queue["event_id"].astype(str))
        if missing_ids:
            raise ValueError(
                "refresh would orphan expert labels: " + ", ".join(sorted(missing_ids)[:5])
            )

    output_dir.mkdir(parents=True, exist_ok=True)
    population_path = output_dir / "population.parquet"
    candidates_path = output_dir / "candidates.parquet"
    marks_path = output_dir / "marks.jsonl"
    report_path = output_dir / "report.json"
    _write_parquet_atomic(population, population_path)
    _write_parquet_atomic(queue, candidates_path)
    _write_jsonl_atomic([_seed_mark(row) for row in queue.to_dict(orient="records")], marks_path)
    labels_path.touch(exist_ok=True)

    stratum_counts = (
        population.groupby(["sampling_month", "wave_ordinal"], sort=True)
        .size()
        .rename("population_count")
        .reset_index()
        .merge(
            queue.groupby(["sampling_month", "wave_ordinal"], sort=True)
            .size()
            .rename("review_count")
            .reset_index(),
            on=["sampling_month", "wave_ordinal"],
            how="left",
        )
    )
    symbol_counts = population["symbol"].value_counts()
    report = {
        "report_version": "recurrent_pump_wave_stage0_report_v1",
        "protocol_freeze_id": PUMP_WAVE_PROTOCOL_FREEZE_ID,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "source_row_count": len(source),
        "source_online_schema_version": SOURCE_ONLINE_SCHEMA_VERSION,
        "population_count": len(population),
        "review_queue_count": len(queue),
        "population_symbol_count": int(population["symbol"].nunique()),
        "population_chain_count": int(population["recurrence_chain_id"].nunique()),
        "top_10_symbol_population_share": float(symbol_counts.head(10).sum() / len(population)),
        "stratum_counts": stratum_counts.to_dict(orient="records"),
        "config": asdict(cfg),
        "audits": {
            "future_columns_absent": True,
            "feature_cutoff_not_after_snapshot": bool(
                population["feature_cutoff_time_ms"]
                .le(population["selection_snapshot_time_ms"])
                .all()
            ),
            "review_tail_before_2026": bool(
                population["review_end_ms"].lt(cfg.is_end_exclusive_ms).all()
            ),
            "only_registered_wave_ordinals": bool(
                population["wave_ordinal"].isin(cfg.wave_ordinals).all()
            ),
            "outcome_blind_sampling": True,
            "untouched_2026_rows_used": False,
        },
        "claim_limit": "Manual morphology validation only; no short, outcome, EV, or PnL claim.",
    }
    _write_json_atomic(report, report_path)
    manifest = build_manifest(
        run_id="pump-wave-stage0-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=[population_path, candidates_path, marks_path, report_path],
        root=output_dir,
    )
    write_manifest(output_dir / "manifest.json", manifest)
    return output_dir


def _candidate_id(source_event_id: str, ordinal: int) -> str:
    raw = f"{PUMP_WAVE_CANDIDATE_SCHEMA_VERSION}|{source_event_id}|{ordinal}"
    digest = hashlib.blake2b(raw.encode("utf-8"), digest_size=10).hexdigest()
    return f"pumpwave_{digest}"


def _sampling_hash(source_event_id: str, salt: str) -> str:
    return hashlib.blake2b(
        f"{salt}|{source_event_id}".encode("utf-8"), digest_size=16
    ).hexdigest()


def _zigzag_points(row: dict[str, Any]) -> list[dict[str, float | int]]:
    points = [
        {"ms": int(row["wave1_ignition_ms"]), "price": float(row["wave1_base_price"])},
        {"ms": int(row["wave1_snapshot_ms"]), "price": float(row["wave1_anchor_price"])},
        {"ms": int(row["wave2_ignition_ms"]), "price": float(row["wave2_base_price"])},
        {"ms": int(row["wave2_snapshot_ms"]), "price": float(row["wave2_anchor_price"])},
    ]
    if int(row["wave_ordinal"]) == 3:
        points.extend(
            [
                {
                    "ms": int(row["current_wave_ignition_ms"]),
                    "price": float(row["current_wave_base_price"]),
                },
                {
                    "ms": int(row["current_wave_snapshot_ms"]),
                    "price": float(row["current_wave_anchor_price"]),
                },
            ]
        )
    unique: dict[int, dict[str, float | int]] = {}
    for point in points:
        unique[int(point["ms"])] = point
    return [unique[key] for key in sorted(unique)]


def _seed_mark(row: dict[str, Any]) -> dict[str, Any]:
    ordinal = int(row["wave_ordinal"])
    setup = {
        "family": "unknown",
        "quality": "bad",
        "notes": (
            f"BOT wave-{ordinal} candidate | Save + quality good if this is a genuine "
            "later pump wave of the same episode; No setup if recurrence grouping is wrong. "
            "Do not judge the future short result at this stage."
        ),
        "has_level": False,
        "has_pump_transition": False,
        "has_structure_break": False,
        "level_price": None,
        "level_start_ms": None,
        "level_end_ms": None,
        "level_broken": None,
        "level_touch_times_ms": None,
        "pump_start_ms": None,
        "pump_start_price": None,
        "culmination_ms": None,
        "culmination_price": None,
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
        "zigzag_points": _zigzag_points(row),
        "zones": [],
    }
    return {
        "event_id": str(row["event_id"]),
        "symbol": str(row["symbol"]),
        "tf": str(row["tf"]),
        "source_event_id": str(row["event_id"]),
        "source_event_ids": [str(row["event_id"])],
        "selected_tf": str(row["tf"]),
        "has_level": False,
        "has_pump_transition": False,
        "setups": [setup],
        "source": "bot_recurrent_pump_wave_stage0",
        "label_schema_version": LEVEL_LABEL_SCHEMA_VERSION,
        "saved_at_ms": 0,
    }


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _write_jsonl_atomic(rows: list[dict[str, Any]], path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in rows),
        encoding="utf-8",
    )
    os.replace(temporary, path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the Stage 0 recurrent pump-wave desk queue.")
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(".output/results/pump_fade_lifecycle_v3/online_states.parquet"),
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(".output/results/pump_wave_short_v1/wave_review"),
    )
    parser.add_argument("--per-month-ordinal", type=int, default=30)
    parser.add_argument("--refresh-existing-labels", action="store_true")
    args = parser.parse_args()
    output = run_wave_review_build(
        input_path=args.input,
        output_dir=args.out_dir,
        config=PumpWaveReviewConfig(per_month_ordinal=args.per_month_ordinal),
        refresh_existing_labels=args.refresh_existing_labels,
    )
    print(output)


__all__ = [
    "FORBIDDEN_FUTURE_COLUMNS",
    "PUMP_WAVE_CANDIDATE_SCHEMA_VERSION",
    "PUMP_WAVE_PROTOCOL_FREEZE_ID",
    "PumpWaveReviewConfig",
    "SOURCE_COLUMNS",
    "build_wave_population",
    "load_online_source",
    "run_wave_review_build",
    "sample_wave_review_queue",
]


if __name__ == "__main__":
    main()
