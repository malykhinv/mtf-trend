"""Build the full IS-only Stage-1 recovery-prediction dataset."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.binance_vision_cache import is_delivery_contract_symbol
from anomaly_science.market_context.minute_breadth import (
    MinuteBreadthSpec,
    build_minute_breadth_from_paths,
)
from anomaly_science.strategy.drawdown_ladder.spec import DRAWDOWN_LADDER_RESEARCH_SPLIT
from anomaly_science.strategy.drawdown_ladder.stage1_enrichment import (
    attach_stage1_breadth_and_concurrency,
    attach_stage1_prior_reactions,
)
from anomaly_science.strategy.drawdown_ladder.stage1_features import (
    PreparedBTCFrame,
    build_symbol_stage1_features,
    prepare_btc_frame,
)
from anomaly_science.strategy.drawdown_ladder.stage1_spec import (
    DrawdownLadderStage1Spec,
    build_stage1_feature_catalog,
    write_stage1_probability_configs,
    write_stage1_protocol,
)


class Stage1BuildError(ValueError):
    """Raised when the Stage-1 dataset cannot satisfy its frozen contract."""


@dataclass(frozen=True, slots=True)
class Stage1BuildConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    stage0_dir: Path = Path(".output/research/drawdown_ladder/stage0_is")
    output_dir: Path = Path(".output/research/drawdown_ladder/stage1_is")
    workers: int = 4
    max_inflight_symbols: int | None = None
    max_symbols: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 8:
            raise ValueError("Stage-1 workers must be between 1 and 8")
        if self.max_inflight_symbols is not None and self.max_inflight_symbols < self.workers:
            raise ValueError("Stage-1 max inflight cannot be lower than workers")
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise ValueError("Stage-1 max symbols must be positive")


@dataclass(frozen=True, slots=True)
class SymbolStage1Stats:
    symbol: str
    row_count: int
    complete_label_count: int
    positive_label_count: int


@dataclass(frozen=True, slots=True)
class Stage1BuildResult:
    output_dir: Path
    dataset_path: Path
    feature_catalog_path: Path
    missingness_path: Path
    breadth_path: Path
    audit_path: Path
    manifest_path: Path


_WORKER_BTC: PreparedBTCFrame | None = None


def _worker_init(btc_path: Path, spec: DrawdownLadderStage1Spec) -> None:
    global _WORKER_BTC
    _WORKER_BTC = prepare_btc_frame(btc_path, spec=spec)


def _typed_feature_frame(
    frame: pd.DataFrame,
    spec: DrawdownLadderStage1Spec,
) -> pd.DataFrame:
    catalog = build_stage1_feature_catalog(spec)
    expected = [definition.name for definition in catalog]
    missing = sorted(set(expected) - set(frame.columns))
    extra = sorted(set(frame.columns) - set(expected))
    if missing or extra:
        raise Stage1BuildError(f"Stage-1 shard schema mismatch; missing={missing}; extra={extra}")
    result = frame.loc[:, expected].copy()
    for definition in catalog:
        if definition.dtype == "string":
            result[definition.name] = result[definition.name].astype("string")
        elif definition.dtype == "bool":
            result[definition.name] = result[definition.name].astype("bool")
        elif definition.dtype == "int64":
            result[definition.name] = pd.array(result[definition.name], dtype="Int64")
        else:
            result[definition.name] = pd.array(result[definition.name], dtype="Float64")
    return result


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    os.replace(temporary, path)


def _build_symbol_task(
    source_path: Path,
    candidate_path: Path,
    outcome_path: Path,
    output_path: Path,
    spec: DrawdownLadderStage1Spec,
) -> SymbolStage1Stats:
    if _WORKER_BTC is None:
        raise Stage1BuildError("Stage-1 worker BTC context is not initialized")
    frame = build_symbol_stage1_features(
        source_path=source_path,
        candidate_path=candidate_path,
        outcome_path=outcome_path,
        btc_frame=_WORKER_BTC,
        spec=spec,
    )
    typed = _typed_feature_frame(frame, spec)
    _atomic_parquet(typed, output_path)
    complete = typed["label_available"].astype(bool)
    positive = complete & typed["recovery_25bps_48h"].astype(bool)
    return SymbolStage1Stats(
        symbol=source_path.stem.upper(),
        row_count=len(typed),
        complete_label_count=int(complete.sum()),
        positive_label_count=int(positive.sum()),
    )


def _iter_bounded_builds(
    tasks: list[tuple[Path, Path, Path, Path]],
    *,
    btc_path: Path,
    spec: DrawdownLadderStage1Spec,
    workers: int,
    max_inflight: int,
) -> Iterator[SymbolStage1Stats]:
    if workers == 1:
        _worker_init(btc_path, spec)
        for task in tasks:
            yield _build_symbol_task(*task, spec)
        return
    executor = ProcessPoolExecutor(
        max_workers=workers,
        initializer=_worker_init,
        initargs=(btc_path, spec),
    )
    pending: dict[Future[SymbolStage1Stats], int] = {}
    completed: dict[int, SymbolStage1Stats] = {}
    next_submit = 0
    next_emit = 0

    def submit_until_capacity() -> None:
        nonlocal next_submit
        while next_submit < len(tasks) and len(pending) + len(completed) < max_inflight:
            future = executor.submit(_build_symbol_task, *tasks[next_submit], spec)
            pending[future] = next_submit
            next_submit += 1

    try:
        submit_until_capacity()
        while next_emit < len(tasks):
            while next_emit not in completed:
                if not pending:
                    raise Stage1BuildError("bounded Stage-1 worker pool stalled")
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    completed[pending.pop(future)] = future.result()
                submit_until_capacity()
            yield completed.pop(next_emit)
            next_emit += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def _validate_stage0(stage0_dir: Path) -> None:
    audit_path = stage0_dir / "temporal_audit.json"
    if not audit_path.is_file():
        raise Stage1BuildError("Stage-0 temporal audit is missing")
    audit = json.loads(audit_path.read_text(encoding="utf-8"))
    if audit.get("status") != "PASS":
        raise Stage1BuildError("Stage-0 temporal audit is not PASS")
    if audit.get("protocol_freeze_id") != "drawdown_ladder_stage0_20260802_v1":
        raise Stage1BuildError("Stage-0 protocol is not the frozen long input")
    for check in audit.get("checks", {}).values():
        if int(check.get("untouched_2026_rows_used", -1)) != 0:
            raise Stage1BuildError("Stage-0 audit reports OOS access")


def _audit_dataset(
    frame: pd.DataFrame,
    *,
    spec: DrawdownLadderStage1Spec,
) -> dict[str, object]:
    if frame["candidate_id"].nunique() != len(frame):
        raise Stage1BuildError("Stage-1 candidate_id is not unique")
    if not bool(frame["feature_cutoff_time_ms"].le(frame["snapshot_time_ms"]).all()):
        raise Stage1BuildError("Stage-1 feature cutoff exceeds snapshot")
    if not bool(frame["future_start_time_ms"].gt(frame["snapshot_time_ms"]).all()):
        raise Stage1BuildError("Stage-1 future starts at or before snapshot")
    labeled = frame["label_available"].astype(bool)
    if labeled.any() and not bool(
        frame.loc[labeled, "label_resolution_time_ms"]
        .gt(frame.loc[labeled, "snapshot_time_ms"])
        .all()
    ):
        raise Stage1BuildError("Stage-1 label resolves at or before snapshot")
    if len(frame) and int(frame["snapshot_time_ms"].max()) >= spec.internal_wfa_end_ms:
        raise Stage1BuildError("Stage-1 dataset crossed into user OOS")
    catalog = build_stage1_feature_catalog(spec)
    model_names = {row.name for row in catalog if row.model_feature}
    forbidden = sorted(
        name
        for name in model_names
        if name.startswith("future_")
        or name.startswith("label_")
        or name == "recovery_25bps_48h"
    )
    if forbidden:
        raise Stage1BuildError(f"label/future fields entered model catalog: {forbidden}")
    return {
        "protocol_freeze_id": spec.protocol_freeze_id,
        "status": "PASS",
        "row_count": len(frame),
        "unique_candidate_ids": len(frame),
        "unique_parent_events": int(frame["parent_event_id"].nunique()),
        "feature_cutoff_le_snapshot": True,
        "future_start_gt_snapshot": True,
        "label_resolution_gt_snapshot": True,
        "model_feature_count": len(model_names),
        "label_fields_excluded_from_model": True,
        "maximum_snapshot_time_ms": int(frame["snapshot_time_ms"].max()),
        "oos_rows_read": 0,
    }


def build_stage1_is(
    config: Stage1BuildConfig = Stage1BuildConfig(),
    *,
    spec: DrawdownLadderStage1Spec = DrawdownLadderStage1Spec(),
    progress: Callable[[str], None] | None = print,
) -> Stage1BuildResult:
    if not config.source_dir.is_dir():
        raise FileNotFoundError(config.source_dir)
    _validate_stage0(config.stage0_dir)
    manifest_path = config.output_dir / "manifest.json"
    if config.output_dir.exists() and any(config.output_dir.iterdir()):
        raise Stage1BuildError(f"refusing to mix Stage-1 runs: {config.output_dir}")
    candidate_shards = config.stage0_dir / "shards" / "ladder_state_candidates"
    outcome_shards = config.stage0_dir / "shards" / "ladder_state_outcomes"
    if not candidate_shards.is_dir() or not outcome_shards.is_dir():
        raise Stage1BuildError("Stage-0 state shards are required for bounded Stage-1 build")
    source_paths = sorted(
        (
            path
            for path in config.source_dir.glob("*.parquet")
            if not is_delivery_contract_symbol(path.stem)
        ),
        key=lambda path: path.stem,
    )
    if config.max_symbols is not None:
        source_paths = source_paths[: config.max_symbols]
    if not source_paths:
        raise Stage1BuildError("no Stage-1 perpetual source paths")
    btc_path = config.source_dir / "BTCUSDT.parquet"
    if not btc_path.is_file():
        raise Stage1BuildError("BTCUSDT reference path is missing")
    tasks: list[tuple[Path, Path, Path, Path]] = []
    for source_path in source_paths:
        symbol = source_path.stem.upper()
        candidate_path = candidate_shards / f"{symbol}.parquet"
        outcome_path = outcome_shards / f"{symbol}.parquet"
        if not candidate_path.is_file() or not outcome_path.is_file():
            raise Stage1BuildError(f"Stage-0 state shard missing for {symbol}")
        output_path = config.output_dir / "shards" / "features" / f"{symbol}.parquet"
        tasks.append((source_path, candidate_path, outcome_path, output_path))
    config.output_dir.mkdir(parents=True, exist_ok=True)
    max_inflight = config.max_inflight_symbols or config.workers * 2
    stats: list[SymbolStage1Stats] = []
    for completed, item in enumerate(
        _iter_bounded_builds(
            tasks,
            btc_path=btc_path,
            spec=spec,
            workers=config.workers,
            max_inflight=max_inflight,
        ),
        start=1,
    ):
        stats.append(item)
        if progress and (completed % 25 == 0 or completed == len(tasks)):
            progress(
                f"drawdown Stage 1 features {completed}/{len(tasks)}; "
                f"rows={sum(row.row_count for row in stats):,}"
            )
    raw_path = config.output_dir / "stage1_symbol_features.parquet"
    shard_paths = sorted((config.output_dir / "shards" / "features").glob("*.parquet"))
    pl.scan_parquet([str(path) for path in shard_paths]).sink_parquet(
        raw_path,
        compression="zstd",
        engine="streaming",
    )
    raw = pd.read_parquet(raw_path)
    snapshot_times = raw["snapshot_time_ms"].astype("int64").unique()
    breadth_path = config.output_dir / "minute_breadth.parquet"
    breadth = build_minute_breadth_from_paths(
        source_paths,
        snapshot_times_ms=snapshot_times,
        start_time_ms=DRAWDOWN_LADDER_RESEARCH_SPLIT.is_start_time_ms,
        end_time_ms_exclusive=DRAWDOWN_LADDER_RESEARCH_SPLIT.oos_start_time_ms,
        workers=config.workers,
        spec=MinuteBreadthSpec(return_lags_minutes=spec.breadth_return_lags_minutes),
    )
    breadth.to_parquet(breadth_path, index=False, compression="zstd")
    enriched = attach_stage1_breadth_and_concurrency(raw, breadth, spec=spec)
    enriched = attach_stage1_prior_reactions(enriched, spec=spec)
    enriched = _typed_feature_frame(enriched, spec)
    dataset_path = config.output_dir / "stage1_recovery_dataset.parquet"
    _atomic_parquet(enriched, dataset_path)
    catalog = build_stage1_feature_catalog(spec)
    feature_catalog_path = config.output_dir / "feature_catalog.parquet"
    pd.DataFrame([asdict(row) for row in catalog]).to_parquet(
        feature_catalog_path,
        index=False,
        compression="zstd",
    )
    missingness_rows = []
    for definition in catalog:
        series = enriched[definition.name]
        missingness_rows.append(
            {
                **asdict(definition),
                "row_count": len(enriched),
                "missing_count": int(series.isna().sum()),
                "missing_fraction": float(series.isna().mean()),
            }
        )
    missingness_path = config.output_dir / "feature_missingness.parquet"
    pd.DataFrame(missingness_rows).to_parquet(
        missingness_path,
        index=False,
        compression="zstd",
    )
    protocol_path = write_stage1_protocol(config.output_dir / "frozen_stage1_protocol.json", spec)
    probability_config_paths = write_stage1_probability_configs(
        config.output_dir / "probability_configs",
        spec,
    )
    audit = _audit_dataset(enriched, spec=spec)
    audit_path = config.output_dir / "temporal_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    manifest = {
        "protocol": asdict(spec),
        "research_partition": "is",
        "oos_rows_read": 0,
        "source_dir": str(config.source_dir),
        "stage0_dir": str(config.stage0_dir),
        "output_dir": str(config.output_dir),
        "workers": config.workers,
        "max_inflight_symbols": max_inflight,
        "source_symbol_count": len(tasks),
        "row_count": len(enriched),
        "complete_label_count": int(enriched["label_available"].astype(bool).sum()),
        "positive_label_count": int(
            (enriched["label_available"].astype(bool)
            & enriched["recovery_25bps_48h"].astype(bool)).sum()
        ),
        "feature_count": sum(row.model_feature for row in catalog),
        "symbol_stats": [asdict(row) for row in sorted(stats, key=lambda row: row.symbol)],
        "artifacts": {
            "dataset": str(dataset_path),
            "raw_symbol_features": str(raw_path),
            "minute_breadth": str(breadth_path),
            "feature_catalog": str(feature_catalog_path),
            "feature_missingness": str(missingness_path),
            "protocol": str(protocol_path),
            "probability_configs": [str(path) for path in probability_config_paths],
            "temporal_audit": str(audit_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return Stage1BuildResult(
        output_dir=config.output_dir,
        dataset_path=dataset_path,
        feature_catalog_path=feature_catalog_path,
        missingness_path=missingness_path,
        breadth_path=breadth_path,
        audit_path=audit_path,
        manifest_path=manifest_path,
    )


__all__ = [
    "Stage1BuildConfig",
    "Stage1BuildError",
    "Stage1BuildResult",
    "SymbolStage1Stats",
    "build_stage1_is",
]
