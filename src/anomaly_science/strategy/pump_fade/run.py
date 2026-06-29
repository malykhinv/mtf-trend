from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts.manifest import build_manifest, write_manifest
from anomaly_science.strategy.pump_fade.builder import (
    PumpFadeBuildError,
    build_pump_fade_decisions_with_quality,
    resolve_pump_fade_cache_universe,
)
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig
from anomaly_science.strategy.pump_fade.spec import PumpFadeStrategyDefinition


@dataclass(frozen=True, slots=True)
class PumpFadeDataQualityPolicy:
    max_rejected_symbol_fraction: float = 0.02
    max_dropped_market_row_fraction: float = 0.01

    def __post_init__(self) -> None:
        for name in ("max_rejected_symbol_fraction", "max_dropped_market_row_fraction"):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between zero and one")


def run_pump_fade_dataset_build(
    *,
    cache_dir: Path,
    output_path: Path,
    config: PumpFadeDecisionConfig | None = None,
    strategy: PumpFadeStrategyDefinition | None = None,
    quality_policy: PumpFadeDataQualityPolicy | None = None,
    limit_symbols: int | None = None,
    progress_every: int = 10,
    workers: int = 1,
) -> Path:
    if strategy is not None and config is not None and strategy.detector_config != config:
        raise ValueError("config and strategy.detector_config disagree")
    strategy = strategy or PumpFadeStrategyDefinition(detector_config=config or PumpFadeDecisionConfig())
    config = strategy.detector_config
    quality_policy = quality_policy or PumpFadeDataQualityPolicy()
    cache_universe = resolve_pump_fade_cache_universe(cache_dir)
    def report(done: int, total: int, symbol: str, rows: int) -> None:
        del symbol
        if progress_every > 0 and (done % progress_every == 0 or done == total):
            print(
                f"pump-fade dataset: {done}/{total} symbols; rows={rows}",
                flush=True,
            )

    frame, quality = build_pump_fade_decisions_with_quality(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        workers=workers,
        progress_callback=report,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    quality_path = output_path.with_suffix(".data_quality.csv")
    quality_temporary = quality_path.with_suffix(quality_path.suffix + ".tmp")
    quality.to_csv(quality_temporary, index=False)
    os.replace(quality_temporary, quality_path)

    rejected_symbol_count = int((quality["status"] == "REJECTED").sum())
    rejected_symbol_fraction = rejected_symbol_count / len(quality) if len(quality) else 0.0
    source_row_count = int(quality["source_row_count"].sum())
    dropped_market_row_count = int(quality["dropped_row_count"].sum())
    dropped_market_row_fraction = (
        dropped_market_row_count / source_row_count if source_row_count else 0.0
    )
    failures: list[str] = []
    if rejected_symbol_fraction > quality_policy.max_rejected_symbol_fraction:
        failures.append(
            "rejected symbol fraction "
            f"{rejected_symbol_fraction:.6f} exceeds "
            f"{quality_policy.max_rejected_symbol_fraction:.6f}"
        )
    if dropped_market_row_fraction > quality_policy.max_dropped_market_row_fraction:
        failures.append(
            "dropped market row fraction "
            f"{dropped_market_row_fraction:.6f} exceeds "
            f"{quality_policy.max_dropped_market_row_fraction:.6f}"
        )
    if failures:
        output_path.unlink(missing_ok=True)
        output_path.with_suffix(".metadata.json").unlink(missing_ok=True)
        (output_path.parent / f"{output_path.stem}.manifest.json").unlink(missing_ok=True)
        raise PumpFadeBuildError(
            "; ".join(failures) + f"; inspect {quality_path.resolve()}"
        )

    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir.resolve()),
        "limit_symbols": limit_symbols,
        "workers": workers,
        "cache_perpetual_symbol_count": len(cache_universe.perpetual_paths),
        "selected_perpetual_symbol_count": min(
            len(cache_universe.perpetual_paths),
            limit_symbols if limit_symbols is not None else len(cache_universe.perpetual_paths),
        ),
        "excluded_delivery_symbols": list(cache_universe.excluded_delivery_symbols),
        "row_count": len(frame),
        "event_count": int(frame["event_id"].nunique()) if not frame.empty else 0,
        "resolved_row_count": int(frame["label_available"].sum()) if not frame.empty else 0,
        "oi_covered_row_count": int(frame["oi_available"].sum()) if not frame.empty else 0,
        "oi_covered_row_fraction": float(frame["oi_available"].mean()) if not frame.empty else 0.0,
        "quality_symbol_count": len(quality),
        "quality_rejected_symbol_count": rejected_symbol_count,
        "quality_rejected_symbol_fraction": rejected_symbol_fraction,
        "quality_dropped_market_row_count": dropped_market_row_count,
        "quality_dropped_market_row_fraction": dropped_market_row_fraction,
        "quality_policy": asdict(quality_policy),
        "config": asdict(config),
        "strategy": {
            "strategy_name": strategy.strategy_name,
            "strategy_version": strategy.strategy_version,
            "strategy_contract_version": strategy.strategy_contract_version,
            "feature_schema_version": strategy.feature_schema_version,
            "label_schema_version": strategy.label_schema_version,
            "outcome_protocol": strategy.outcome_protocol,
            "required_data_streams": dict(strategy.required_data_streams),
            "custom_feature_names": [spec.name for spec in strategy.custom_feature_catalog],
            "execution_policy_version": strategy.execution_policies.policy_version,
            "stop_policy_ids": [policy.policy_id for policy in strategy.execution_policies.stop_policies],
            "target_policies": [
                {
                    "policy_id": policy.policy_id,
                    "close_fraction_grid": list(policy.close_fraction_grid),
                }
                for policy in strategy.execution_policies.take_profit_policies
            ],
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest = build_manifest(
        run_id=f"pump-fade-dataset-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
        artifact_paths=[output_path, quality_path, metadata_path],
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path
