from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, write_manifest
from anomaly_science.strategy.pump_fade.builder import (
    PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION,
    PumpFadeBuildError,
    build_pump_fade_datasets_with_quality,
    join_pump_fade_states_and_labels,
    resolve_pump_fade_cache_universe,
)
from anomaly_science.strategy.pump_fade.config import PumpFadeDecisionConfig
from anomaly_science.strategy.pump_fade.event_memory import (
    PUMP_FADE_EVENT_MEMORY_AVAILABILITY_FLAGS,
)
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


def _write_csv_atomic(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False)
    os.replace(temporary, path)


def _availability_flag(feature_name: str, columns: set[str]) -> str:
    direct = f"has_{feature_name}"
    if direct in columns:
        return direct
    if feature_name.startswith("oi_") or "_oi_" in feature_name:
        return "oi_available" if "oi_available" in columns else ""
    explicit = {
        "average_trade_notional_vs_24h": "has_average_trade_notional_baseline",
        "event_average_trade_notional": "has_average_trade_notional_baseline",
        "taker_buy_share_event": "has_taker_buy_quote_data",
        "taker_imbalance_event": "has_taker_buy_quote_data",
        **PUMP_FADE_EVENT_MEMORY_AVAILABILITY_FLAGS,
    }
    candidate = explicit.get(feature_name, "")
    return candidate if candidate in columns else ""


def _build_feature_missingness_report(
    online_states: pd.DataFrame,
    *,
    strategy: PumpFadeStrategyDefinition,
) -> pd.DataFrame:
    columns = set(online_states.columns)
    rows: list[dict[str, object]] = []
    for spec in strategy.custom_feature_catalog:
        emitted = spec.name in columns
        missing_count = (
            int(online_states[spec.name].isna().sum())
            if emitted
            else len(online_states)
        )
        row_count = len(online_states)
        missing_fraction = missing_count / row_count if row_count else 0.0
        required_streams = tuple(spec.required_streams)
        required = any(
            bool(strategy.required_data_streams.get(stream, False))
            for stream in required_streams
        )
        if not emitted:
            status = "NOT_EMITTED"
        elif missing_count == 0:
            status = "COMPLETE"
        elif required:
            status = "MISSING_REQUIRED"
        elif missing_count == row_count:
            status = "MISSING_OPTIONAL"
        else:
            status = "PARTIAL_OPTIONAL"
        rows.append(
            {
                "strategy_name": strategy.strategy_name,
                "feature_schema_version": strategy.feature_schema_version,
                "feature_name": spec.name,
                "feature_family": spec.family,
                "is_model_feature": spec.is_model_feature,
                "required_streams": ";".join(required_streams),
                "stream_requirement": "required" if required else "optional_or_internal",
                "availability_flag": _availability_flag(spec.name, columns),
                "row_count": row_count,
                "missing_count": missing_count,
                "missing_fraction": missing_fraction,
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def _build_rejection_summary(
    *,
    strategy_name: str,
    rejected_symbol_count: int,
    symbol_count: int,
    rejected_symbol_fraction: float,
    dropped_market_row_count: int,
    source_row_count: int,
    dropped_market_row_fraction: float,
    policy: PumpFadeDataQualityPolicy,
) -> pd.DataFrame:
    checks = (
        (
            "rejected_symbol_fraction",
            rejected_symbol_count,
            symbol_count,
            rejected_symbol_fraction,
            policy.max_rejected_symbol_fraction,
        ),
        (
            "dropped_market_row_fraction",
            dropped_market_row_count,
            source_row_count,
            dropped_market_row_fraction,
            policy.max_dropped_market_row_fraction,
        ),
    )
    rows = [
        {
            "strategy_name": strategy_name,
            "check_name": name,
            "rejected_count": rejected_count,
            "population_count": population_count,
            "observed_fraction": observed_fraction,
            "maximum_allowed_fraction": maximum_allowed,
            "status": "PASS" if observed_fraction <= maximum_allowed else "FAIL",
        }
        for name, rejected_count, population_count, observed_fraction, maximum_allowed in checks
    ]
    run_valid = all(row["status"] == "PASS" for row in rows)
    rows.append(
        {
            "strategy_name": strategy_name,
            "check_name": "run_valid",
            "rejected_count": sum(row["status"] == "FAIL" for row in rows),
            "population_count": len(checks),
            "observed_fraction": 0.0 if run_valid else 1.0,
            "maximum_allowed_fraction": 0.0,
            "status": "PASS" if run_valid else "FAIL",
        }
    )
    return pd.DataFrame(rows)


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
    max_inflight_symbols: int | None = None,
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

    online_states, labels, quality = build_pump_fade_datasets_with_quality(
        cache_dir=cache_dir,
        config=config,
        limit_symbols=limit_symbols,
        workers=workers,
        max_inflight_symbols=max_inflight_symbols,
        progress_callback=report,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    labels_path = output_path.with_name(
        f"{output_path.stem}.labels{output_path.suffix}"
    )
    supervised_path = output_path.with_name(
        f"{output_path.stem}.supervised{output_path.suffix}"
    )
    quality_path = output_path.with_suffix(".data_quality.csv")
    strategy_quality_path = output_path.parent / "strategy_data_quality.csv"
    missingness_path = output_path.parent / "feature_missingness_report.csv"
    rejection_summary_path = output_path.parent / "dataset_rejection_summary.csv"
    _write_csv_atomic(quality, quality_path)
    strategy_quality = quality.copy()
    strategy_quality.insert(0, "strategy_name", strategy.strategy_name)
    _write_csv_atomic(strategy_quality, strategy_quality_path)
    missingness = _build_feature_missingness_report(
        online_states,
        strategy=strategy,
    )
    _write_csv_atomic(missingness, missingness_path)

    rejected_symbol_count = int((quality["status"] == "REJECTED").sum())
    rejected_symbol_fraction = rejected_symbol_count / len(quality) if len(quality) else 0.0
    source_row_count = int(quality["source_row_count"].sum())
    dropped_market_row_count = int(quality["dropped_row_count"].sum())
    dropped_market_row_fraction = (
        dropped_market_row_count / source_row_count if source_row_count else 0.0
    )
    rejection_summary = _build_rejection_summary(
        strategy_name=strategy.strategy_name,
        rejected_symbol_count=rejected_symbol_count,
        symbol_count=len(quality),
        rejected_symbol_fraction=rejected_symbol_fraction,
        dropped_market_row_count=dropped_market_row_count,
        source_row_count=source_row_count,
        dropped_market_row_fraction=dropped_market_row_fraction,
        policy=quality_policy,
    )
    _write_csv_atomic(rejection_summary, rejection_summary_path)
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
        for stale_path in (output_path, labels_path, supervised_path):
            stale_path.unlink(missing_ok=True)
        output_path.with_suffix(".metadata.json").unlink(missing_ok=True)
        (output_path.parent / f"{output_path.stem}.manifest.json").unlink(missing_ok=True)
        raise PumpFadeBuildError(
            "; ".join(failures) + f"; inspect {quality_path.resolve()}"
        )

    supervised = join_pump_fade_states_and_labels(online_states, labels)
    for artifact_path, artifact_frame in (
        (output_path, online_states),
        (labels_path, labels),
        (supervised_path, supervised),
    ):
        temporary = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
        artifact_frame.to_parquet(temporary, index=False)
        os.replace(temporary, artifact_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "cache_dir": str(cache_dir.resolve()),
        "limit_symbols": limit_symbols,
        "workers": workers,
        "max_inflight_symbols": max_inflight_symbols if max_inflight_symbols is not None else workers * 2,
        "worker_scheduling_contract": "bounded_inflight_symbol_pool_v1",
        "cache_perpetual_symbol_count": len(cache_universe.perpetual_paths),
        "selected_perpetual_symbol_count": min(
            len(cache_universe.perpetual_paths),
            limit_symbols if limit_symbols is not None else len(cache_universe.perpetual_paths),
        ),
        "excluded_delivery_symbols": list(cache_universe.excluded_delivery_symbols),
        "online_state_path": str(output_path.resolve()),
        "offline_label_path": str(labels_path.resolve()),
        "supervised_research_path": str(supervised_path.resolve()),
        "online_state_schema_version": PUMP_FADE_ONLINE_STATE_SCHEMA_VERSION,
        "lifecycle_contract": "online_states_and_offline_labels_separate_v1",
        "row_count": len(online_states),
        "event_count": int(online_states["event_id"].nunique()) if not online_states.empty else 0,
        "resolved_row_count": int(labels["label_available"].sum()) if not labels.empty else 0,
        "oi_covered_row_count": int(online_states["oi_available"].sum()) if not online_states.empty else 0,
        "oi_covered_row_fraction": float(online_states["oi_available"].mean()) if not online_states.empty else 0.0,
        "quality_symbol_count": len(quality),
        "quality_rejected_symbol_count": rejected_symbol_count,
        "quality_rejected_symbol_fraction": rejected_symbol_fraction,
        "quality_dropped_market_row_count": dropped_market_row_count,
        "quality_dropped_market_row_fraction": dropped_market_row_fraction,
        "quality_policy": asdict(quality_policy),
        "data_quality_artifacts": {
            "strategy_data_quality": str(strategy_quality_path.resolve()),
            "feature_missingness_report": str(missingness_path.resolve()),
            "dataset_rejection_summary": str(rejection_summary_path.resolve()),
        },
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
        artifact_paths=[
            output_path,
            labels_path,
            supervised_path,
            quality_path,
            strategy_quality_path,
            missingness_path,
            rejection_summary_path,
            metadata_path,
        ],
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path
