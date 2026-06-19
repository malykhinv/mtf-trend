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
from anomaly_science.contracts.features import AnomalyFeatureMatrixRow
from anomaly_science.contracts.future import BARRIER_RESOLUTION_STOP_LOSS_FIRST, FuturePathRow
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.features.matrix import load_anomaly_feature_matrix_csv
from anomaly_science.future.builder import (
    AnomalyFutureArtifactError,
    AnomalyStateArtifactError,
    load_anomaly_future_paths_csv,
    load_anomaly_state_1m_csv,
)

TEMPORAL_CONTRACT_TEXT = "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"
OUTCOME_COORDINATE_ATR = "ATR_normalized_30m"


class AtlasInputError(ValueError):
    """Raised when atlas input artifacts cannot be joined one-to-one."""


@dataclass(frozen=True, slots=True)
class AtlasInputRow:
    state: AnomalyState1mRow
    future: FuturePathRow
    feature: AnomalyFeatureMatrixRow
    contexts: tuple[tuple[str, str], ...]
    atlas_outcome_bin: str


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
    state_rows = load_anomaly_state_1m_csv(state_path)
    future_rows = load_anomaly_future_paths_csv(future_path)
    feature_rows = load_anomaly_feature_matrix_csv(feature_matrix_path)
    return build_atlas_inputs(state_rows=state_rows, future_rows=future_rows, feature_rows=feature_rows, config=cfg)


def build_atlas_inputs(
    *,
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    feature_rows: Sequence[AnomalyFeatureMatrixRow] | Iterable[AnomalyFeatureMatrixRow],
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
        if not isinstance(state, AnomalyState1mRow) or not isinstance(future, FuturePathRow):
            raise AtlasInputError("atlas join loaded unexpected row types")
        if not isinstance(feature, AnomalyFeatureMatrixRow):
            raise AtlasInputError("feature matrix join loaded unexpected row type")
        _enforce_atlas_temporal_contract(state=state, future=future, feature=feature)
        rows.append(
            AtlasInputRow(
                state=state,
                future=future,
                feature=feature,
                contexts=assign_atlas_contexts(state, feature=feature),
                atlas_outcome_bin=assign_atlas_outcome_bin(future, config=cfg),
            )
        )
    return tuple(rows)


def build_atlas_artifacts(
    *,
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    feature_rows: Sequence[AnomalyFeatureMatrixRow] | Iterable[AnomalyFeatureMatrixRow],
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
    state: AnomalyState1mRow,
    *,
    feature: AnomalyFeatureMatrixRow,
) -> tuple[tuple[str, str], ...]:
    """Assign atlas context bins from feature-matrix as-of fields."""
    base_contexts: list[tuple[str, str]] = [
        ("price_shape_atr", _price_shape_atr_bin(state, feature)),
        ("high_position_atr", _high_position_atr_bin(state, feature)),
        ("detection_maturity", _detection_maturity_bin(state)),
        ("state_liveness", "alive" if state.event_alive else "not_alive"),
    ]
    base_contexts.extend(
        [
            ("alpha_decay_bucket", feature.alpha_decay_bucket),
            ("volume_regime_relative", _percentile_bin(feature.quote_volume_market_percentile, missing="missing_volume_rank")),
            ("oi_regime_relative", _centered_percentile_bin(feature.oi_growth_market_percentile, missing="missing_oi_rank")),
            ("liquidation_regime_relative", _liquidation_regime_bin(feature)),
            ("cvd_divergence_regime", _cvd_divergence_regime_bin(feature)),
            ("cross_sectional_rank_regime", _percentile_bin(feature.return_from_event_market_percentile, missing="missing_return_rank")),
            ("btc_relative_regime", _btc_relative_regime_bin(feature)),
            ("systemic_cluster_regime", feature.systemic_cluster_regime),
            ("feature_matrix", "feature_matrix_joined"),
        ]
    )
    return tuple(base_contexts)


def assign_atlas_outcome_bin(future: FuturePathRow, *, config: AtlasConfig | None = None) -> str:
    """Assign a coarse descriptive 30m ATR-normalized outcome bin for atlas summaries only.

    This is not a trading label and must not be consumed as decision logic.
    """
    cfg = config or AtlasConfig()
    future_return = future.future_return_atr_30m
    future_max = future.future_max_atr_30m
    future_min = future.future_min_atr_30m
    if future_return is None or future_max is None or future_min is None:
        return "missing_atr_future_30m"
    if future.barrier_resolution_30m == BARRIER_RESOLUTION_STOP_LOSS_FIRST:
        return "stop_first_double_barrier_30m"

    upside = future_max >= cfg.outcome_continuation_threshold_atr
    downside = future_min <= -cfg.outcome_fade_threshold_atr
    if upside and downside:
        return "two_sided_atr_30m"
    if upside:
        return "upside_continuation_atr_30m"
    if downside:
        return "downside_extension_atr_30m"
    if (
        abs(future_return) <= cfg.outcome_chop_threshold_atr
        and future_max < cfg.outcome_continuation_threshold_atr
        and future_min > -cfg.outcome_fade_threshold_atr
    ):
        return "range_chop_atr_30m"
    return "mixed_drift_atr_30m"


def nature_rows_to_artifact(rows: Sequence[AtlasNatureRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def context_split_rows_to_artifact(rows: Sequence[AtlasContextSplitRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def response_surface_rows_to_artifact(rows: Sequence[AtlasResponseSurfaceRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def market_shock_group_rows_to_artifact(rows: Sequence[AtlasMarketShockGroupRow]) -> list[dict[str, object]]:
    return [_row_to_csv_payload(row) for row in rows]


def _build_nature_atlas(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasNatureRow, ...]:
    groups: dict[tuple[str, str, str], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        for split_family, split_value in row.contexts:
            groups[(split_family, split_value, row.atlas_outcome_bin)].append(row)

    result: list[AtlasNatureRow] = []
    for (split_family, split_value, outcome_bin), group_rows in sorted(groups.items()):
        result.append(
            AtlasNatureRow(
                atlas_version=config.atlas_version,
                split_family=split_family,
                split_value=split_value,
                outcome_horizon_minutes=config.outcome_horizon_minutes,
                atlas_outcome_bin=outcome_bin,
                outcome_coordinate=OUTCOME_COORDINATE_ATR,
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_future_return_atr=_mean(_future_return_atr_30m(row) for row in group_rows),
                median_future_return_atr=_median(_future_return_atr_30m(row) for row in group_rows),
                mean_future_max_atr=_mean(row.future.future_max_atr_30m for row in group_rows),
                mean_future_min_atr=_mean(row.future.future_min_atr_30m for row in group_rows),
                mean_future_return=_mean(_future_return_30m(row) for row in group_rows),
                median_future_return=_median(_future_return_30m(row) for row in group_rows),
                mean_future_max=_mean(row.future.future_max_30m for row in group_rows),
                mean_future_min=_mean(row.future.future_min_30m for row in group_rows),
                reclaim_rate=_bool_rate(row.future.reclaimed_running_high_30m for row in group_rows),
                double_barrier_stop_first_rate=_bool_rate(_stop_first_30m(row) for row in group_rows),
                feature_min_snapshot_time_ms=min(row.state.snapshot_time_ms for row in group_rows),
                feature_max_snapshot_time_ms=max(row.state.snapshot_time_ms for row in group_rows),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


def _build_context_splits(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasContextSplitRow, ...]:
    groups: dict[tuple[str, str], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        for context_name, context_value in row.contexts:
            groups[(context_name, context_value)].append(row)

    result: list[AtlasContextSplitRow] = []
    for (context_name, context_value), group_rows in sorted(groups.items()):
        result.append(
            AtlasContextSplitRow(
                atlas_version=config.atlas_version,
                context_name=context_name,
                context_value=context_value,
                outcome_coordinate=OUTCOME_COORDINATE_ATR,
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_minutes_since_trigger=_mean(_minutes_since_trigger(row) for row in group_rows),
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_distance_to_running_high_atr=_mean(_distance_to_running_high_atr(row) for row in group_rows),
                upside_continuation_share=_outcome_share(group_rows, "upside_continuation_atr_30m"),
                downside_extension_share=_outcome_share(group_rows, "downside_extension_atr_30m"),
                two_sided_share=_outcome_share(group_rows, "two_sided_atr_30m"),
                range_chop_share=_outcome_share(group_rows, "range_chop_atr_30m"),
                mixed_drift_share=_outcome_share(group_rows, "mixed_drift_atr_30m"),
                missing_atr_future_share=_outcome_share(group_rows, "missing_atr_future_30m"),
                stop_first_barrier_share=_outcome_share(group_rows, "stop_first_double_barrier_30m"),
            )
        )
    return tuple(result)


def _build_response_surfaces(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasResponseSurfaceRow, ...]:
    surfaces = (
        ("price_shape_atr_x_alpha_decay", "price_shape_atr", "alpha_decay_bucket"),
        ("liquidation_x_cvd_divergence", "liquidation_regime_relative", "cvd_divergence_regime"),
        ("cross_section_x_systemic_cluster", "cross_sectional_rank_regime", "systemic_cluster_regime"),
        ("btc_relative_x_volume_rank", "btc_relative_regime", "volume_regime_relative"),
    )
    result: list[AtlasResponseSurfaceRow] = []
    for surface_name, x_axis, y_axis in surfaces:
        groups: dict[tuple[str, str], list[AtlasInputRow]] = defaultdict(list)
        for row in rows:
            contexts = dict(row.contexts)
            if x_axis in contexts and y_axis in contexts:
                groups[(contexts[x_axis], contexts[y_axis])].append(row)
        for (x_bin, y_bin), group_rows in sorted(groups.items()):
            result.append(
                AtlasResponseSurfaceRow(
                    atlas_version=config.atlas_version,
                    surface_name=surface_name,
                    x_axis=x_axis,
                    x_bin=x_bin,
                    y_axis=y_axis,
                    y_bin=y_bin,
                    outcome_horizon_minutes=config.outcome_horizon_minutes,
                    outcome_coordinate=OUTCOME_COORDINATE_ATR,
                    row_count=len(group_rows),
                    unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                    mean_future_return_atr=_mean(_future_return_atr_30m(row) for row in group_rows),
                    mean_future_max_atr=_mean(row.future.future_max_atr_30m for row in group_rows),
                    mean_future_min_atr=_mean(row.future.future_min_atr_30m for row in group_rows),
                    reclaim_rate=_bool_rate(row.future.reclaimed_running_high_30m for row in group_rows),
                    double_barrier_stop_first_rate=_bool_rate(_stop_first_30m(row) for row in group_rows),
                    dominant_outcome_bin=_dominant_outcome_bin(group_rows),
                )
            )
    return tuple(result)


def _build_market_shock_groups(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasMarketShockGroupRow, ...]:
    groups: dict[tuple[str, str, int], list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        groups[(_market_shock_id(row), _systemic_cluster_regime(row), row.state.snapshot_time_ms)].append(row)

    result: list[AtlasMarketShockGroupRow] = []
    for (market_shock_id, systemic_cluster_regime, snapshot_time_ms), group_rows in sorted(groups.items()):
        symbols = sorted({row.state.symbol for row in group_rows})
        result.append(
            AtlasMarketShockGroupRow(
                atlas_version=config.atlas_version,
                market_shock_group_id=f"{market_shock_id}:{snapshot_time_ms}",
                market_shock_id=market_shock_id,
                systemic_cluster_regime=systemic_cluster_regime,
                snapshot_time_ms=snapshot_time_ms,
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=len(symbols),
                simultaneous_anomalies_count_1m=max(_simultaneous_count(row) for row in group_rows),
                simultaneous_anomalies_share_1m=_mean(_simultaneous_share(row) for row in group_rows),
                symbols="|".join(symbols),
                market_shock_candidate=systemic_cluster_regime == "systemic_beta_shock"
                or len(symbols) >= config.min_symbols_for_market_shock_candidate,
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_future_return_atr_30m=_mean(_future_return_atr_30m(row) for row in group_rows),
                dominant_outcome_bin=_dominant_outcome_bin(group_rows),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


def _price_shape_atr_bin(state: AnomalyState1mRow, feature: AnomalyFeatureMatrixRow) -> str:
    value = feature.range_since_start_atr
    if value is None:
        return "missing_range_since_start_atr"
    if value >= 3.0:
        return "strong_range_expansion_atr"
    if value >= 1.5:
        return "moderate_range_expansion_atr"
    return "muted_range_expansion_atr"


def _high_position_atr_bin(state: AnomalyState1mRow, feature: AnomalyFeatureMatrixRow) -> str:
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


def _detection_maturity_bin(state: AnomalyState1mRow) -> str:
    value = state.minutes_since_detection
    if value <= 2:
        return "early_after_detection"
    if value <= 10:
        return "developing_after_detection"
    return "late_after_detection"


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


def _liquidation_regime_bin(feature: AnomalyFeatureMatrixRow) -> str:
    pct_bin = _percentile_bin(feature.liq_intensity_market_percentile, missing="missing_liq_rank")
    imbalance = feature.liquidation_imbalance
    if imbalance is None:
        return pct_bin
    if imbalance >= 0.5:
        return f"{pct_bin}_short_liq_dominant"
    if imbalance <= -0.5:
        return f"{pct_bin}_long_liq_dominant"
    return f"{pct_bin}_balanced_liq"


def _cvd_divergence_regime_bin(feature: AnomalyFeatureMatrixRow) -> str:
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


def _btc_relative_regime_bin(feature: AnomalyFeatureMatrixRow) -> str:
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
    rows: Iterable[AnomalyState1mRow] | Iterable[FuturePathRow] | Iterable[AnomalyFeatureMatrixRow],
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
    state: AnomalyState1mRow,
    future: FuturePathRow,
    feature: AnomalyFeatureMatrixRow,
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


def _future_return_30m(row: AtlasInputRow) -> float | None:
    return row.future.future_return_30m


def _future_return_atr_30m(row: AtlasInputRow) -> float | None:
    return row.future.future_return_atr_30m


def _stop_first_30m(row: AtlasInputRow) -> bool | None:
    resolution = row.future.barrier_resolution_30m
    if resolution is None:
        return None
    return resolution == BARRIER_RESOLUTION_STOP_LOSS_FIRST


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


def _outcome_share(rows: Sequence[AtlasInputRow], outcome_bin: str) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if row.atlas_outcome_bin == outcome_bin) / len(rows)


def _dominant_outcome_bin(rows: Sequence[AtlasInputRow]) -> str:
    if not rows:
        return "none"
    counts = Counter(row.atlas_outcome_bin for row in rows)
    return counts.most_common(1)[0][0]


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
