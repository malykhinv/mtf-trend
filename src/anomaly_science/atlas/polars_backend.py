from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from anomaly_science.atlas.builder import (
    TEMPORAL_CONTRACT_TEXT,
    AtlasArtifacts,
    AtlasInputError,
    _atlas_context_columns,
    _atlas_feature_usecols,
    _atlas_future_usecols,
    _atlas_state_usecols,
    _bool_value,
    _btc_relative_frame_bin,
    _centered_percentile_value_bin,
    _consolidation_width_ratio_value_bin,
    _cvd_divergence_frame_bin,
    _detection_maturity_value_bin,
    _enforce_atlas_frame_temporal_contract,
    _high_position_atr_value_bin,
    _initial_pump_height_value_bin,
    _liquidation_regime_frame_bin,
    _market_context_frame_bin,
    _outcome_coordinate,
    _percentile_value_bin,
    _price_shape_atr_value_bin,
    _shelf_break_risk_value_bin,
    _shelf_position_value_bin,
    _shelf_reclaim_state_value_bin,
    _speed_regime_value_bin,
    _sweep_flow_frame_bin,
    _utc_session_bin,
    _build_atlas_artifacts_from_frame,
)
from anomaly_science.atlas.config import AtlasConfig
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.atlas import AtlasNatureRow
from anomaly_science.contracts.future import BARRIER_RESOLUTION_STOP_LOSS_FIRST

_JOIN_COLUMNS = ("event_id", "symbol", "snapshot_time_ms", "feature_cutoff_time_ms")


def _import_polars_for_atlas_backend():
    try:
        import polars as pl
    except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
        raise AtlasInputError(
            "polars is required for the explicit atlas Polars backend; "
            "install project dependencies instead of falling back to the legacy backend"
        ) from exc
    return pl


def build_atlas_artifacts_polars_from_csv_paths(
    *,
    state_path: str | Path,
    future_path: str | Path,
    feature_matrix_path: str | Path,
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    """Build atlas artifacts through an explicit Polars input backend.

    This is the first migration seam for replacing the legacy pandas/Python
    accumulator pipeline. It keeps the existing Atlas math/output path for now,
    but moves strict input reads, row-alignment checks, and feature Parquet-sidecar
    selection behind a Polars boundary that can be expanded slice-by-slice.
    The normal run-research path is intentionally not switched here.
    """
    cfg = config or AtlasConfig()
    state_frame = _read_strict_polars_csv_frame(
        path=Path(state_path),
        usecols=_atlas_state_usecols(),
    )
    future_frame = _read_strict_polars_csv_frame(
        path=Path(future_path),
        usecols=_atlas_future_usecols(cfg.outcome_horizon_minutes_list),
    )
    feature_frame = _read_feature_matrix_polars_frame(
        csv_path=Path(feature_matrix_path),
        usecols=_atlas_feature_usecols(),
    )
    joined = _join_row_aligned_polars_frames(
        state_frame=state_frame,
        future_frame=future_frame,
        feature_frame=feature_frame,
    )
    _enforce_atlas_polars_temporal_contract(joined)
    joined = _add_atlas_context_columns_polars(joined)
    nature_rows = _build_polars_nature_atlas_rows(joined=joined, config=cfg)

    # The remaining atlas outputs are intentionally still produced by the legacy
    # path in this migration step. Patch 3 only moves the toxic nature/context
    # grouping core off Python list/dict accumulation; response surfaces and market
    # summaries are migrated in the next patches.
    joined_pandas = joined.to_pandas()
    _enforce_atlas_frame_temporal_contract(joined_pandas)
    legacy_artifacts = _with_streaming_median_semantics(
        _build_atlas_artifacts_from_frame(joined=joined_pandas, config=cfg)
    )
    return AtlasArtifacts(
        nature_atlas_rows=tuple(nature_rows),
        context_split_rows=legacy_artifacts.context_split_rows,
        response_surface_rows=legacy_artifacts.response_surface_rows,
        market_shock_group_rows=legacy_artifacts.market_shock_group_rows,
    )


def _read_strict_polars_csv_frame(*, path: Path, usecols: Sequence[str]):
    _validate_strict_csv_header(path=path)
    pl = _import_polars_for_atlas_backend()
    return pl.read_csv(path, columns=list(usecols))


def _read_feature_matrix_polars_frame(*, csv_path: Path, usecols: Sequence[str]):
    _validate_strict_csv_header(path=csv_path)
    sidecar_path = csv_path.with_suffix(".parquet")
    manifest_path = csv_path.with_name(csv_path.stem + ".parquet_manifest.json")
    manifest_exists = manifest_path.is_file()
    sidecar_exists = sidecar_path.is_file()
    if manifest_exists != sidecar_exists:
        missing = sidecar_path if manifest_exists else manifest_path
        raise AtlasInputError(f"incomplete strategy_feature_matrix.parquet sidecar, missing {missing}")
    if not manifest_exists:
        pl = _import_polars_for_atlas_backend()
        return pl.read_csv(csv_path, columns=list(usecols))

    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    parquet_name = payload.get("parquet_path")
    if not isinstance(parquet_name, str) or not parquet_name:
        raise AtlasInputError(f"{manifest_path.name} must contain non-empty parquet_path")
    declared_columns = payload.get("columns")
    if declared_columns is not None and list(declared_columns) != list(get_artifact_schema(csv_path.name).required_columns):
        raise AtlasInputError(f"{manifest_path.name} columns do not match {csv_path.name} schema")
    parquet_path = manifest_path.parent / parquet_name
    if parquet_path != sidecar_path:
        raise AtlasInputError(f"{manifest_path.name} parquet_path must point to {sidecar_path.name}")
    pl = _import_polars_for_atlas_backend()
    return pl.read_parquet(parquet_path, columns=list(usecols))


def _validate_strict_csv_header(*, path: Path) -> None:
    schema = get_artifact_schema(path.name)
    pl = _import_polars_for_atlas_backend()
    actual_columns = pl.read_csv(path, n_rows=0).columns
    expected_columns = list(schema.required_columns)
    if actual_columns != expected_columns:
        raise AtlasInputError(f"{path.name} columns must match {expected_columns}, got {actual_columns}")


def _join_row_aligned_polars_frames(
    *,
    state_frame,
    future_frame,
    feature_frame,
):
    pl = _import_polars_for_atlas_backend()
    if state_frame.height != future_frame.height or state_frame.height != feature_frame.height:
        raise AtlasInputError("state/future/feature atlas frames must have identical row counts")
    for column in _JOIN_COLUMNS:
        if state_frame.get_column(column).to_list() != future_frame.get_column(column).to_list():
            raise AtlasInputError(f"state/future atlas frames must be row-aligned on {column}")
        if state_frame.get_column(column).to_list() != feature_frame.get_column(column).to_list():
            raise AtlasInputError(f"state/feature atlas frames must be row-aligned on {column}")
    return pl.concat(
        [
            state_frame,
            future_frame.drop(list(_JOIN_COLUMNS)),
            feature_frame.drop(list(_JOIN_COLUMNS)),
        ],
        how="horizontal",
    )


def _enforce_atlas_polars_temporal_contract(frame) -> None:
    pl = _import_polars_for_atlas_backend()
    violations = frame.filter(
        (pl.col("feature_cutoff_time_ms") > pl.col("snapshot_time_ms"))
        | (pl.col("future_start_time_ms") <= pl.col("snapshot_time_ms"))
    )
    if violations.height:
        raise AtlasInputError("atlas joined frame violates feature/future temporal contract")


def _add_atlas_context_columns_polars(frame):
    pl = _import_polars_for_atlas_backend()
    string_dtype = pl.String if hasattr(pl, "String") else pl.Utf8
    frame = frame.with_columns(
        [
            pl.col("range_since_start_atr")
            .map_elements(_price_shape_atr_value_bin, return_dtype=string_dtype)
            .alias("ctx_price_shape_atr"),
            pl.col("distance_to_running_high_atr")
            .map_elements(_high_position_atr_value_bin, return_dtype=string_dtype)
            .alias("ctx_high_position_atr"),
            pl.col("minutes_since_detection")
            .map_elements(_detection_maturity_value_bin, return_dtype=string_dtype)
            .alias("ctx_detection_maturity"),
            pl.col("event_alive")
            .map_elements(lambda value: "alive" if _bool_value(value) else "not_alive", return_dtype=string_dtype)
            .alias("ctx_state_liveness"),
            pl.col("alpha_decay_bucket").fill_null("").cast(string_dtype).alias("ctx_alpha_decay_bucket"),
            pl.col("price_speed_atr")
            .map_elements(_speed_regime_value_bin, return_dtype=string_dtype)
            .alias("ctx_speed_regime"),
            pl.col("snapshot_time_ms")
            .map_elements(_utc_session_bin, return_dtype=string_dtype)
            .alias("ctx_session"),
            pl.col("quote_volume_market_percentile")
            .map_elements(
                lambda value: _percentile_value_bin(value, missing="missing_volume_rank"),
                return_dtype=string_dtype,
            )
            .alias("ctx_volume_regime_relative"),
            pl.col("oi_growth_market_percentile")
            .map_elements(
                lambda value: _centered_percentile_value_bin(value, missing="missing_oi_rank"),
                return_dtype=string_dtype,
            )
            .alias("ctx_oi_regime_relative"),
            pl.struct(["liq_intensity_market_percentile", "liquidation_imbalance"])
            .map_elements(_liquidation_regime_frame_bin, return_dtype=string_dtype)
            .alias("ctx_liquidation_regime_relative"),
            pl.struct(
                [
                    "price_up_cvd_down_flag",
                    "price_down_cvd_up_flag",
                    "cvd_failed_to_confirm_high_flag",
                    "cvd_price_divergence_5m",
                ]
            )
            .map_elements(_cvd_divergence_frame_bin, return_dtype=string_dtype)
            .alias("ctx_cvd_divergence_regime"),
            pl.col("return_from_event_market_percentile")
            .map_elements(
                lambda value: _percentile_value_bin(value, missing="missing_return_rank"),
                return_dtype=string_dtype,
            )
            .alias("ctx_cross_sectional_rank_regime"),
            pl.struct(["idiosyncratic_momentum_score", "symbol_return_minus_btc_return_15m"])
            .map_elements(_btc_relative_frame_bin, return_dtype=string_dtype)
            .alias("ctx_btc_relative_regime"),
            pl.col("systemic_cluster_regime").fill_null("").cast(string_dtype).alias("ctx_systemic_cluster_regime"),
            pl.col("initial_pump_height_core_atr_1440")
            .map_elements(_initial_pump_height_value_bin, return_dtype=string_dtype)
            .alias("ctx_initial_pump_height_atr"),
            pl.col("consolidation_width_ratio")
            .map_elements(_consolidation_width_ratio_value_bin, return_dtype=string_dtype)
            .alias("ctx_consolidation_width_ratio"),
            pl.col("current_close_minus_shelf_low_core_atr_1440")
            .map_elements(_shelf_position_value_bin, return_dtype=string_dtype)
            .alias("ctx_shelf_position_atr"),
            pl.col("current_low_minus_shelf_low_core_atr_1440")
            .map_elements(_shelf_break_risk_value_bin, return_dtype=string_dtype)
            .alias("ctx_shelf_break_risk"),
            pl.col("minutes_since_reclaim")
            .map_elements(_shelf_reclaim_state_value_bin, return_dtype=string_dtype)
            .alias("ctx_shelf_reclaim_state"),
            pl.struct(["volume_on_sweep_percentile", "liq_intensity_during_sweep"])
            .map_elements(_sweep_flow_frame_bin, return_dtype=string_dtype)
            .alias("ctx_sweep_flow_regime"),
            pl.lit("feature_matrix_joined").alias("ctx_feature_matrix"),
        ]
    )
    return frame.with_columns(
        pl.struct(["systemic_cluster_regime", "ctx_btc_relative_regime"])
        .map_elements(_market_context_frame_bin, return_dtype=string_dtype)
        .alias("ctx_market_context")
    )


def _build_polars_nature_atlas_rows(*, joined, config: AtlasConfig) -> tuple[AtlasNatureRow, ...]:
    rows: list[AtlasNatureRow] = []
    for horizon in config.outcome_horizon_minutes_list:
        horizon_frame = _with_polars_horizon_columns(joined, horizon=horizon, config=config)
        for context_name, context_column in _atlas_context_columns():
            rows.extend(
                _polars_nature_rows_for_context(
                    frame=horizon_frame,
                    context_name=context_name,
                    context_column=context_column,
                    horizon=horizon,
                    config=config,
                )
            )
    return tuple(rows)


def _with_polars_horizon_columns(frame, *, horizon: int, config: AtlasConfig):
    pl = _import_polars_for_atlas_backend()
    suffix = f"{horizon}m"
    future_return_atr = pl.col(f"future_return_atr_{suffix}")
    future_max_atr = pl.col(f"future_max_atr_{suffix}")
    future_min_atr = pl.col(f"future_min_atr_{suffix}")
    stop_first = pl.col(f"barrier_resolution_{suffix}") == BARRIER_RESOLUTION_STOP_LOSS_FIRST
    missing = future_return_atr.is_null() | future_max_atr.is_null() | future_min_atr.is_null()
    upside = future_max_atr >= config.outcome_continuation_threshold_atr
    downside = future_min_atr <= -config.outcome_fade_threshold_atr
    range_chop = (
        (future_return_atr.abs() <= config.outcome_chop_threshold_atr)
        & (future_max_atr < config.outcome_continuation_threshold_atr)
        & (future_min_atr > -config.outcome_fade_threshold_atr)
    )
    mixed = f"mixed_drift_atr_{suffix}"
    outcome_expr = (
        pl.when(missing)
        .then(pl.lit(f"missing_atr_future_{suffix}"))
        .when(stop_first & ~missing)
        .then(pl.lit(f"stop_first_double_barrier_{suffix}"))
        .when(upside & downside & ~missing & ~stop_first)
        .then(pl.lit(f"two_sided_atr_{suffix}"))
        .when(upside & ~downside & ~missing & ~stop_first)
        .then(pl.lit(f"upside_continuation_atr_{suffix}"))
        .when(downside & ~upside & ~missing & ~stop_first)
        .then(pl.lit(f"downside_extension_atr_{suffix}"))
        .when(range_chop & ~missing & ~stop_first & ~upside & ~downside)
        .then(pl.lit(f"range_chop_atr_{suffix}"))
        .otherwise(pl.lit(mixed))
    )
    reclaimed_column = "reclaimed_running_high_30m" if horizon <= 30 else "reclaimed_running_high_60m"
    return frame.with_columns(
        [
            pl.lit(horizon).alias("_horizon"),
            outcome_expr.alias("_outcome_bin"),
            pl.col(f"future_return_atr_{suffix}").alias("_future_return_atr"),
            pl.col(f"future_max_atr_{suffix}").alias("_future_max_atr"),
            pl.col(f"future_min_atr_{suffix}").alias("_future_min_atr"),
            pl.col(f"future_return_{suffix}").alias("_future_return"),
            pl.col(f"future_max_{suffix}").alias("_future_max"),
            pl.col(f"future_min_{suffix}").alias("_future_min"),
            pl.col(reclaimed_column).alias("_reclaimed_running_high"),
            stop_first.alias("_stop_first"),
        ]
    )


def _polars_nature_rows_for_context(
    *,
    frame,
    context_name: str,
    context_column: str,
    horizon: int,
    config: AtlasConfig,
) -> list[AtlasNatureRow]:
    pl = _import_polars_for_atlas_backend()
    grouped = (
        frame.group_by([context_column, "_outcome_bin"])
        .agg(
            [
                pl.col("event_id").count().alias("row_count"),
                pl.col("event_id").n_unique().alias("unique_event_count"),
                pl.col("symbol").n_unique().alias("unique_symbol_count"),
                pl.col("_future_return_atr").mean().alias("mean_future_return_atr"),
                pl.col("_future_max_atr").mean().alias("mean_future_max_atr"),
                pl.col("_future_min_atr").mean().alias("mean_future_min_atr"),
                pl.col("_future_return").mean().alias("mean_future_return"),
                pl.col("_future_max").mean().alias("mean_future_max"),
                pl.col("_future_min").mean().alias("mean_future_min"),
                pl.col("_reclaimed_running_high").cast(pl.Float64, strict=False).mean().alias("reclaim_rate"),
                pl.col("_stop_first").cast(pl.Float64, strict=False).mean().alias("double_barrier_stop_first_rate"),
                pl.col("snapshot_time_ms").min().alias("feature_min_snapshot_time_ms"),
                pl.col("snapshot_time_ms").max().alias("feature_max_snapshot_time_ms"),
            ]
        )
        .sort([context_column, "_outcome_bin"])
    )
    rows: list[AtlasNatureRow] = []
    for item in grouped.iter_rows(named=True):
        rows.append(
            AtlasNatureRow(
                atlas_version=config.atlas_version,
                split_family=context_name,
                split_value=str(item[context_column]),
                outcome_horizon_minutes=horizon,
                atlas_outcome_bin=str(item["_outcome_bin"]),
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=int(item["row_count"]),
                unique_event_count=int(item["unique_event_count"]),
                unique_symbol_count=int(item["unique_symbol_count"]),
                mean_future_return_atr=_optional_float(item["mean_future_return_atr"]),
                median_future_return_atr=None,
                mean_future_max_atr=_optional_float(item["mean_future_max_atr"]),
                mean_future_min_atr=_optional_float(item["mean_future_min_atr"]),
                mean_future_return=_optional_float(item["mean_future_return"]),
                median_future_return=None,
                mean_future_max=_optional_float(item["mean_future_max"]),
                mean_future_min=_optional_float(item["mean_future_min"]),
                reclaim_rate=_optional_float(item["reclaim_rate"]),
                double_barrier_stop_first_rate=_optional_float(item["double_barrier_stop_first_rate"]),
                feature_min_snapshot_time_ms=int(item["feature_min_snapshot_time_ms"]),
                feature_max_snapshot_time_ms=int(item["feature_max_snapshot_time_ms"]),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return rows


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    if number != number:
        return None
    return number


def _with_streaming_median_semantics(artifacts: AtlasArtifacts) -> AtlasArtifacts:
    return AtlasArtifacts(
        nature_atlas_rows=tuple(
            replace(row, median_future_return_atr=None, median_future_return=None)
            for row in artifacts.nature_atlas_rows
        ),
        context_split_rows=artifacts.context_split_rows,
        response_surface_rows=artifacts.response_surface_rows,
        market_shock_group_rows=artifacts.market_shock_group_rows,
    )
