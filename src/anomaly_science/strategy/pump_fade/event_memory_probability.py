from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.probability import (
    PairedProbabilityComparisonConfig,
    compare_paired_oos_probabilities,
    load_binary_weekly_walk_forward_config,
    run_binary_weekly_walk_forward,
)
from anomaly_science.probability.config import QUANTILE_BINNED_BETA_ISOTONIC_V2
from anomaly_science.strategy.pump_fade.event_memory import (
    PUMP_FADE_EVENT_MEMORY_FEATURES,
    PUMP_FADE_EVENT_MEMORY_FLAGS,
    PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION,
)


PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES = (
    *PUMP_FADE_EVENT_MEMORY_FEATURES,
    *PUMP_FADE_EVENT_MEMORY_FLAGS,
)


@dataclass(frozen=True, slots=True)
class PumpFadeEventMemoryProbabilityConfig:
    protocol_freeze_id: str
    t0_base_config_path: str
    state_base_config_path: str
    state_ordinals: tuple[int, ...]
    event_memory_schema_version: str
    bootstrap_iterations: int = 1_000
    sign_flip_iterations: int = 999
    familywise_alpha: float = 0.05
    min_auc_delta: float = 0.01
    min_log_loss_improvement: float = 0.002
    min_brier_improvement: float = 0.001
    random_seed: int = 20260630

    def __post_init__(self) -> None:
        if not self.protocol_freeze_id:
            raise ValueError("protocol_freeze_id is required")
        if self.state_ordinals != (1, 2):
            raise ValueError("registered event-memory state ordinals must be exactly (1, 2)")
        if self.event_memory_schema_version != PUMP_FADE_EVENT_MEMORY_SCHEMA_VERSION:
            raise ValueError("event-memory schema version does not match the strategy declaration")
        if not 0.0 < self.familywise_alpha < 0.5:
            raise ValueError("familywise_alpha must lie in (0, 0.5)")


def load_pump_fade_event_memory_probability_config(
    path: Path,
) -> PumpFadeEventMemoryProbabilityConfig:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if "state_ordinals" in raw:
        raw["state_ordinals"] = tuple(raw["state_ordinals"])
    unknown = sorted(
        set(raw) - set(PumpFadeEventMemoryProbabilityConfig.__dataclass_fields__)
    )
    if unknown:
        raise ValueError(f"unknown event-memory probability config fields: {unknown}")
    return PumpFadeEventMemoryProbabilityConfig(**raw)


def run_pump_fade_event_memory_probability_experiment(
    *,
    nature_path: Path,
    state_lattice_path: Path,
    out_dir: Path,
    config: PumpFadeEventMemoryProbabilityConfig,
    repository_root: Path,
    allow_dirty_development: bool = False,
) -> Path:
    """Run paired baseline vs causal resolved-event-memory weekly WFA."""

    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"event-memory probability output must be absent or empty: {out_dir}")
    _validate_memory_schema(nature_path, config.event_memory_schema_version)
    _validate_memory_schema(state_lattice_path, config.event_memory_schema_version)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0_base = load_binary_weekly_walk_forward_config(
        repository_root / config.t0_base_config_path
    )
    state_base = load_binary_weekly_walk_forward_config(
        repository_root / config.state_base_config_path
    )
    variants = [("t0", nature_path, t0_base, None)] + [
        (f"ordinal_{ordinal}", state_lattice_path, state_base, ordinal)
        for ordinal in config.state_ordinals
    ]
    comparison_alpha = config.familywise_alpha / len(variants)
    summary_rows: list[dict[str, object]] = []
    written: list[Path] = []
    for variant_name, input_path, base_config, ordinal in variants:
        shared = replace(
            base_config,
            protocol_freeze_id=f"{config.protocol_freeze_id}:{variant_name}",
            calibration_method=QUANTILE_BINNED_BETA_ISOTONIC_V2,
            population_exact_value=(
                ordinal if ordinal is not None else base_config.population_exact_value
            ),
        )
        if shared.split_group_column != "recurrence_chain_id":
            raise ValueError(
                "event-memory experiments require recurrence_chain_id split isolation"
            )
        baseline = replace(
            shared,
            protocol_freeze_id=f"{shared.protocol_freeze_id}:baseline",
            strategy_name=f"{shared.strategy_name}_event_memory_baseline",
        )
        augmented = replace(
            shared,
            protocol_freeze_id=f"{shared.protocol_freeze_id}:with_event_memory",
            strategy_name=f"{shared.strategy_name}_with_event_memory",
            numeric_features=(
                *shared.numeric_features,
                *PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES,
            ),
        )
        variant_dir = out_dir / variant_name
        baseline_dir = variant_dir / "baseline"
        augmented_dir = variant_dir / "with_event_memory"
        run_binary_weekly_walk_forward(
            input_path=input_path,
            out_dir=baseline_dir,
            config=baseline,
            allow_dirty_development=allow_dirty_development,
        )
        run_binary_weekly_walk_forward(
            input_path=input_path,
            out_dir=augmented_dir,
            config=augmented,
            allow_dirty_development=allow_dirty_development,
        )
        comparison = compare_paired_oos_probabilities(
            pd.read_parquet(baseline_dir / "oos_predictions.parquet"),
            pd.read_parquet(augmented_dir / "oos_predictions.parquet"),
            PairedProbabilityComparisonConfig(
                protocol_freeze_id=f"{config.protocol_freeze_id}:{variant_name}:paired",
                bootstrap_iterations=config.bootstrap_iterations,
                sign_flip_iterations=config.sign_flip_iterations,
                alpha=comparison_alpha,
                min_auc_delta=config.min_auc_delta,
                min_log_loss_improvement=config.min_log_loss_improvement,
                min_brier_improvement=config.min_brier_improvement,
                random_seed=config.random_seed,
            ),
        )
        metrics_path = variant_dir / "paired_metrics.csv"
        inference_path = variant_dir / "paired_inference.csv"
        gates_path = variant_dir / "paired_gates.csv"
        _write_csv(metrics_path, comparison.metrics)
        _write_csv(inference_path, comparison.inference)
        _write_csv(gates_path, comparison.gates)
        augmented_gates = pd.read_csv(augmented_dir / "gate_evaluation.csv")
        deltas = dict(
            zip(
                comparison.inference["metric"],
                comparison.inference["observed_delta"],
                strict=True,
            )
        )
        paired_pass = bool((comparison.gates["status"] == "PASS").all())
        prediction_pass = bool((augmented_gates["status"] == "PASS").all())
        summary_rows.append(
            {
                "variant": variant_name,
                "paired_oos_rows": int(
                    comparison.metrics.loc[0, "row_count"]
                ),
                "auc_delta": deltas["auc_delta"],
                "log_loss_improvement": deltas["log_loss_improvement"],
                "brier_improvement": deltas["brier_improvement"],
                "paired_all_gates_pass": paired_pass,
                "augmented_all_prediction_gates_pass": prediction_pass,
                "incremental_edge_claim_allowed": paired_pass and prediction_pass,
            }
        )
        written.extend(path for path in variant_dir.rglob("*") if path.is_file())
    summary_path = out_dir / "event_memory_probability_summary.csv"
    _write_csv(summary_path, pd.DataFrame(summary_rows))
    protocol_path = out_dir / "event_memory_probability_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_id": config.protocol_freeze_id,
            "event_memory_schema_version": config.event_memory_schema_version,
            "nature_input": str(nature_path.resolve()),
            "nature_sha256": sha256_file(nature_path),
            "state_lattice_input": str(state_lattice_path.resolve()),
            "state_lattice_sha256": sha256_file(state_lattice_path),
            "model_features": list(PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES),
            "split_group_column": "recurrence_chain_id",
            "registered_variants": [name for name, *_ in variants],
            "familywise_alpha": config.familywise_alpha,
            "per_variant_alpha": comparison_alpha,
            "scientific_scope": (
                "paired incremental resolved-event-memory probability evidence; "
                "development only; no EV or trading claim"
            ),
        },
    )
    written.extend((summary_path, protocol_path))
    manifest = build_manifest(
        run_id=(
            "pump-fade-event-memory-probability-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ),
        artifact_paths=tuple(dict.fromkeys(written)),
        root=out_dir,
    )
    write_manifest(out_dir / "event_memory_probability.manifest.json", manifest)
    return out_dir


def _validate_memory_schema(path: Path, expected: str) -> None:
    frame = pd.read_parquet(
        path,
        columns=["event_memory_schema_version", "recurrence_chain_id"],
    )
    observed = set(frame["event_memory_schema_version"].dropna().astype(str).unique())
    if observed != {expected}:
        raise ValueError(
            f"event-memory schema mismatch for {path}: observed={sorted(observed)}"
        )
    if frame["recurrence_chain_id"].isna().any():
        raise ValueError("recurrence_chain_id must be complete")


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


__all__ = [
    "PUMP_FADE_EVENT_MEMORY_MODEL_FEATURES",
    "PumpFadeEventMemoryProbabilityConfig",
    "load_pump_fade_event_memory_probability_config",
    "run_pump_fade_event_memory_probability_experiment",
]
