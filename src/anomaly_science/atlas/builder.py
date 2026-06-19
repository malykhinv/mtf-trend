from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence

from anomaly_science.atlas.config import AtlasConfig
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
    for key in sorted(state_keys):
        state = state_by_key[key]
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
    groups: dict[tuple[str, str, int, str], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        for horizon, outcome_bin in row.atlas_outcome_bins:
            for split_family, split_value in row.contexts:
                groups[(split_family, split_value, horizon, outcome_bin)].append(row)

    result: list[AtlasNatureRow] = []
    for (split_family, split_value, horizon, outcome_bin), group_rows in sorted(groups.items()):
        result.append(
            AtlasNatureRow(
                atlas_version=config.atlas_version,
                split_family=split_family,
                split_value=split_value,
                outcome_horizon_minutes=horizon,
                atlas_outcome_bin=outcome_bin,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_future_return_atr=_mean(_future_return_atr(row, horizon) for row in group_rows),
                median_future_return_atr=_median(_future_return_atr(row, horizon) for row in group_rows),
                mean_future_max_atr=_mean(_future_max_atr(row, horizon) for row in group_rows),
                mean_future_min_atr=_mean(_future_min_atr(row, horizon) for row in group_rows),
                mean_future_return=_mean(_future_return(row, horizon) for row in group_rows),
                median_future_return=_median(_future_return(row, horizon) for row in group_rows),
                mean_future_max=_mean(_future_max(row, horizon) for row in group_rows),
                mean_future_min=_mean(_future_min(row, horizon) for row in group_rows),
                reclaim_rate=_bool_rate(_reclaimed_running_high(row, horizon) for row in group_rows),
                double_barrier_stop_first_rate=_bool_rate(_stop_first(row, horizon) for row in group_rows),
                feature_min_snapshot_time_ms=min(row.state.snapshot_time_ms for row in group_rows),
                feature_max_snapshot_time_ms=max(row.state.snapshot_time_ms for row in group_rows),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


def _build_context_splits(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasContextSplitRow, ...]:
    groups: dict[tuple[str, str, int], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        for horizon, _outcome_bin in row.atlas_outcome_bins:
            for context_name, context_value in row.contexts:
                groups[(context_name, context_value, horizon)].append(row)

    result: list[AtlasContextSplitRow] = []
    for (context_name, context_value, horizon), group_rows in sorted(groups.items()):
        result.append(
            AtlasContextSplitRow(
                atlas_version=config.atlas_version,
                context_name=context_name,
                context_value=context_value,
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_minutes_since_trigger=_mean(_minutes_since_trigger(row) for row in group_rows),
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_distance_to_running_high_atr=_mean(_distance_to_running_high_atr(row) for row in group_rows),
                upside_continuation_share=_outcome_share(group_rows, horizon, "upside_continuation"),
                downside_extension_share=_outcome_share(group_rows, horizon, "downside_extension"),
                two_sided_share=_outcome_share(group_rows, horizon, "two_sided"),
                range_chop_share=_outcome_share(group_rows, horizon, "range_chop"),
                mixed_drift_share=_outcome_share(group_rows, horizon, "mixed_drift"),
                missing_atr_future_share=_outcome_share(group_rows, horizon, "missing_atr_future"),
                stop_first_barrier_share=_outcome_share(group_rows, horizon, "stop_first_double_barrier"),
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
    for surface_name, x_axis, y_axis in surfaces:
        groups: dict[tuple[str, str, int], list[AtlasInputRow]] = defaultdict(list)
        for row in rows:
            contexts = dict(row.contexts)
            if x_axis in contexts and y_axis in contexts:
                for horizon, _outcome_bin in row.atlas_outcome_bins:
                    groups[(contexts[x_axis], contexts[y_axis], horizon)].append(row)
        for (x_bin, y_bin, horizon), group_rows in sorted(groups.items()):
            result.append(
                AtlasResponseSurfaceRow(
                    atlas_version=config.atlas_version,
                    surface_name=surface_name,
                    x_axis=x_axis,
                    x_bin=x_bin,
                    y_axis=y_axis,
                    y_bin=y_bin,
                    outcome_horizon_minutes=horizon,
                    outcome_coordinate=_outcome_coordinate(horizon),
                    row_count=len(group_rows),
                    unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                    mean_future_return_atr=_mean(_future_return_atr(row, horizon) for row in group_rows),
                    mean_future_max_atr=_mean(_future_max_atr(row, horizon) for row in group_rows),
                    mean_future_min_atr=_mean(_future_min_atr(row, horizon) for row in group_rows),
                    reclaim_rate=_bool_rate(_reclaimed_running_high(row, horizon) for row in group_rows),
                    double_barrier_stop_first_rate=_bool_rate(_stop_first(row, horizon) for row in group_rows),
                    dominant_outcome_bin=_dominant_outcome_bin(group_rows, horizon),
                )
            )
    return tuple(result)


def _build_market_shock_groups(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasMarketShockGroupRow, ...]:
    groups: dict[tuple[str, str, int, int], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        for horizon, _outcome_bin in row.atlas_outcome_bins:
            groups[(_market_shock_id(row), _systemic_cluster_regime(row), row.state.snapshot_time_ms, horizon)].append(row)

    result: list[AtlasMarketShockGroupRow] = []
    for (market_shock_id, systemic_cluster_regime, snapshot_time_ms, horizon), group_rows in sorted(groups.items()):
        symbols = sorted({row.state.symbol for row in group_rows})
        result.append(
            AtlasMarketShockGroupRow(
                atlas_version=config.atlas_version,
                market_shock_group_id=f"{market_shock_id}:{snapshot_time_ms}:{horizon}m",
                market_shock_id=market_shock_id,
                systemic_cluster_regime=systemic_cluster_regime,
                snapshot_time_ms=snapshot_time_ms,
                outcome_horizon_minutes=horizon,
                outcome_coordinate=_outcome_coordinate(horizon),
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=len(symbols),
                simultaneous_anomalies_count_1m=max(_simultaneous_count(row) for row in group_rows),
                simultaneous_anomalies_share_1m=_mean(_simultaneous_share(row) for row in group_rows),
                symbols="|".join(symbols),
                market_shock_candidate=systemic_cluster_regime == "systemic_beta_shock"
                or len(symbols) >= config.min_symbols_for_market_shock_candidate,
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_future_return_atr=_mean(_future_return_atr(row, horizon) for row in group_rows),
                dominant_outcome_bin=_dominant_outcome_bin(group_rows, horizon),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


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
        key = (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)
        if key in result:
            raise AtlasInputError(f"{artifact_name} duplicate atlas join key: {key}")
        result[key] = row
    return result


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
