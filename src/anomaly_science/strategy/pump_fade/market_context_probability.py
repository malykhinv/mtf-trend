from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.market_context import (
    event_positioning_feature_names,
    perp_crowding_feature_names,
    reference_market_feature_names,
    reference_positioning_feature_names,
)
from anomaly_science.probability import (
    PairedProbabilityComparisonConfig,
    compare_paired_oos_probabilities,
    load_binary_weekly_walk_forward_config,
    run_binary_weekly_walk_forward,
)
from anomaly_science.probability.config import QUANTILE_BINNED_BETA_ISOTONIC_V2
from anomaly_science.strategy.pump_fade.market_context import (
    PUMP_FADE_REFERENCE_MARKET_CONTEXT,
    PUMP_FADE_REFERENCE_POSITIONING_CONTEXT,
    PUMP_FADE_SYMBOL_POSITIONING_CONTEXT,
    PUMP_FADE_PERP_CROWDING_CONTEXT,
)
from anomaly_science.strategy.pump_fade.cvd import PUMP_FADE_CVD_MODEL_FEATURES
from anomaly_science.strategy.pump_fade.aggtrades_dynamics import (
    PUMP_FADE_AGGTRADES_MODEL_FEATURES,
)


@dataclass(frozen=True, slots=True)
class PumpFadeMarketContextProbabilityConfig:
    protocol_freeze_id: str
    t0_base_config_path: str
    state_base_config_path: str
    state_ordinals: tuple[int, ...]
    context_family: str
    context_features: tuple[str, ...]
    required_true_columns: tuple[str, ...]
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
            raise ValueError("registered context state ordinals must be exactly (1, 2)")
        families = {
            "reference_market_state_v1": (
                reference_market_feature_names(PUMP_FADE_REFERENCE_MARKET_CONTEXT),
                tuple(
                    f"has_{reference.alias}_context"
                    for reference in PUMP_FADE_REFERENCE_MARKET_CONTEXT.references
                ),
            ),
            "reference_positioning_metrics_v1": (
                reference_positioning_feature_names(
                    PUMP_FADE_REFERENCE_POSITIONING_CONTEXT
                ),
                tuple(
                    f"has_{reference.alias}_positioning_context"
                    for reference in PUMP_FADE_REFERENCE_POSITIONING_CONTEXT.references
                ),
            ),
            "event_scoped_symbol_positioning_v1": (
                event_positioning_feature_names(PUMP_FADE_SYMBOL_POSITIONING_CONTEXT),
                ("symbol_positioning_complete",),
            ),
            "pump_fade_cvd_path_v1": (
                PUMP_FADE_CVD_MODEL_FEATURES,
                ("cvd_available",),
            ),
            "pump_fade_perp_crowding_v1": (
                perp_crowding_feature_names(PUMP_FADE_PERP_CROWDING_CONTEXT),
                ("perp_crowding_complete",),
            ),
            "pump_fade_aggtrades_dynamics_v1": (
                PUMP_FADE_AGGTRADES_MODEL_FEATURES,
                ("aggtrades_available",),
            ),
        }
        if self.context_family not in families:
            raise ValueError(f"unknown context_family: {self.context_family}")
        expected_features, expected_flags = families[self.context_family]
        if self.context_features != expected_features:
            raise ValueError("context_features must equal the registered Core feature family")
        if self.required_true_columns != expected_flags:
            raise ValueError("required_true_columns must match the context family")
        if not 0.0 < self.familywise_alpha < 0.5:
            raise ValueError("familywise_alpha must lie in (0, 0.5)")


def load_pump_fade_market_context_probability_config(
    path: Path,
) -> PumpFadeMarketContextProbabilityConfig:
    raw: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    for name in ("state_ordinals", "context_features", "required_true_columns"):
        if name in raw:
            raw[name] = tuple(raw[name])
    unknown = sorted(
        set(raw) - set(PumpFadeMarketContextProbabilityConfig.__dataclass_fields__)
    )
    if unknown:
        raise ValueError(f"unknown market-context probability fields: {unknown}")
    return PumpFadeMarketContextProbabilityConfig(**raw)


def run_pump_fade_market_context_probability_experiment(
    *,
    nature_path: Path,
    state_lattice_path: Path,
    out_dir: Path,
    config: PumpFadeMarketContextProbabilityConfig,
    repository_root: Path,
    allow_dirty_development: bool = False,
) -> Path:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"market-context probability output must be absent or empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)
    t0_base = load_binary_weekly_walk_forward_config(repository_root / config.t0_base_config_path)
    state_base = load_binary_weekly_walk_forward_config(repository_root / config.state_base_config_path)
    variants = [("t0", nature_path, t0_base, None)] + [
        (f"ordinal_{ordinal}", state_lattice_path, state_base, ordinal)
        for ordinal in config.state_ordinals
    ]
    summary_rows: list[dict[str, object]] = []
    written: list[Path] = []
    comparison_alpha = config.familywise_alpha / len(variants)
    context_flags = config.required_true_columns
    for variant_name, input_path, base_config, ordinal in variants:
        shared = replace(
            base_config,
            protocol_freeze_id=f"{config.protocol_freeze_id}:{variant_name}",
            calibration_method=QUANTILE_BINNED_BETA_ISOTONIC_V2,
            required_true_columns=context_flags,
            required_finite_columns=config.context_features,
            population_exact_value=(ordinal if ordinal is not None else base_config.population_exact_value),
        )
        baseline_config = replace(
            shared,
            protocol_freeze_id=f"{shared.protocol_freeze_id}:baseline",
            strategy_name=f"{shared.strategy_name}_context_complete_case_baseline",
        )
        augmented_config = replace(
            shared,
            protocol_freeze_id=f"{shared.protocol_freeze_id}:with_context",
            strategy_name=f"{shared.strategy_name}_with_{config.context_family}",
            numeric_features=(*shared.numeric_features, *config.context_features),
        )
        variant_dir = out_dir / variant_name
        baseline_dir = variant_dir / "baseline"
        augmented_dir = variant_dir / "with_context"
        run_binary_weekly_walk_forward(
            input_path=input_path, out_dir=baseline_dir, config=baseline_config,
            allow_dirty_development=allow_dirty_development,
        )
        run_binary_weekly_walk_forward(
            input_path=input_path, out_dir=augmented_dir, config=augmented_config,
            allow_dirty_development=allow_dirty_development,
        )
        baseline_predictions = pd.read_parquet(baseline_dir / "oos_predictions.parquet")
        augmented_predictions = pd.read_parquet(augmented_dir / "oos_predictions.parquet")
        comparison = compare_paired_oos_probabilities(
            baseline_predictions,
            augmented_predictions,
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
        for name, frame in (
            ("paired_metrics.csv", comparison.metrics),
            ("paired_inference.csv", comparison.inference),
            ("paired_gates.csv", comparison.gates),
        ):
            path = variant_dir / name
            _write_csv(path, frame)
            written.append(path)
        augmented_gates = pd.read_csv(augmented_dir / "gate_evaluation.csv")
        paired_all_pass = bool((comparison.gates["status"] == "PASS").all())
        augmented_all_pass = bool((augmented_gates["status"] == "PASS").all())
        deltas = dict(zip(comparison.inference["metric"], comparison.inference["observed_delta"]))
        summary_rows.append(
            {
                "variant": variant_name,
                "paired_oos_rows": len(baseline_predictions),
                "auc_delta": deltas["auc_delta"],
                "log_loss_improvement": deltas["log_loss_improvement"],
                "brier_improvement": deltas["brier_improvement"],
                "paired_all_gates_pass": paired_all_pass,
                "augmented_all_prediction_gates_pass": augmented_all_pass,
                "incremental_edge_claim_allowed": paired_all_pass and augmented_all_pass,
            }
        )
        written.extend(path for path in variant_dir.rglob("*") if path.is_file())
    summary_path = out_dir / "market_context_probability_summary.csv"
    _write_csv(summary_path, pd.DataFrame(summary_rows))
    written.append(summary_path)
    protocol_path = out_dir / "market_context_probability_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "protocol_freeze_id": config.protocol_freeze_id,
            "context_family": config.context_family,
            "nature_input": str(nature_path.resolve()),
            "nature_sha256": sha256_file(nature_path),
            "state_lattice_input": str(state_lattice_path.resolve()),
            "state_lattice_sha256": sha256_file(state_lattice_path),
            "identical_complete_case_population": True,
            "required_true_columns": list(context_flags),
            "required_finite_columns": list(config.context_features),
            "registered_variants": [name for name, *_ in variants],
            "familywise_alpha": config.familywise_alpha,
            "per_variant_alpha": comparison_alpha,
            "scientific_scope": "paired registered-context incremental probability; no EV/trading claim",
        },
    )
    written.append(protocol_path)
    manifest = build_manifest(
        run_id="pump-fade-market-context-probability-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=tuple(dict.fromkeys(written)), root=out_dir,
    )
    write_manifest(out_dir / "market_context_probability.manifest.json", manifest)
    return out_dir


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
    "PumpFadeMarketContextProbabilityConfig",
    "load_pump_fade_market_context_probability_config",
    "run_pump_fade_market_context_probability_experiment",
]
