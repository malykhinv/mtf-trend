from __future__ import annotations

import math
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence

import pandas as pd

from anomaly_science.atlas.config import AtlasConfig
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.atlas import (
    AtlasContextSplitRow,
    AtlasMarketShockGroupRow,
    AtlasNatureRow,
    AtlasResponseSurfaceRow,
)
from anomaly_science.contracts.features import StrategyFeatureMatrixRow
from anomaly_science.contracts.future import BARRIER_RESOLUTION_STOP_LOSS_FIRST, FuturePathRow
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.features.matrix import load_strategy_feature_matrix_csv
from anomaly_science.future.builder import (
    AnomalyFutureArtifactError,
    AnomalyStateArtifactError,
    load_strategy_future_paths_csv,
    load_strategy_state_1m_csv,
)

load_anomaly_future_paths_csv = load_strategy_future_paths_csv

TEMPORAL_CONTRACT_TEXT = "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"
OUTCOME_COORDINATE_ATR = "ATR_normalized_30m"


class AtlasInputError(ValueError):
    """Raised when atlas input artifacts cannot be joined one-to-one."""


@dataclass(frozen=True, slots=True)
class AtlasInputRow:
    state: StrategyState1mRow
    future: FuturePathRow
    feature: StrategyFeatureMatrixRow
    contexts: tuple[tuple[str, str], ...]
    atlas_outcome_bins: tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class AtlasArtifacts:
    nature_atlas_rows: tuple[AtlasNatureRow, ...]
    context_split_rows: tuple[AtlasContextSplitRow, ...]
    response_surface_rows: tuple[AtlasResponseSurfaceRow, ...]
    market_shock_group_rows: tuple[AtlasMarketShockGroupRow, ...]


def build_atlas_artifacts_from_csv_paths(
    *,
    state_path: str | Path,
    future_path: str | Path,
    feature_matrix_path: str | Path,
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    """Build atlas artifacts from strict CSV boundaries using vectorized grouping."""
    cfg = config or AtlasConfig()
    state_frame = _read_atlas_state_frame(Path(state_path))
    future_frame = _read_atlas_future_frame(Path(future_path), horizons=cfg.outcome_horizon_minutes_list)
    feature_frame = _read_atlas_feature_frame(Path(feature_matrix_path))
    joined = state_frame.merge(
        future_frame,
        on=["event_id", "symbol", "snapshot_time_ms", "feature_cutoff_time_ms"],
        how="inner",
        validate="one_to_one",
    ).merge(
        feature_frame,
        on=["event_id", "symbol", "snapshot_time_ms", "feature_cutoff_time_ms"],
        how="inner",
        validate="one_to_one",
    )
    if len(joined) != len(state_frame) or len(joined) != len(future_frame) or len(joined) != len(feature_frame):
        raise AtlasInputError("state/future/feature atlas join must be one-to-one on event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms")
    _enforce_atlas_frame_temporal_contract(joined)
    _add_atlas_context_columns(joined)
    return _build_atlas_artifacts_from_frame(joined=joined, config=cfg)


def _read_atlas_state_frame(path: Path) -> pd.DataFrame:
    usecols = [
        "event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "minutes_since_detection",
        "event_alive",
        "current_return_from_start",
    ]
    return _read_strict_artifact_frame(path=path, usecols=usecols)


def _read_atlas_future_frame(path: Path, *, horizons: Sequence[int]) -> pd.DataFrame:
    horizon_columns: list[str] = []
    for horizon in horizons:
        suffix = f"{horizon}m"
        horizon_columns.extend(
            [
                f"future_return_{suffix}",
                f"future_max_{suffix}",
                f"future_min_{suffix}",
                f"future_return_atr_{suffix}",
                f"future_max_atr_{suffix}",
                f"future_min_atr_{suffix}",
                f"barrier_resolution_{suffix}",
            ]
        )
    usecols = [
        "event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "future_start_time_ms",
        "reclaimed_running_high_30m",
        "reclaimed_running_high_60m",
        *horizon_columns,
    ]
    return _read_strict_artifact_frame(path=path, usecols=usecols)


def _read_atlas_feature_frame(path: Path) -> pd.DataFrame:
    usecols = [
        "event_id",
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "minutes_since_trigger",
        "range_since_start_atr",
        "distance_to_running_high_atr",
        "price_speed_atr",
        "alpha_decay_bucket",
        "quote_volume_market_percentile",
        "oi_growth_market_percentile",
        "liq_intensity_market_percentile",
        "liquidation_imbalance",
        "return_from_event_market_percentile",
        "cvd_price_divergence_5m",
        "price_up_cvd_down_flag",
        "price_down_cvd_up_flag",
        "cvd_failed_to_confirm_high_flag",
        "corr_with_btc_30m",
        "symbol_return_minus_btc_return_15m",
        "idiosyncratic_momentum_score",
        "simultaneous_anomalies_count_1m",
        "simultaneous_anomalies_share_1m",
        "systemic_cluster_regime",
        "market_shock_id",
        "initial_pump_height_core_atr_1440",
        "consolidation_width_ratio",
        "current_close_minus_shelf_low_core_atr_1440",
        "current_low_minus_shelf_low_core_atr_1440",
        "minutes_since_reclaim",
        "volume_on_sweep_percentile",
        "liq_intensity_during_sweep",
    ]
    return _read_strict_artifact_frame(path=path, usecols=usecols)


def _read_strict_artifact_frame(*, path: Path, usecols: Sequence[str]) -> pd.DataFrame:
    schema = get_artifact_schema(path.name)
    actual_columns = list(pd.read_csv(path, nrows=0).columns)
    expected_columns = list(schema.required_columns)
    if actual_columns != expected_columns:
        raise AtlasInputError(f"{path.name} columns must match {expected_columns}, got {actual_columns}")
    return pd.read_csv(path, usecols=list(usecols), low_memory=False)


def _enforce_atlas_frame_temporal_contract(frame: pd.DataFrame) -> None:
    if not (frame["feature_cutoff_time_ms"] <= frame["snapshot_time_ms"]).all():
        raise AtlasInputError("atlas joined frame violates feature_cutoff_time_ms <= snapshot_time_ms")
    if not (frame["future_start_time_ms"] > frame["snapshot_time_ms"]).all():
        raise AtlasInputError("atlas joined frame violates snapshot_time_ms < future_start_time_ms")


def _add_atlas_context_columns(frame: pd.DataFrame) -> None:
    frame["ctx_price_shape_atr"] = frame["range_since_start_atr"].map(_price_shape_atr_value_bin)
    frame["ctx_high_position_atr"] = frame["distance_to_running_high_atr"].map(_high_position_atr_value_bin)
    frame["ctx_detection_maturity"] = frame["minutes_since_detection"].map(_detection_maturity_value_bin)
    frame["ctx_state_liveness"] = frame["event_alive"].map(lambda value: "alive" if _bool_value(value) else "not_alive")
    frame["ctx_alpha_decay_bucket"] = frame["alpha_decay_bucket"].fillna("")
    frame["ctx_speed_regime"] = frame["price_speed_atr"].map(_speed_regime_value_bin)
    frame["ctx_session"] = frame["snapshot_time_ms"].map(_utc_session_bin)
    frame["ctx_volume_regime_relative"] = frame["quote_volume_market_percentile"].map(
        lambda value: _percentile_value_bin(value, missing="missing_volume_rank")
    )
    frame["ctx_oi_regime_relative"] = frame["oi_growth_market_percentile"].map(
        lambda value: _centered_percentile_value_bin(value, missing="missing_oi_rank")
    )
    frame["ctx_liquidation_regime_relative"] = frame.apply(_liquidation_regime_frame_bin, axis=1)
    frame["ctx_cvd_divergence_regime"] = frame.apply(_cvd_divergence_frame_bin, axis=1)
    frame["ctx_cross_sectional_rank_regime"] = frame["return_from_event_market_percentile"].map(
        lambda value: _percentile_value_bin(value, missing="missing_return_rank")
    )
    frame["ctx_btc_relative_regime"] = frame.apply(_btc_relative_frame_bin, axis=1)
    frame["ctx_systemic_cluster_regime"] = frame["systemic_cluster_regime"].fillna("")
    frame["ctx_initial_pump_height_atr"] = frame["initial_pump_height_core_atr_1440"].map(_initial_pump_height_value_bin)
    frame["ctx_consolidation_width_ratio"] = frame["consolidation_width_ratio"].map(_consolidation_width_ratio_value_bin)
    frame["ctx_shelf_position_atr"] = frame["current_close_minus_shelf_low_core_atr_1440"].map(_shelf_position_value_bin)
    frame["ctx_shelf_break_risk"] = frame["current_low_minus_shelf_low_core_atr_1440"].map(_shelf_break_risk_value_bin)
    frame["ctx_shelf_reclaim_state"] = frame["minutes_since_reclaim"].map(_shelf_reclaim_state_value_bin)
    frame["ctx_sweep_flow_regime"] = frame.apply(_sweep_flow_frame_bin, axis=1)
    frame["ctx_market_context"] = frame.apply(_market_context_frame_bin, axis=1)
    frame["ctx_feature_matrix"] = "feature_matrix_joined"


def _build_atlas_artifacts_from_frame(*, joined: pd.DataFrame, config: AtlasConfig) -> AtlasArtifacts:
    context_columns = _atlas_context_columns()
    nature_rows: list[AtlasNatureRow] = []
    context_rows: list[AtlasContextSplitRow] = []
    response_rows: list[AtlasResponseSurfaceRow] = []
    market_rows: list[AtlasMarketShockGroupRow] = []

    for horizon in config.outcome_horizon_minutes_list:
        horizon_frame = joined.copy(deep=False)
        _add_horizon_columns(horizon_frame, horizon=horizon, config=config)
        for context_name, column_name in context_columns:
            nature_rows.extend(_nature_rows_from_grouped_frame(horizon_frame, context_name, column_name, horizon, config))
            context_rows.extend(_context_rows_from_grouped_frame(horizon_frame, context_name, column_name, horizon, config))
        response_rows.extend(_response_rows_from_grouped_frame(horizon_frame, horizon, config))
        market_rows.extend(_market_rows_from_grouped_frame(horizon_frame, horizon, config))

    return AtlasArtifacts(
        nature_atlas_rows=tuple(nature_rows),
        context_split_rows=tuple(context_rows),
        response_surface_rows=tuple(response_rows),
        market_shock_group_rows=tuple(market_rows),
    )


def _atlas_context_columns() -> tuple[tuple[str, str], ...]:
    return (
        ("price_shape_atr", "ctx_price_shape_atr"),
        ("high_position_atr", "ctx_high_position_atr"),
        ("detection_maturity", "ctx_detection_maturity"),
        ("state_liveness", "ctx_state_liveness"),
        ("alpha_decay_bucket", "ctx_alpha_decay_bucket"),
        ("speed_regime", "ctx_speed_regime"),
        ("session", "ctx_session"),
        ("market_context", "ctx_market_context"),
        ("volume_regime_relative", "ctx_volume_regime_relative"),
        ("oi_regime_relative", "ctx_oi_regime_relative"),
        ("liquidation_regime_relative", "ctx_liquidation_regime_relative"),
        ("cvd_divergence_regime", "ctx_cvd_divergence_regime"),
        ("cross_sectional_rank_regime", "ctx_cross_sectional_rank_regime"),
        ("btc_relative_regime", "ctx_btc_relative_regime"),
        ("systemic_cluster_regime", "ctx_systemic_cluster_regime"),
        ("initial_pump_height_atr", "ctx_initial_pump_height_atr"),
        ("consolidation_width_ratio", "ctx_consolidation_width_ratio"),
        ("shelf_position_atr", "ctx_shelf_position_atr"),
        ("shelf_break_risk", "ctx_shelf_break_risk"),
        ("shelf_reclaim_state", "ctx_shelf_reclaim_state"),
        ("sweep_flow_regime", "ctx_sweep_flow_regime"),
        ("feature_matrix", "ctx_feature_matrix"),
    )


def _add_horizon_columns(frame: pd.DataFrame, *, horizon: int, config: AtlasConfig) -> None:
    suffix = f"{horizon}m"
    frame["_horizon"] = horizon
    frame["_outcome_bin"] = _outcome_bin_series(frame, horizon=horizon, config=config)
    frame["_future_return_atr"] = frame[f"future_return_atr_{suffix}"]
    frame["_future_max_atr"] = frame[f"future_max_atr_{suffix}"]
    frame["_future_min_atr"] = frame[f"future_min_atr_{suffix}"]
    frame["_future_return"] = frame[f"future_return_{suffix}"]
    frame["_future_max"] = frame[f"future_max_{suffix}"]
    frame["_future_min"] = frame[f"future_min_{suffix}"]
    frame["_reclaimed_running_high"] = frame["reclaimed_running_high_30m"] if horizon <= 30 else frame["reclaimed_running_high_60m"]
    frame["_stop_first"] = frame[f"barrier_resolution_{suffix}"] == BARRIER_RESOLUTION_STOP_LOSS_FIRST


def _outcome_bin_series(frame: pd.DataFrame, *, horizon: int, config: AtlasConfig) -> pd.Series:
    suffix = f"{horizon}m"
    result = pd.Series(f"mixed_drift_atr_{suffix}", index=frame.index, dtype="object")
    future_return = frame[f"future_return_atr_{suffix}"]
    future_max = frame[f"future_max_atr_{suffix}"]
    future_min = frame[f"future_min_atr_{suffix}"]
    missing = future_return.isna() | future_max.isna() | future_min.isna()
    stop_first = frame[f"barrier_resolution_{suffix}"] == BARRIER_RESOLUTION_STOP_LOSS_FIRST
    upside = future_max >= config.outcome_continuation_threshold_atr
    downside = future_min <= -config.outcome_fade_threshold_atr
    range_chop = (
        future_return.abs().le(config.outcome_chop_threshold_atr)
        & future_max.lt(config.outcome_continuation_threshold_atr)
        & future_min.gt(-config.outcome_fade_threshold_atr)
    )
    result.loc[missing] = f"missing_atr_future_{suffix}"
    result.loc[stop_first & ~missing] = f"stop_first_double_barrier_{suffix}"
    result.loc[upside & downside & ~missing & ~stop_first] = f"two_sided_atr_{suffix}"
    result.loc[upside & ~downside & ~missing & ~stop_first] = f"upside_continuation_atr_{suffix}"
    result.loc[downside & ~upside & ~missing & ~stop_first] = f"downside_extension_atr_{suffix}"
    result.loc[range_chop & ~missing & ~stop_first & ~upside & ~downside] = f"range_chop_atr_{suffix}"
    return result


def _nature_rows_from_grouped_frame(
    frame: pd.DataFrame,
    context_name: str,
    context_column: str,
    horizon: int,
    config: AtlasConfig,
) -> list[AtlasNatureRow]:
    grouped = frame.groupby([context_column, "_outcome_bin"], dropna=False, sort=True)
    rows: list[AtlasNatureRow] = []
    for (context_value, outcome_bin), group in grouped:
        rows.append(
            AtlasNatureRow(
                atlas_version=config.atlas_version,
                split_family=context_name,
                split_value=str(context_value),
                outcome_horizon_minutes=horizon,
                atlas_outcome_bin=str(outcome_bin),
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=int(len(group)),
                unique_event_count=int(group["event_id"].nunique()),
                unique_symbol_count=int(group["symbol"].nunique()),
                mean_future_return_atr=_series_mean(group["_future_return_atr"]),
                median_future_return_atr=_series_median(group["_future_return_atr"]),
                mean_future_max_atr=_series_mean(group["_future_max_atr"]),
                mean_future_min_atr=_series_mean(group["_future_min_atr"]),
                mean_future_return=_series_mean(group["_future_return"]),
                median_future_return=_series_median(group["_future_return"]),
                mean_future_max=_series_mean(group["_future_max"]),
                mean_future_min=_series_mean(group["_future_min"]),
                reclaim_rate=_series_bool_rate(group["_reclaimed_running_high"]),
                double_barrier_stop_first_rate=_series_bool_rate(group["_stop_first"]),
                feature_min_snapshot_time_ms=int(group["snapshot_time_ms"].min()),
                feature_max_snapshot_time_ms=int(group["snapshot_time_ms"].max()),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return rows


def _context_rows_from_grouped_frame(
    frame: pd.DataFrame,
    context_name: str,
    context_column: str,
    horizon: int,
    config: AtlasConfig,
) -> list[AtlasContextSplitRow]:
    grouped = frame.groupby(context_column, dropna=False, sort=True)
    rows: list[AtlasContextSplitRow] = []
    expected_bins = {
        "upside_continuation_share": _outcome_bin_name("upside_continuation", horizon),
        "downside_extension_share": _outcome_bin_name("downside_extension", horizon),
        "two_sided_share": _outcome_bin_name("two_sided", horizon),
        "range_chop_share": _outcome_bin_name("range_chop", horizon),
        "mixed_drift_share": _outcome_bin_name("mixed_drift", horizon),
        "missing_atr_future_share": _outcome_bin_name("missing_atr_future", horizon),
        "stop_first_barrier_share": _outcome_bin_name("stop_first_double_barrier", horizon),
    }
    for context_value, group in grouped:
        counts = group["_outcome_bin"].value_counts()
        row_count = int(len(group))
        shares = {name: float(counts.get(outcome_bin, 0) / row_count) for name, outcome_bin in expected_bins.items()}
        rows.append(
            AtlasContextSplitRow(
                atlas_version=config.atlas_version,
                context_name=context_name,
                context_value=str(context_value),
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=row_count,
                unique_event_count=int(group["event_id"].nunique()),
                unique_symbol_count=int(group["symbol"].nunique()),
                mean_minutes_since_trigger=_series_mean(group["minutes_since_trigger"]),
                mean_current_return_from_start=_series_mean(group["current_return_from_start"]),
                mean_distance_to_running_high_atr=_series_mean(group["distance_to_running_high_atr"]),
                **shares,
            )
        )
    return rows


def _response_rows_from_grouped_frame(frame: pd.DataFrame, horizon: int, config: AtlasConfig) -> list[AtlasResponseSurfaceRow]:
    surfaces = (
        ("price_shape_atr_x_alpha_decay", "price_shape_atr", "ctx_price_shape_atr", "alpha_decay_bucket", "ctx_alpha_decay_bucket"),
        ("liquidation_x_cvd_divergence", "liquidation_regime_relative", "ctx_liquidation_regime_relative", "cvd_divergence_regime", "ctx_cvd_divergence_regime"),
        ("cross_section_x_systemic_cluster", "cross_sectional_rank_regime", "ctx_cross_sectional_rank_regime", "systemic_cluster_regime", "ctx_systemic_cluster_regime"),
        ("btc_relative_x_volume_rank", "btc_relative_regime", "ctx_btc_relative_regime", "volume_regime_relative", "ctx_volume_regime_relative"),
        ("session_x_market_context", "session", "ctx_session", "market_context", "ctx_market_context"),
        ("speed_x_alpha_decay", "speed_regime", "ctx_speed_regime", "alpha_decay_bucket", "ctx_alpha_decay_bucket"),
        ("initial_pump_x_consolidation", "initial_pump_height_atr", "ctx_initial_pump_height_atr", "consolidation_width_ratio", "ctx_consolidation_width_ratio"),
        ("shelf_position_x_alpha_decay", "shelf_position_atr", "ctx_shelf_position_atr", "alpha_decay_bucket", "ctx_alpha_decay_bucket"),
        ("shelf_break_x_systemic_cluster", "shelf_break_risk", "ctx_shelf_break_risk", "systemic_cluster_regime", "ctx_systemic_cluster_regime"),
        ("sweep_flow_x_liquidation", "sweep_flow_regime", "ctx_sweep_flow_regime", "liquidation_regime_relative", "ctx_liquidation_regime_relative"),
    )
    rows: list[AtlasResponseSurfaceRow] = []
    for surface_name, x_axis, x_column, y_axis, y_column in surfaces:
        grouped = frame.groupby([x_column, y_column], dropna=False, sort=True)
        for (x_bin, y_bin), group in grouped:
            rows.append(
                AtlasResponseSurfaceRow(
                    atlas_version=config.atlas_version,
                    surface_name=surface_name,
                    x_axis=x_axis,
                    x_bin=str(x_bin),
                    y_axis=y_axis,
                    y_bin=str(y_bin),
                    outcome_horizon_minutes=horizon,
                    outcome_coordinate=_outcome_coordinate(horizon),
                    row_count=int(len(group)),
                    unique_event_count=int(group["event_id"].nunique()),
                    mean_future_return_atr=_series_mean(group["_future_return_atr"]),
                    mean_future_max_atr=_series_mean(group["_future_max_atr"]),
                    mean_future_min_atr=_series_mean(group["_future_min_atr"]),
                    reclaim_rate=_series_bool_rate(group["_reclaimed_running_high"]),
                    double_barrier_stop_first_rate=_series_bool_rate(group["_stop_first"]),
                    dominant_outcome_bin=str(group["_outcome_bin"].value_counts().idxmax()),
                )
            )
    return rows


def _market_rows_from_grouped_frame(frame: pd.DataFrame, horizon: int, config: AtlasConfig) -> list[AtlasMarketShockGroupRow]:
    grouped = frame.groupby(["market_shock_id", "systemic_cluster_regime", "snapshot_time_ms"], dropna=False, sort=True)
    rows: list[AtlasMarketShockGroupRow] = []
    for (market_shock_id, systemic_cluster_regime, snapshot_time_ms), group in grouped:
        symbols = sorted(str(symbol) for symbol in group["symbol"].dropna().unique())
        rows.append(
            AtlasMarketShockGroupRow(
                atlas_version=config.atlas_version,
                market_shock_group_id=f"{market_shock_id}:{int(snapshot_time_ms)}:{horizon}m",
                market_shock_id=str(market_shock_id),
                systemic_cluster_regime=str(systemic_cluster_regime),
                snapshot_time_ms=int(snapshot_time_ms),
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=int(len(group)),
                unique_event_count=int(group["event_id"].nunique()),
                unique_symbol_count=len(symbols),
                simultaneous_anomalies_count_1m=int(group["simultaneous_anomalies_count_1m"].max()),
                simultaneous_anomalies_share_1m=_series_mean(group["simultaneous_anomalies_share_1m"]),
                symbols="|".join(symbols),
                market_shock_candidate=str(systemic_cluster_regime) == "systemic_beta_shock"
                or len(symbols) >= config.min_symbols_for_market_shock_candidate,
                mean_current_return_from_start=_series_mean(group["current_return_from_start"]),
                mean_future_return_atr=_series_mean(group["_future_return_atr"]),
                dominant_outcome_bin=str(group["_outcome_bin"].value_counts().idxmax()),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return rows


def _series_mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def _series_median(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.median())


def _series_bool_rate(series: pd.Series) -> float | None:
    observed = series.dropna()
    if observed.empty:
        return None
    return float(observed.map(_bool_value).mean())


def _bool_value(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1"}


def _price_shape_atr_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_range_since_start_atr"
    if number >= 3.0:
        return "strong_range_expansion_atr"
    if number >= 1.5:
        return "moderate_range_expansion_atr"
    return "muted_range_expansion_atr"


def _high_position_atr_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_distance_to_running_high_atr"
    if number <= 0.25:
        return "at_or_near_running_high_atr"
    if number <= 0.75:
        return "shallow_pullback_from_high_atr"
    if number <= 1.5:
        return "deep_pullback_from_high_atr"
    return "far_below_running_high_atr"


def _detection_maturity_value_bin(value: object) -> str:
    number = int(value)
    if number <= 2:
        return "early_after_detection"
    if number <= 10:
        return "developing_after_detection"
    return "late_after_detection"


def _speed_regime_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_speed_regime"
    if number >= 1.0:
        return "fast_atr_move"
    if number >= 0.4:
        return "moderate_atr_move"
    return "slow_atr_move"


def _percentile_value_bin(value: object, *, missing: str) -> str:
    number = _optional_number(value)
    if number is None:
        return missing
    if number >= 0.9:
        return "top_decile"
    if number >= 0.75:
        return "upper_quartile"
    if number >= 0.25:
        return "middle_half"
    return "lower_quartile"


def _centered_percentile_value_bin(value: object, *, missing: str) -> str:
    number = _optional_number(value)
    if number is None:
        return missing
    if number >= 0.75:
        return "high_positive_rank"
    if number <= 0.25:
        return "high_negative_rank"
    return "middle_rank"


def _liquidation_regime_frame_bin(row: pd.Series) -> str:
    pct_bin = _percentile_value_bin(row["liq_intensity_market_percentile"], missing="missing_liq_rank")
    imbalance = _optional_number(row["liquidation_imbalance"])
    if imbalance is None:
        return pct_bin
    if imbalance >= 0.5:
        return f"{pct_bin}_short_liq_dominant"
    if imbalance <= -0.5:
        return f"{pct_bin}_long_liq_dominant"
    return f"{pct_bin}_balanced_liq"


def _cvd_divergence_frame_bin(row: pd.Series) -> str:
    if _bool_value(row["price_up_cvd_down_flag"]):
        return "price_up_cvd_down"
    if _bool_value(row["price_down_cvd_up_flag"]):
        return "price_down_cvd_up"
    if _bool_value(row["cvd_failed_to_confirm_high_flag"]):
        return "cvd_failed_to_confirm_high"
    value = _optional_number(row["cvd_price_divergence_5m"])
    if value is None:
        return "missing_cvd_divergence"
    if value >= 1.0:
        return "positive_price_minus_cvd_divergence"
    if value <= -1.0:
        return "negative_price_minus_cvd_divergence"
    return "cvd_confirmed_or_neutral"


def _btc_relative_frame_bin(row: pd.Series) -> str:
    score = _optional_number(row["idiosyncratic_momentum_score"])
    if score is not None and score >= 1.0:
        return "strong_idiosyncratic_momentum"
    diff = _optional_number(row["symbol_return_minus_btc_return_15m"])
    if diff is None:
        return "missing_btc_relative"
    if diff > 0:
        return "outperforming_btc"
    if diff < 0:
        return "underperforming_btc"
    return "btc_neutral"


def _market_context_frame_bin(row: pd.Series) -> str:
    if row["systemic_cluster_regime"] == "systemic_beta_shock":
        return "systemic_beta_shock"
    btc_regime = row["ctx_btc_relative_regime"]
    if btc_regime == "strong_idiosyncratic_momentum":
        return "idiosyncratic_momentum"
    if btc_regime == "missing_btc_relative":
        return "missing_market_context"
    return str(btc_regime)


def _initial_pump_height_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_initial_pump_height_atr"
    if number >= 5.0:
        return "extreme_initial_pump_atr"
    if number >= 3.0:
        return "large_initial_pump_atr"
    if number >= 1.5:
        return "moderate_initial_pump_atr"
    return "muted_initial_pump_atr"


def _consolidation_width_ratio_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_consolidation_width_ratio"
    if number <= 0.2:
        return "tight_consolidation"
    if number <= 0.5:
        return "moderate_consolidation"
    if number <= 1.0:
        return "wide_consolidation"
    return "loose_or_noisy_consolidation"


def _shelf_position_value_bin(value: object) -> str:
    return _signed_value_atr_bin(
        value,
        missing="missing_shelf_position_atr",
        negative_prefix="below_shelf_low",
        positive_prefix="above_shelf_low",
    )


def _shelf_break_risk_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "missing_shelf_break_risk"
    if number < -0.25:
        return "confirmed_shelf_break_asof"
    if number < 0.0:
        return "shallow_shelf_sweep_asof"
    if number <= 0.25:
        return "testing_shelf_low_asof"
    return "clear_above_shelf_low_asof"


def _shelf_reclaim_state_value_bin(value: object) -> str:
    number = _optional_number(value)
    if number is None:
        return "no_reclaim_observed_asof"
    if number <= 2:
        return "fresh_reclaim_asof"
    if number <= 10:
        return "recent_reclaim_asof"
    return "stale_reclaim_asof"


def _sweep_flow_frame_bin(row: pd.Series) -> str:
    pct_bin = _percentile_value_bin(row["volume_on_sweep_percentile"], missing="missing_sweep_volume_rank")
    liq = _optional_number(row["liq_intensity_during_sweep"])
    if liq is None:
        return pct_bin
    if liq >= 1.0:
        return f"{pct_bin}_high_liq_sweep"
    if liq > 0.0:
        return f"{pct_bin}_some_liq_sweep"
    return f"{pct_bin}_no_liq_sweep"


def _signed_value_atr_bin(value: object, *, missing: str, negative_prefix: str, positive_prefix: str) -> str:
    number = _optional_number(value)
    if number is None:
        return missing
    if number < -0.5:
        return f"{negative_prefix}_deep"
    if number < 0.0:
        return f"{negative_prefix}_shallow"
    if number <= 0.25:
        return f"{positive_prefix}_near"
    if number <= 1.0:
        return f"{positive_prefix}_moderate"
    return f"{positive_prefix}_far"


def _optional_number(value: object) -> float | None:
    if pd.isna(value):
        return None
    number = float(value)
    if not math.isfinite(number):
        return None
    return number


def load_atlas_inputs(
    *,
    state_path: str | Path,
    future_path: str | Path,
    feature_matrix_path: str | Path,
    config: AtlasConfig | None = None,
) -> tuple[AtlasInputRow, ...]:
    """Load state, future and feature matrix artifacts through strict boundaries."""
    cfg = config or AtlasConfig()
    state_rows = load_strategy_state_1m_csv(state_path)
    future_rows = load_strategy_future_paths_csv(future_path)
    feature_rows = load_strategy_feature_matrix_csv(feature_matrix_path)
    return build_atlas_inputs(state_rows=state_rows, future_rows=future_rows, feature_rows=feature_rows, config=cfg)


def build_atlas_inputs(
    *,
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    feature_rows: Sequence[StrategyFeatureMatrixRow] | Iterable[StrategyFeatureMatrixRow],
    config: AtlasConfig | None = None,
) -> tuple[AtlasInputRow, ...]:
    cfg = config or AtlasConfig()
    states = tuple(state_rows)
    futures = tuple(future_rows)
    features = tuple(feature_rows)
    state_by_key = _unique_by_join_key(states, artifact_name="anomaly_state_1m.csv")
    future_by_key = _unique_by_join_key(futures, artifact_name="anomaly_future_paths.csv")
    feature_by_key = _unique_by_join_key(features, artifact_name="anomaly_feature_matrix.csv")

    state_keys = set(state_by_key)
    future_keys = set(future_by_key)
    if state_keys != future_keys:
        missing_future = sorted(state_keys - future_keys)[:5]
        orphan_future = sorted(future_keys - state_keys)[:5]
        raise AtlasInputError(
            "state/future atlas join must be one-to-one on "
            "event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms; "
            f"missing_future={missing_future}, orphan_future={orphan_future}"
        )
    if state_keys != set(feature_by_key):
        missing_feature = sorted(state_keys - set(feature_by_key))[:5]
        orphan_feature = sorted(set(feature_by_key) - state_keys)[:5]
        raise AtlasInputError(
            "state/feature atlas join must be one-to-one on "
            "event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms; "
            f"missing_feature={missing_feature}, orphan_feature={orphan_feature}"
        )

    rows: list[AtlasInputRow] = []
    for state in states:
        key = _join_key(state)
        future = future_by_key[key]
        feature = feature_by_key[key]
        if not isinstance(state, StrategyState1mRow) or not isinstance(future, FuturePathRow):
            raise AtlasInputError("atlas join loaded unexpected row types")
        if not isinstance(feature, StrategyFeatureMatrixRow):
            raise AtlasInputError("feature matrix join loaded unexpected row type")
        _enforce_atlas_temporal_contract(state=state, future=future, feature=feature)
        rows.append(
            AtlasInputRow(
                state=state,
                future=future,
                feature=feature,
                contexts=assign_atlas_contexts(state, feature=feature),
                atlas_outcome_bins=tuple(
                    (horizon, assign_atlas_outcome_bin(future, horizon_minutes=horizon, config=cfg))
                    for horizon in cfg.outcome_horizon_minutes_list
                ),
            )
        )
    return tuple(rows)


def build_atlas_artifacts(
    *,
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    feature_rows: Sequence[StrategyFeatureMatrixRow] | Iterable[StrategyFeatureMatrixRow],
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    cfg = config or AtlasConfig()
    inputs = build_atlas_inputs(state_rows=state_rows, future_rows=future_rows, feature_rows=feature_rows, config=cfg)
    return build_atlas_artifacts_from_inputs(inputs=inputs, config=cfg)


def build_atlas_artifacts_from_inputs(
    *,
    inputs: Sequence[AtlasInputRow] | Iterable[AtlasInputRow],
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    cfg = config or AtlasConfig()
    rows = tuple(inputs)
    return AtlasArtifacts(
        nature_atlas_rows=_build_nature_atlas(rows=rows, config=cfg),
        context_split_rows=_build_context_splits(rows=rows, config=cfg),
        response_surface_rows=_build_response_surfaces(rows=rows, config=cfg),
        market_shock_group_rows=_build_market_shock_groups(rows=rows, config=cfg),
    )


def assign_atlas_contexts(
    state: StrategyState1mRow,
    *,
    feature: StrategyFeatureMatrixRow,
) -> tuple[tuple[str, str], ...]:
    """Assign atlas context bins from state/feature as-of fields only."""
    base_contexts: list[tuple[str, str]] = [
        ("price_shape_atr", _price_shape_atr_bin(state, feature)),
        ("high_position_atr", _high_position_atr_bin(state, feature)),
        ("detection_maturity", _detection_maturity_bin(state)),
        ("state_liveness", "alive" if state.event_alive else "not_alive"),
    ]
    base_contexts.extend(
        [
            ("alpha_decay_bucket", feature.alpha_decay_bucket),
            ("speed_regime", _speed_regime_bin(feature)),
            ("session", _utc_session_bin(state.snapshot_time_ms)),
            ("market_context", _market_context_bin(feature)),
            ("volume_regime_relative", _percentile_bin(feature.quote_volume_market_percentile, missing="missing_volume_rank")),
            ("oi_regime_relative", _centered_percentile_bin(feature.oi_growth_market_percentile, missing="missing_oi_rank")),
            ("liquidation_regime_relative", _liquidation_regime_bin(feature)),
            ("cvd_divergence_regime", _cvd_divergence_regime_bin(feature)),
            ("cross_sectional_rank_regime", _percentile_bin(feature.return_from_event_market_percentile, missing="missing_return_rank")),
            ("btc_relative_regime", _btc_relative_regime_bin(feature)),
            ("systemic_cluster_regime", feature.systemic_cluster_regime),
            ("initial_pump_height_atr", _initial_pump_height_atr_bin(feature)),
            ("consolidation_width_ratio", _consolidation_width_ratio_bin(feature)),
            ("shelf_position_atr", _shelf_position_atr_bin(feature)),
            ("shelf_break_risk", _shelf_break_risk_bin(feature)),
            ("shelf_reclaim_state", _shelf_reclaim_state_bin(feature)),
            ("sweep_flow_regime", _sweep_flow_regime_bin(feature)),
            ("feature_matrix", "feature_matrix_joined"),
        ]
    )
    return tuple(base_contexts)


def assign_atlas_outcome_bin(
    future: FuturePathRow,
    *,
    horizon_minutes: int = 30,
    config: AtlasConfig | None = None,
) -> str:
    """Assign a coarse descriptive ATR-normalized outcome bin for atlas summaries only.

    This is not a trading label and must not be consumed as decision logic.
    """
    cfg = config or AtlasConfig(outcome_horizon_minutes=horizon_minutes)
    future_return = _future_value(future, "future_return_atr", horizon_minutes)
    future_max = _future_value(future, "future_max_atr", horizon_minutes)
    future_min = _future_value(future, "future_min_atr", horizon_minutes)
    suffix = f"{horizon_minutes}m"
    if future_return is None or future_max is None or future_min is None:
        return f"missing_atr_future_{suffix}"
    if _barrier_resolution(future, horizon_minutes) == BARRIER_RESOLUTION_STOP_LOSS_FIRST:
        return f"stop_first_double_barrier_{suffix}"

    upside = future_max >= cfg.outcome_continuation_threshold_atr
    downside = future_min <= -cfg.outcome_fade_threshold_atr
    if upside and downside:
        return f"two_sided_atr_{suffix}"
    if upside:
        return f"upside_continuation_atr_{suffix}"
    if downside:
        return f"downside_extension_atr_{suffix}"
    if (
        abs(future_return) <= cfg.outcome_chop_threshold_atr
        and future_max < cfg.outcome_continuation_threshold_atr
        and future_min > -cfg.outcome_fade_threshold_atr
    ):
        return f"range_chop_atr_{suffix}"
    return f"mixed_drift_atr_{suffix}"


def nature_rows_to_artifact(rows: Sequence[AtlasNatureRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def context_split_rows_to_artifact(rows: Sequence[AtlasContextSplitRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def response_surface_rows_to_artifact(rows: Sequence[AtlasResponseSurfaceRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def market_shock_group_rows_to_artifact(rows: Sequence[AtlasMarketShockGroupRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def _build_nature_atlas(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasNatureRow, ...]:
    groups: dict[tuple[str, str, int, str], _NatureAccumulator] = {}
    for row in rows:
        for horizon, outcome_bin in row.atlas_outcome_bins:
            for split_family, split_value in row.contexts:
                key = (split_family, split_value, horizon, outcome_bin)
                groups.setdefault(key, _NatureAccumulator()).add(row=row, horizon=horizon)

    result: list[AtlasNatureRow] = []
    for (split_family, split_value, horizon, outcome_bin), stats in sorted(groups.items()):
        result.append(
            AtlasNatureRow(
                atlas_version=config.atlas_version,
                split_family=split_family,
                split_value=split_value,
                outcome_horizon_minutes=horizon,
                atlas_outcome_bin=outcome_bin,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=stats.row_count,
                unique_event_count=len(stats.event_ids),
                unique_symbol_count=len(stats.symbols),
                mean_future_return_atr=stats.future_return_atr.mean(),
                median_future_return_atr=stats.future_return_atr.median(),
                mean_future_max_atr=stats.future_max_atr.mean(),
                mean_future_min_atr=stats.future_min_atr.mean(),
                mean_future_return=stats.future_return.mean(),
                median_future_return=stats.future_return.median(),
                mean_future_max=stats.future_max.mean(),
                mean_future_min=stats.future_min.mean(),
                reclaim_rate=stats.reclaimed_running_high.rate(),
                double_barrier_stop_first_rate=stats.stop_first.rate(),
                feature_min_snapshot_time_ms=stats.min_snapshot_time_ms,
                feature_max_snapshot_time_ms=stats.max_snapshot_time_ms,
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


def _build_context_splits(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasContextSplitRow, ...]:
    groups: dict[tuple[str, str, int], _ContextAccumulator] = {}
    for row in rows:
        for horizon, outcome_bin in row.atlas_outcome_bins:
            for context_name, context_value in row.contexts:
                key = (context_name, context_value, horizon)
                groups.setdefault(key, _ContextAccumulator()).add(row=row, outcome_bin=outcome_bin)

    result: list[AtlasContextSplitRow] = []
    for (context_name, context_value, horizon), stats in sorted(groups.items()):
        result.append(
            AtlasContextSplitRow(
                atlas_version=config.atlas_version,
                context_name=context_name,
                context_value=context_value,
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=stats.row_count,
                unique_event_count=len(stats.event_ids),
                unique_symbol_count=len(stats.symbols),
                mean_minutes_since_trigger=stats.minutes_since_trigger.mean(),
                mean_current_return_from_start=stats.current_return_from_start.mean(),
                mean_distance_to_running_high_atr=stats.distance_to_running_high_atr.mean(),
                upside_continuation_share=stats.outcome_share(_outcome_bin_name("upside_continuation", horizon)),
                downside_extension_share=stats.outcome_share(_outcome_bin_name("downside_extension", horizon)),
                two_sided_share=stats.outcome_share(_outcome_bin_name("two_sided", horizon)),
                range_chop_share=stats.outcome_share(_outcome_bin_name("range_chop", horizon)),
                mixed_drift_share=stats.outcome_share(_outcome_bin_name("mixed_drift", horizon)),
                missing_atr_future_share=stats.outcome_share(_outcome_bin_name("missing_atr_future", horizon)),
                stop_first_barrier_share=stats.outcome_share(_outcome_bin_name("stop_first_double_barrier", horizon)),
            )
        )
    return tuple(result)


def _build_response_surfaces(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasResponseSurfaceRow, ...]:
    surfaces = (
        ("price_shape_atr_x_alpha_decay", "price_shape_atr", "alpha_decay_bucket"),
        ("liquidation_x_cvd_divergence", "liquidation_regime_relative", "cvd_divergence_regime"),
        ("cross_section_x_systemic_cluster", "cross_sectional_rank_regime", "systemic_cluster_regime"),
        ("btc_relative_x_volume_rank", "btc_relative_regime", "volume_regime_relative"),
        ("session_x_market_context", "session", "market_context"),
        ("speed_x_alpha_decay", "speed_regime", "alpha_decay_bucket"),
        ("initial_pump_x_consolidation", "initial_pump_height_atr", "consolidation_width_ratio"),
        ("shelf_position_x_alpha_decay", "shelf_position_atr", "alpha_decay_bucket"),
        ("shelf_break_x_systemic_cluster", "shelf_break_risk", "systemic_cluster_regime"),
        ("sweep_flow_x_liquidation", "sweep_flow_regime", "liquidation_regime_relative"),
    )
    result: list[AtlasResponseSurfaceRow] = []
    surface_pairs = tuple((surface_name, x_axis, y_axis) for surface_name, x_axis, y_axis in surfaces)
    groups: dict[tuple[str, str, str, str, str, int], _ResponseSurfaceAccumulator] = {}
    surface_axes = {surface_name: (x_axis, y_axis) for surface_name, x_axis, y_axis in surface_pairs}
    for row in rows:
        contexts = dict(row.contexts)
        for surface_name, x_axis, y_axis in surface_pairs:
            if x_axis not in contexts or y_axis not in contexts:
                continue
            for horizon, outcome_bin in row.atlas_outcome_bins:
                key = (surface_name, x_axis, contexts[x_axis], y_axis, contexts[y_axis], horizon)
                groups.setdefault(key, _ResponseSurfaceAccumulator()).add(
                    row=row,
                    horizon=horizon,
                    outcome_bin=outcome_bin,
                )
    for surface_name, x_axis, y_axis in surfaces:
        for (group_surface_name, group_x_axis, x_bin, group_y_axis, y_bin, horizon), stats in sorted(groups.items()):
            if group_surface_name != surface_name:
                continue
            expected_x_axis, expected_y_axis = surface_axes[group_surface_name]
            result.append(
                AtlasResponseSurfaceRow(
                    atlas_version=config.atlas_version,
                    surface_name=group_surface_name,
                    x_axis=expected_x_axis,
                    x_bin=x_bin,
                    y_axis=expected_y_axis,
                    y_bin=y_bin,
                    outcome_horizon_minutes=horizon,
                    outcome_coordinate=_outcome_coordinate(horizon),
                    row_count=stats.row_count,
                    unique_event_count=len(stats.event_ids),
                    mean_future_return_atr=stats.future_return_atr.mean(),
                    mean_future_max_atr=stats.future_max_atr.mean(),
                    mean_future_min_atr=stats.future_min_atr.mean(),
                    reclaim_rate=stats.reclaimed_running_high.rate(),
                    double_barrier_stop_first_rate=stats.stop_first.rate(),
                    dominant_outcome_bin=stats.dominant_outcome_bin(),
                )
            )
    return tuple(result)


def _build_market_shock_groups(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasMarketShockGroupRow, ...]:
    groups: dict[tuple[str, str, int, int], _MarketShockAccumulator] = {}
    for row in rows:
        for horizon, outcome_bin in row.atlas_outcome_bins:
            key = (_market_shock_id(row), _systemic_cluster_regime(row), row.state.snapshot_time_ms, horizon)
            groups.setdefault(key, _MarketShockAccumulator()).add(
                row=row,
                horizon=horizon,
                outcome_bin=outcome_bin,
            )

    result: list[AtlasMarketShockGroupRow] = []
    for (market_shock_id, systemic_cluster_regime, snapshot_time_ms, horizon), stats in sorted(groups.items()):
        symbols = sorted(stats.symbols)
        result.append(
            AtlasMarketShockGroupRow(
                atlas_version=config.atlas_version,
                market_shock_group_id=f"{market_shock_id}:{snapshot_time_ms}:{horizon}m",
                market_shock_id=market_shock_id,
                systemic_cluster_regime=systemic_cluster_regime,
                snapshot_time_ms=snapshot_time_ms,
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=stats.row_count,
                unique_event_count=len(stats.event_ids),
                unique_symbol_count=len(symbols),
                simultaneous_anomalies_count_1m=stats.max_simultaneous_count,
                simultaneous_anomalies_share_1m=stats.simultaneous_share.mean(),
                symbols="|".join(symbols),
                market_shock_candidate=systemic_cluster_regime == "systemic_beta_shock"
                or len(symbols) >= config.min_symbols_for_market_shock_candidate,
                mean_current_return_from_start=stats.current_return_from_start.mean(),
                mean_future_return_atr=stats.future_return_atr.mean(),
                dominant_outcome_bin=stats.dominant_outcome_bin(),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


@dataclass(slots=True)
class _MeanAccumulator:
    total: float = 0.0
    count: int = 0
    values: list[float] | None = None

    def add(self, value: float | int | None) -> None:
        if value is None:
            return
        item = float(value)
        if not math.isfinite(item):
            return
        self.total += item
        self.count += 1
        if self.values is not None:
            self.values.append(item)

    def mean(self) -> float | None:
        if self.count == 0:
            return None
        return self.total / self.count

    def median(self) -> float | None:
        if not self.values:
            return None
        return float(median(self.values))


@dataclass(slots=True)
class _BoolRateAccumulator:
    true_count: int = 0
    observed_count: int = 0

    def add(self, value: bool | None) -> None:
        if value is None:
            return
        self.observed_count += 1
        if value:
            self.true_count += 1

    def rate(self) -> float | None:
        if self.observed_count == 0:
            return None
        return self.true_count / self.observed_count


@dataclass(slots=True)
class _NatureAccumulator:
    row_count: int = 0
    event_ids: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    future_return_atr: _MeanAccumulator = field(default_factory=lambda: _MeanAccumulator(values=[]))
    future_max_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_min_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_return: _MeanAccumulator = field(default_factory=lambda: _MeanAccumulator(values=[]))
    future_max: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_min: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    reclaimed_running_high: _BoolRateAccumulator = field(default_factory=_BoolRateAccumulator)
    stop_first: _BoolRateAccumulator = field(default_factory=_BoolRateAccumulator)
    min_snapshot_time_ms: int = 0
    max_snapshot_time_ms: int = 0

    def add(self, *, row: AtlasInputRow, horizon: int) -> None:
        self.row_count += 1
        self.event_ids.add(row.state.event_id)
        self.symbols.add(row.state.symbol)
        if self.row_count == 1:
            self.min_snapshot_time_ms = row.state.snapshot_time_ms
            self.max_snapshot_time_ms = row.state.snapshot_time_ms
        else:
            self.min_snapshot_time_ms = min(self.min_snapshot_time_ms, row.state.snapshot_time_ms)
            self.max_snapshot_time_ms = max(self.max_snapshot_time_ms, row.state.snapshot_time_ms)
        self.future_return_atr.add(_future_return_atr(row, horizon))
        self.future_max_atr.add(_future_max_atr(row, horizon))
        self.future_min_atr.add(_future_min_atr(row, horizon))
        self.future_return.add(_future_return(row, horizon))
        self.future_max.add(_future_max(row, horizon))
        self.future_min.add(_future_min(row, horizon))
        self.reclaimed_running_high.add(_reclaimed_running_high(row, horizon))
        self.stop_first.add(_stop_first(row, horizon))


@dataclass(slots=True)
class _ContextAccumulator:
    row_count: int = 0
    event_ids: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    minutes_since_trigger: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    current_return_from_start: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    distance_to_running_high_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    outcome_counts: Counter[str] = field(default_factory=Counter)

    def add(self, *, row: AtlasInputRow, outcome_bin: str) -> None:
        self.row_count += 1
        self.event_ids.add(row.state.event_id)
        self.symbols.add(row.state.symbol)
        self.minutes_since_trigger.add(_minutes_since_trigger(row))
        self.current_return_from_start.add(row.state.current_return_from_start)
        self.distance_to_running_high_atr.add(_distance_to_running_high_atr(row))
        self.outcome_counts[outcome_bin] += 1

    def outcome_share(self, outcome_bin: str) -> float:
        if self.row_count == 0:
            return 0.0
        return self.outcome_counts[outcome_bin] / self.row_count


@dataclass(slots=True)
class _ResponseSurfaceAccumulator:
    row_count: int = 0
    event_ids: set[str] = field(default_factory=set)
    future_return_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_max_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_min_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    reclaimed_running_high: _BoolRateAccumulator = field(default_factory=_BoolRateAccumulator)
    stop_first: _BoolRateAccumulator = field(default_factory=_BoolRateAccumulator)
    outcome_counts: Counter[str] = field(default_factory=Counter)

    def add(self, *, row: AtlasInputRow, horizon: int, outcome_bin: str) -> None:
        self.row_count += 1
        self.event_ids.add(row.state.event_id)
        self.future_return_atr.add(_future_return_atr(row, horizon))
        self.future_max_atr.add(_future_max_atr(row, horizon))
        self.future_min_atr.add(_future_min_atr(row, horizon))
        self.reclaimed_running_high.add(_reclaimed_running_high(row, horizon))
        self.stop_first.add(_stop_first(row, horizon))
        self.outcome_counts[outcome_bin] += 1

    def dominant_outcome_bin(self) -> str:
        if not self.outcome_counts:
            return "none"
        return self.outcome_counts.most_common(1)[0][0]


@dataclass(slots=True)
class _MarketShockAccumulator:
    row_count: int = 0
    event_ids: set[str] = field(default_factory=set)
    symbols: set[str] = field(default_factory=set)
    max_simultaneous_count: int = 0
    simultaneous_share: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    current_return_from_start: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    future_return_atr: _MeanAccumulator = field(default_factory=_MeanAccumulator)
    outcome_counts: Counter[str] = field(default_factory=Counter)

    def add(self, *, row: AtlasInputRow, horizon: int, outcome_bin: str) -> None:
        self.row_count += 1
        self.event_ids.add(row.state.event_id)
        self.symbols.add(row.state.symbol)
        self.max_simultaneous_count = max(self.max_simultaneous_count, _simultaneous_count(row))
        self.simultaneous_share.add(_simultaneous_share(row))
        self.current_return_from_start.add(row.state.current_return_from_start)
        self.future_return_atr.add(_future_return_atr(row, horizon))
        self.outcome_counts[outcome_bin] += 1

    def dominant_outcome_bin(self) -> str:
        if not self.outcome_counts:
            return "none"
        return self.outcome_counts.most_common(1)[0][0]


def _price_shape_atr_bin(state: StrategyState1mRow, feature: StrategyFeatureMatrixRow) -> str:
    value = feature.range_since_start_atr
    if value is None:
        return "missing_range_since_start_atr"
    if value >= 3.0:
        return "strong_range_expansion_atr"
    if value >= 1.5:
        return "moderate_range_expansion_atr"
    return "muted_range_expansion_atr"


def _high_position_atr_bin(state: StrategyState1mRow, feature: StrategyFeatureMatrixRow) -> str:
    value = feature.distance_to_running_high_atr
    if value is None:
        return "missing_distance_to_running_high_atr"
    if value <= 0.25:
        return "at_or_near_running_high_atr"
    if value <= 0.75:
        return "shallow_pullback_from_high_atr"
    if value <= 1.5:
        return "deep_pullback_from_high_atr"
    return "far_below_running_high_atr"


def _detection_maturity_bin(state: StrategyState1mRow) -> str:
    value = state.minutes_since_detection
    if value <= 2:
        return "early_after_detection"
    if value <= 10:
        return "developing_after_detection"
    return "late_after_detection"


def _speed_regime_bin(feature: StrategyFeatureMatrixRow) -> str:
    value = feature.price_speed_atr
    if value is None:
        return "missing_speed_regime"
    if value >= 1.0:
        return "fast_atr_move"
    if value >= 0.4:
        return "moderate_atr_move"
    return "slow_atr_move"


def _utc_session_bin(snapshot_time_ms: int) -> str:
    hour = (snapshot_time_ms // 3_600_000) % 24
    if 0 <= hour < 7:
        return "asia_utc"
    if 7 <= hour < 13:
        return "europe_utc"
    if 13 <= hour < 20:
        return "us_utc"
    return "late_us_utc"


def _market_context_bin(feature: StrategyFeatureMatrixRow) -> str:
    if feature.systemic_cluster_regime == "systemic_beta_shock":
        return "systemic_beta_shock"
    btc_regime = _btc_relative_regime_bin(feature)
    if btc_regime == "strong_idiosyncratic_momentum":
        return "idiosyncratic_momentum"
    if btc_regime == "missing_btc_relative":
        return "missing_market_context"
    return btc_regime


def _percentile_bin(value: float | None, *, missing: str) -> str:
    if value is None:
        return missing
    if value >= 0.9:
        return "top_decile"
    if value >= 0.75:
        return "upper_quartile"
    if value >= 0.25:
        return "middle_half"
    return "lower_quartile"


def _centered_percentile_bin(value: float | None, *, missing: str) -> str:
    if value is None:
        return missing
    if value >= 0.75:
        return "high_positive_rank"
    if value <= 0.25:
        return "high_negative_rank"
    return "middle_rank"


def _signed_atr_bin(value: float | None, *, missing: str, negative_prefix: str, positive_prefix: str) -> str:
    if value is None:
        return missing
    if value < -0.5:
        return f"{negative_prefix}_deep"
    if value < 0.0:
        return f"{negative_prefix}_shallow"
    if value <= 0.25:
        return f"{positive_prefix}_near"
    if value <= 1.0:
        return f"{positive_prefix}_moderate"
    return f"{positive_prefix}_far"


def _initial_pump_height_atr_bin(feature: StrategyFeatureMatrixRow) -> str:
    value = feature.initial_pump_height_core_atr_1440
    if value is None:
        return "missing_initial_pump_height_atr"
    if value >= 5.0:
        return "extreme_initial_pump_atr"
    if value >= 3.0:
        return "large_initial_pump_atr"
    if value >= 1.5:
        return "moderate_initial_pump_atr"
    return "muted_initial_pump_atr"


def _consolidation_width_ratio_bin(feature: StrategyFeatureMatrixRow) -> str:
    value = feature.consolidation_width_ratio
    if value is None:
        return "missing_consolidation_width_ratio"
    if value <= 0.2:
        return "tight_consolidation"
    if value <= 0.5:
        return "moderate_consolidation"
    if value <= 1.0:
        return "wide_consolidation"
    return "loose_or_noisy_consolidation"


def _shelf_position_atr_bin(feature: StrategyFeatureMatrixRow) -> str:
    return _signed_atr_bin(
        feature.current_close_minus_shelf_low_core_atr_1440,
        missing="missing_shelf_position_atr",
        negative_prefix="below_shelf_low",
        positive_prefix="above_shelf_low",
    )


def _shelf_break_risk_bin(feature: StrategyFeatureMatrixRow) -> str:
    value = feature.current_low_minus_shelf_low_core_atr_1440
    if value is None:
        return "missing_shelf_break_risk"
    if value < -0.25:
        return "confirmed_shelf_break_asof"
    if value < 0.0:
        return "shallow_shelf_sweep_asof"
    if value <= 0.25:
        return "testing_shelf_low_asof"
    return "clear_above_shelf_low_asof"


def _shelf_reclaim_state_bin(feature: StrategyFeatureMatrixRow) -> str:
    value = feature.minutes_since_reclaim
    if value is None:
        return "no_reclaim_observed_asof"
    if value <= 2:
        return "fresh_reclaim_asof"
    if value <= 10:
        return "recent_reclaim_asof"
    return "stale_reclaim_asof"


def _sweep_flow_regime_bin(feature: StrategyFeatureMatrixRow) -> str:
    pct_bin = _percentile_bin(feature.volume_on_sweep_percentile, missing="missing_sweep_volume_rank")
    liq = feature.liq_intensity_during_sweep
    if liq is None:
        return pct_bin
    if liq >= 1.0:
        return f"{pct_bin}_high_liq_sweep"
    if liq > 0.0:
        return f"{pct_bin}_some_liq_sweep"
    return f"{pct_bin}_no_liq_sweep"


def _liquidation_regime_bin(feature: StrategyFeatureMatrixRow) -> str:
    pct_bin = _percentile_bin(feature.liq_intensity_market_percentile, missing="missing_liq_rank")
    imbalance = feature.liquidation_imbalance
    if imbalance is None:
        return pct_bin
    if imbalance >= 0.5:
        return f"{pct_bin}_short_liq_dominant"
    if imbalance <= -0.5:
        return f"{pct_bin}_long_liq_dominant"
    return f"{pct_bin}_balanced_liq"


def _cvd_divergence_regime_bin(feature: StrategyFeatureMatrixRow) -> str:
    if feature.price_up_cvd_down_flag:
        return "price_up_cvd_down"
    if feature.price_down_cvd_up_flag:
        return "price_down_cvd_up"
    if feature.cvd_failed_to_confirm_high_flag:
        return "cvd_failed_to_confirm_high"
    value = feature.cvd_price_divergence_5m
    if value is None:
        return "missing_cvd_divergence"
    if value >= 1.0:
        return "positive_price_minus_cvd_divergence"
    if value <= -1.0:
        return "negative_price_minus_cvd_divergence"
    return "cvd_confirmed_or_neutral"


def _btc_relative_regime_bin(feature: StrategyFeatureMatrixRow) -> str:
    score = feature.idiosyncratic_momentum_score
    if score is not None and score >= 1.0:
        return "strong_idiosyncratic_momentum"
    diff = feature.symbol_return_minus_btc_return_15m
    if diff is None:
        return "missing_btc_relative"
    if diff > 0:
        return "outperforming_btc"
    if diff < 0:
        return "underperforming_btc"
    return "btc_neutral"


def _unique_by_join_key(
    rows: Iterable[StrategyState1mRow] | Iterable[FuturePathRow] | Iterable[StrategyFeatureMatrixRow],
    *,
    artifact_name: str,
) -> dict[tuple[str, str, int, int], object]:
    result: dict[tuple[str, str, int, int], object] = {}
    for row in rows:
        key = _join_key(row)
        if key in result:
            raise AtlasInputError(f"{artifact_name} duplicate atlas join key: {key}")
        result[key] = row
    return result


def _join_key(row: StrategyState1mRow | FuturePathRow | StrategyFeatureMatrixRow) -> tuple[str, str, int, int]:
    return (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)


def _enforce_atlas_temporal_contract(
    *,
    state: StrategyState1mRow,
    future: FuturePathRow,
    feature: StrategyFeatureMatrixRow,
) -> None:
    if state.snapshot_time_ms != future.snapshot_time_ms:
        raise AtlasInputError("state and future snapshot_time_ms must match")
    if state.feature_cutoff_time_ms != future.feature_cutoff_time_ms:
        raise AtlasInputError("state and future feature_cutoff_time_ms must match")
    if feature.snapshot_time_ms != state.snapshot_time_ms:
        raise AtlasInputError("feature and state snapshot_time_ms must match")
    if feature.feature_cutoff_time_ms != state.feature_cutoff_time_ms:
        raise AtlasInputError("feature and state feature_cutoff_time_ms must match")
    if feature.feature_cutoff_time_ms > feature.snapshot_time_ms:
        raise AtlasInputError("feature matrix violates as-of contract")
    if state.feature_cutoff_time_ms > state.snapshot_time_ms:
        raise AtlasInputError("state row violates as-of feature cutoff contract")
    if future.future_start_time_ms <= state.snapshot_time_ms:
        raise AtlasInputError("future row must start strictly after snapshot_time_ms")


def _future_return(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_return", horizon_minutes)


def _future_max(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_max", horizon_minutes)


def _future_min(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_min", horizon_minutes)


def _future_return_atr(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_return_atr", horizon_minutes)


def _future_max_atr(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_max_atr", horizon_minutes)


def _future_min_atr(row: AtlasInputRow, horizon_minutes: int) -> float | None:
    return _future_value(row.future, "future_min_atr", horizon_minutes)


def _future_value(future: FuturePathRow, field_prefix: str, horizon_minutes: int) -> float | None:
    return getattr(future, f"{field_prefix}_{horizon_minutes}m")


def _barrier_resolution(future: FuturePathRow, horizon_minutes: int) -> str | None:
    return getattr(future, f"barrier_resolution_{horizon_minutes}m")


def _stop_first(row: AtlasInputRow, horizon_minutes: int) -> bool | None:
    resolution = _barrier_resolution(row.future, horizon_minutes)
    if resolution is None:
        return None
    return resolution == BARRIER_RESOLUTION_STOP_LOSS_FIRST


def _reclaimed_running_high(row: AtlasInputRow, horizon_minutes: int) -> bool | None:
    if horizon_minutes <= 30:
        return row.future.reclaimed_running_high_30m
    return row.future.reclaimed_running_high_60m


def _minutes_since_trigger(row: AtlasInputRow) -> int:
    return row.feature.minutes_since_trigger


def _distance_to_running_high_atr(row: AtlasInputRow) -> float | None:
    return row.feature.distance_to_running_high_atr


def _market_shock_id(row: AtlasInputRow) -> str:
    return row.feature.market_shock_id


def _systemic_cluster_regime(row: AtlasInputRow) -> str:
    return row.feature.systemic_cluster_regime


def _simultaneous_count(row: AtlasInputRow) -> int:
    return row.feature.simultaneous_anomalies_count_1m


def _simultaneous_share(row: AtlasInputRow) -> float | None:
    return row.feature.simultaneous_anomalies_share_1m


def _outcome_coordinate(horizon_minutes: int) -> str:
    return f"ATR_normalized_{horizon_minutes}m"


def _mean(values: Iterable[float | int | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return None
    return sum(finite) / len(finite)


def _median(values: Iterable[float | int | None]) -> float | None:
    finite = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not finite:
        return None
    return float(median(finite))


def _bool_rate(values: Iterable[bool | None]) -> float | None:
    observed = [value for value in values if value is not None]
    if not observed:
        return None
    return sum(1 for value in observed if value) / len(observed)


def _outcome_share(rows: Sequence[AtlasInputRow], horizon_minutes: int, outcome_prefix: str) -> float:
    if not rows:
        return 0.0
    expected = _outcome_bin_name(outcome_prefix, horizon_minutes)
    return sum(1 for row in rows if _row_outcome_bin(row, horizon_minutes) == expected) / len(rows)


def _outcome_bin_name(outcome_prefix: str, horizon_minutes: int) -> str:
    suffix = f"{horizon_minutes}m"
    if outcome_prefix in {"missing_atr_future", "stop_first_double_barrier"}:
        return f"{outcome_prefix}_{suffix}"
    return f"{outcome_prefix}_atr_{suffix}"


def _dominant_outcome_bin(rows: Sequence[AtlasInputRow], horizon_minutes: int) -> str:
    if not rows:
        return "none"
    counts = Counter(_row_outcome_bin(row, horizon_minutes) for row in rows)
    return counts.most_common(1)[0][0]


def _row_outcome_bin(row: AtlasInputRow, horizon_minutes: int) -> str:
    for horizon, outcome_bin in row.atlas_outcome_bins:
        if horizon == horizon_minutes:
            return outcome_bin
    raise AtlasInputError(f"atlas input row is missing outcome bin for horizon={horizon_minutes}")


def _unique_count(values: Iterable[str]) -> int:
    return len(set(values))


def _row_to_csv_payload(row: object) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in asdict(row).items():
        if value is None:
            result[key] = ""
        else:
            result[key] = value
    return result
