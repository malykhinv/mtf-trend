from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.probability import (
    BinaryWeeklyWalkForwardConfig,
    run_binary_weekly_walk_forward,
)
from anomaly_science.strategy.pump_fade.state_lattice import (
    PUMP_FADE_STATE_LATTICE_ORDINALS,
)


def run_pump_fade_state_probability_family(
    *,
    input_path: Path,
    out_dir: Path,
    base_config: BinaryWeeklyWalkForwardConfig,
    allow_dirty_development: bool = False,
) -> Path:
    """Run independently frozen weekly models for registered state ordinals."""
    if out_dir.exists() and any(out_dir.iterdir()):
        raise ValueError(f"state probability output must be absent or empty: {out_dir}")
    if base_config.population_exact_column != "state_ordinal":
        raise ValueError("state probability base config must filter population_exact_column=state_ordinal")
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_rows: list[dict[str, object]] = []
    written: list[Path] = []
    for ordinal in PUMP_FADE_STATE_LATTICE_ORDINALS:
        variant = replace(
            base_config,
            protocol_freeze_id=f"{base_config.protocol_freeze_id}:ordinal-{ordinal}",
            strategy_name=f"{base_config.strategy_name}_{ordinal}",
            population_exact_value=ordinal,
        )
        variant_dir = out_dir / f"ordinal_{ordinal}"
        run_binary_weekly_walk_forward(
            input_path=input_path,
            out_dir=variant_dir,
            config=variant,
            allow_dirty_development=allow_dirty_development,
        )
        gates = pd.read_csv(variant_dir / "gate_evaluation.csv")
        metadata = json.loads(
            (variant_dir / "binary_probability.metadata.json").read_text(encoding="utf-8")
        )
        metrics = pd.read_csv(variant_dir / "prediction_metrics.csv")
        overall = metrics.loc[
            (metrics["slice"] == "overall") & (metrics["slice_value"] == "all")
        ]
        metric_values = dict(zip(overall["metric"], overall["value"]))
        summary_rows.append(
            {
                "state_ordinal": ordinal,
                "oos_prediction_row_count": metadata["oos_prediction_row_count"],
                "frozen_week_count": metadata["frozen_week_count"],
                "skipped_week_count": metadata["skipped_week_count"],
                "auc": metric_values.get("auc"),
                "log_loss_improvement": metric_values.get("log_loss_improvement"),
                "brier_improvement": metric_values.get("brier_improvement"),
                "ece": metric_values.get("ece"),
                "passed_gate_count": int((gates["status"] == "PASS").sum()),
                "total_gate_count": len(gates),
                "all_gates_pass": bool((gates["status"] == "PASS").all()),
                "evidence_status": metadata["evidence_status"],
            }
        )
        written.extend(path for path in variant_dir.rglob("*") if path.is_file())
    summary_path = out_dir / "state_probability_family_summary.csv"
    _write_csv(summary_path, pd.DataFrame(summary_rows))
    written.append(summary_path)
    protocol_path = out_dir / "state_probability_family_protocol.json"
    _write_json(
        protocol_path,
        {
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "base_protocol_freeze_id": base_config.protocol_freeze_id,
            "input_path": str(input_path.resolve()),
            "input_sha256": sha256_file(input_path),
            "registered_state_ordinals": list(PUMP_FADE_STATE_LATTICE_ORDINALS),
            "models_are_separate_by_state_ordinal": True,
            "familywise_null_alpha": 0.05,
            "per_variant_bonferroni_alpha": base_config.gates.max_auc_permutation_p_value,
            "selection_after_results_forbidden": True,
            "scientific_scope": "development timing experiment; not EV or trading evidence",
        },
    )
    written.append(protocol_path)
    manifest = build_manifest(
        run_id="pump-fade-state-probability-family-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=written,
        root=out_dir,
    )
    write_manifest(out_dir / "state_probability_family.manifest.json", manifest)
    return out_dir


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


__all__ = ["run_pump_fade_state_probability_family"]
