from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import pandas as pd

from anomaly_science.artifacts.manifest import build_manifest, sha256_file, write_manifest
from anomaly_science.market_context.builder import (
    attach_reference_market_context,
    build_reference_market_features,
    reference_market_feature_names,
)
from anomaly_science.market_context.config import ReferenceMarketContextConfig
from anomaly_science.market_context.config import ReferencePositioningContextConfig
from anomaly_science.market_context.positioning import (
    attach_reference_positioning_context,
    build_reference_positioning_features,
    reference_positioning_feature_names,
)
from anomaly_science.market_context.config import EventScopedPositioningContextConfig
from anomaly_science.market_context.event_positioning import (
    attach_event_positioning_context,
    build_event_positioning_features,
    event_positioning_feature_names,
)
from anomaly_science.market_context.config import EventScopedPerpCrowdingContextConfig
from anomaly_science.market_context.perp_crowding import (
    attach_perp_crowding_context,
    build_perp_crowding_features,
    perp_crowding_feature_names,
)


def run_attach_reference_market_context(
    *, input_path: Path, cache_dir: Path, output_path: Path,
    config: ReferenceMarketContextConfig,
) -> Path:
    rows = pd.read_parquet(input_path)
    feature_frames = []
    inputs = [input_path]
    for reference in config.references:
        path = cache_dir / f"{reference.symbol}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"reference market cache not found: {path}")
        market = pd.read_parquet(path)
        feature_frames.append(
            build_reference_market_features(market, reference=reference, config=config)
        )
        inputs.append(path)
    augmented = attach_reference_market_context(rows, tuple(feature_frames), config=config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    augmented.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    feature_names = reference_market_feature_names(config)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "reference_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)} for path in inputs[1:]
        ],
        "input_row_count": len(rows),
        "output_row_count": len(augmented),
        "config": asdict(config),
        "feature_names": list(feature_names),
        "coverage": {
            reference.alias: float(augmented[f"has_{reference.alias}_context"].mean())
            for reference in config.references
        },
        "temporal_contract": "reference candle timestamp + 1m <= strategy snapshot; exact closed-minute join",
    }
    metadata_path.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    manifest = build_manifest(
        run_id="reference-market-context-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(output_path, metadata_path), root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path


def run_attach_reference_positioning_context(
    *, input_path: Path, metrics_dir: Path, output_path: Path,
    config: ReferencePositioningContextConfig,
) -> Path:
    rows = pd.read_parquet(input_path)
    feature_frames = []
    metric_paths = []
    for reference in config.references:
        path = metrics_dir / f"{reference.symbol}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"reference positioning cache not found: {path}")
        metrics = pd.read_parquet(path)
        feature_frames.append(
            build_reference_positioning_features(
                metrics, reference=reference, config=config
            )
        )
        metric_paths.append(path)
    augmented = attach_reference_positioning_context(
        rows, tuple(feature_frames), config=config
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    augmented.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    feature_names = reference_positioning_feature_names(config)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "metrics_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in metric_paths
        ],
        "input_row_count": len(rows),
        "output_row_count": len(augmented),
        "config": asdict(config),
        "feature_names": list(feature_names),
        "coverage": {
            reference.alias: float(
                augmented[f"has_{reference.alias}_positioning_context"].mean()
            )
            for reference in config.references
        },
        "temporal_contract": (
            "metrics source + registered publication lag = available_time; "
            "backward as-of available_time <= strategy snapshot"
        ),
    }
    metadata_path.write_text(
        json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
    )
    manifest = build_manifest(
        run_id="reference-positioning-context-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(output_path, metadata_path), root=output_path.parent,
    )
    write_manifest(
        output_path.parent / f"{output_path.stem}.manifest.json", manifest
    )
    return output_path


def run_attach_event_positioning_context(
    *,
    input_path: Path,
    metrics_dir: Path,
    output_path: Path,
    config: EventScopedPositioningContextConfig,
) -> Path:
    rows = pd.read_parquet(input_path)
    symbols = tuple(sorted(rows["symbol"].astype(str).unique()))
    feature_frames: list[pd.DataFrame] = []
    metric_paths: list[Path] = []
    for symbol in symbols:
        path = metrics_dir / "symbols" / f"{symbol}.parquet"
        if not path.is_file():
            raise FileNotFoundError(f"event-scoped positioning cache not found: {path}")
        metrics = pd.read_parquet(path)
        feature_frames.append(
            build_event_positioning_features(metrics, symbol=symbol, config=config)
        )
        metric_paths.append(path)
    augmented = attach_event_positioning_context(
        rows,
        tuple(feature_frames),
        config=config,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    augmented.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    features = event_positioning_feature_names(config)
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "metrics_dir": str(metrics_dir.resolve()),
        "metrics_input_count": len(metric_paths),
        "metrics_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in metric_paths
        ],
        "input_row_count": len(rows),
        "output_row_count": len(augmented),
        "config": asdict(config),
        "feature_names": list(features),
        "current_context_coverage": float(
            augmented["has_symbol_positioning_context"].mean()
        ),
        "ignition_context_coverage": float(
            augmented["has_symbol_positioning_at_ignition"].mean()
        ),
        "complete_case_coverage": float(
            augmented["symbol_positioning_complete"].mean()
        ),
        "temporal_contract": (
            "same-symbol metrics publication available_time <= snapshot/ignition; "
            "backward as-of with bounded age; fixed changes require contiguous 5m history"
        ),
    }
    temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    temporary_metadata.write_text(
        json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary_metadata, metadata_path)
    manifest = build_manifest(
        run_id="event-positioning-context-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(output_path, metadata_path),
        root=output_path.parent,
    )
    write_manifest(
        output_path.parent / f"{output_path.stem}.manifest.json", manifest
    )
    return output_path


def run_attach_perp_crowding_context(
    *,
    input_path: Path,
    archive_dir: Path,
    output_path: Path,
    config: EventScopedPerpCrowdingContextConfig,
) -> Path:
    rows = pd.read_parquet(input_path)
    feature_frames: list[tuple[str, pd.DataFrame, pd.DataFrame]] = []
    archive_paths: list[Path] = []
    for symbol in sorted(rows["symbol"].astype(str).unique()):
        premium_path = archive_dir / "symbols" / f"{symbol}.premium.parquet"
        funding_path = archive_dir / "symbols" / f"{symbol}.funding.parquet"
        if not premium_path.is_file() or not funding_path.is_file():
            raise FileNotFoundError(f"perp crowding archive missing for {symbol}")
        premium = pd.read_parquet(premium_path)
        funding = pd.read_parquet(funding_path)
        premium_features, funding_features = build_perp_crowding_features(
            premium, funding, symbol=symbol, config=config
        )
        feature_frames.append((symbol, premium_features, funding_features))
        archive_paths.extend((premium_path, funding_path))
    augmented = attach_perp_crowding_context(rows, tuple(feature_frames), config=config)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    augmented.to_parquet(temporary, index=False)
    os.replace(temporary, output_path)
    metadata_path = output_path.with_suffix(".metadata.json")
    metadata = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "input_path": str(input_path.resolve()),
        "input_sha256": sha256_file(input_path),
        "archive_dir": str(archive_dir.resolve()),
        "archive_input_count": len(archive_paths),
        "archive_inputs": [
            {"path": str(path.resolve()), "sha256": sha256_file(path)}
            for path in archive_paths
        ],
        "input_row_count": len(rows),
        "output_row_count": len(augmented),
        "config": asdict(config),
        "feature_names": list(perp_crowding_feature_names(config)),
        "premium_coverage": float(augmented["has_symbol_premium_context"].mean()),
        "funding_coverage": float(augmented["has_symbol_funding_context"].mean()),
        "complete_case_coverage": float(augmented["perp_crowding_complete"].mean()),
        "temporal_contract": (
            "same-symbol premium/funding available_time <= snapshot; backward as-of "
            "with registered bounded ages"
        ),
    }
    temporary_metadata = metadata_path.with_suffix(metadata_path.suffix + ".tmp")
    temporary_metadata.write_text(
        json.dumps(metadata, indent=2, default=str) + "\n", encoding="utf-8"
    )
    os.replace(temporary_metadata, metadata_path)
    manifest = build_manifest(
        run_id="event-perp-crowding-context-"
        + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
        artifact_paths=(output_path, metadata_path),
        root=output_path.parent,
    )
    write_manifest(output_path.parent / f"{output_path.stem}.manifest.json", manifest)
    return output_path


__all__ = [
    "run_attach_event_positioning_context",
    "run_attach_reference_market_context",
    "run_attach_reference_positioning_context",
    "run_attach_perp_crowding_context",
]
