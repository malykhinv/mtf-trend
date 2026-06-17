from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Iterable, Mapping, Sequence

import pandas as pd

from anomaly_science.atlas.config import AtlasConfig
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.atlas import (
    AtlasContextSplitRow,
    AtlasMarketShockGroupRow,
    AtlasNatureRow,
    AtlasResponseSurfaceRow,
)
from anomaly_science.contracts.future import FuturePathRow
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.future.builder import AnomalyStateArtifactError, load_anomaly_state_1m_csv

TEMPORAL_CONTRACT_TEXT = "feature_cutoff_time_ms<=snapshot_time_ms<future_start_time_ms"


class AnomalyFutureArtifactError(ValueError):
    """Raised when anomaly_future_paths.csv violates its strict artifact boundary."""


class AtlasInputError(ValueError):
    """Raised when atlas input artifacts cannot be joined one-to-one."""


@dataclass(frozen=True, slots=True)
class AtlasInputRow:
    state: AnomalyState1mRow
    future: FuturePathRow
    contexts: tuple[tuple[str, str], ...]
    atlas_outcome_bin: str


@dataclass(frozen=True, slots=True)
class AtlasArtifacts:
    nature_atlas_rows: tuple[AtlasNatureRow, ...]
    context_split_rows: tuple[AtlasContextSplitRow, ...]
    response_surface_rows: tuple[AtlasResponseSurfaceRow, ...]
    market_shock_group_rows: tuple[AtlasMarketShockGroupRow, ...]


def load_anomaly_future_paths_csv(path: str | Path) -> tuple[FuturePathRow, ...]:
    """Read anomaly_future_paths.csv through the declared strict artifact schema."""
    future_path = Path(path)
    if not future_path.exists():
        raise AnomalyFutureArtifactError(f"future artifact is missing: {future_path}")

    frame = pd.read_csv(future_path)
    schema = get_artifact_schema("anomaly_future_paths.csv")
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise AnomalyFutureArtifactError(
            f"future artifact columns must match {expected_columns}, got {actual_columns}"
        )

    rows: list[FuturePathRow] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(
                FuturePathRow(
                    event_id=_required_str(row, "event_id"),
                    symbol=_required_str(row, "symbol"),
                    snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
                    feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
                    future_start_time_ms=_required_int(row, "future_start_time_ms"),
                    future_return_5m=_optional_float(row, "future_return_5m"),
                    future_return_15m=_optional_float(row, "future_return_15m"),
                    future_return_30m=_optional_float(row, "future_return_30m"),
                    future_return_60m=_optional_float(row, "future_return_60m"),
                    future_max_5m=_optional_float(row, "future_max_5m"),
                    future_max_15m=_optional_float(row, "future_max_15m"),
                    future_max_30m=_optional_float(row, "future_max_30m"),
                    future_max_60m=_optional_float(row, "future_max_60m"),
                    future_min_5m=_optional_float(row, "future_min_5m"),
                    future_min_15m=_optional_float(row, "future_min_15m"),
                    future_min_30m=_optional_float(row, "future_min_30m"),
                    future_min_60m=_optional_float(row, "future_min_60m"),
                    reclaimed_running_high_30m=_optional_bool(row, "reclaimed_running_high_30m"),
                    reclaimed_running_high_60m=_optional_bool(row, "reclaimed_running_high_60m"),
                    broke_structural_low_30m=_optional_bool(row, "broke_structural_low_30m"),
                    broke_structural_low_60m=_optional_bool(row, "broke_structural_low_60m"),
                    time_to_new_high_minutes=_optional_int(row, "time_to_new_high_minutes"),
                    time_to_structural_break_minutes=_optional_int(row, "time_to_structural_break_minutes"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise AnomalyFutureArtifactError(f"invalid anomaly_future_paths.csv row {row_index}: {exc}") from exc
    return tuple(rows)


def load_atlas_inputs(
    *,
    state_path: str | Path,
    future_path: str | Path,
    config: AtlasConfig | None = None,
) -> tuple[AtlasInputRow, ...]:
    """Load state and future artifacts and join them through an auditable temporal key."""
    cfg = config or AtlasConfig()
    state_rows = load_anomaly_state_1m_csv(state_path)
    future_rows = load_anomaly_future_paths_csv(future_path)
    return build_atlas_inputs(state_rows=state_rows, future_rows=future_rows, config=cfg)


def build_atlas_inputs(
    *,
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    config: AtlasConfig | None = None,
) -> tuple[AtlasInputRow, ...]:
    cfg = config or AtlasConfig()
    states = tuple(state_rows)
    futures = tuple(future_rows)
    state_by_key = _unique_by_join_key(states, artifact_name="anomaly_state_1m.csv")
    future_by_key = _unique_by_join_key(futures, artifact_name="anomaly_future_paths.csv")

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

    rows: list[AtlasInputRow] = []
    for key in sorted(state_keys):
        state = state_by_key[key]
        future = future_by_key[key]
        _enforce_atlas_temporal_contract(state=state, future=future)
        rows.append(
            AtlasInputRow(
                state=state,
                future=future,
                contexts=assign_atlas_contexts(state),
                atlas_outcome_bin=assign_atlas_outcome_bin(future, config=cfg),
            )
        )
    return tuple(rows)


def build_atlas_artifacts(
    *,
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    config: AtlasConfig | None = None,
) -> AtlasArtifacts:
    cfg = config or AtlasConfig()
    inputs = build_atlas_inputs(state_rows=state_rows, future_rows=future_rows, config=cfg)
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


def assign_atlas_contexts(state: AnomalyState1mRow) -> tuple[tuple[str, str], ...]:
    """Assign descriptive atlas context bins from state-only, as-of fields."""
    return (
        ("price_shape", _price_shape_bin(state)),
        ("high_position", _high_position_bin(state)),
        ("detection_maturity", _detection_maturity_bin(state)),
        ("event_speed", _event_speed_bin(state)),
        ("running_high_age", _running_high_age_bin(state)),
        ("state_liveness", "alive" if state.event_alive else "not_alive"),
    )


def assign_atlas_outcome_bin(future: FuturePathRow, *, config: AtlasConfig | None = None) -> str:
    """Assign a coarse descriptive 30m outcome bin for atlas summaries only.

    This is not a trading label and must not be consumed as decision logic.
    """
    cfg = config or AtlasConfig()
    future_return = future.future_return_30m
    future_max = future.future_max_30m
    future_min = future.future_min_30m
    if future_return is None or future_max is None or future_min is None:
        return "missing_future_30m"

    upside = bool(future.reclaimed_running_high_30m) and future_max >= cfg.outcome_move_threshold
    downside = future_min <= -cfg.outcome_move_threshold
    if upside and downside:
        return "two_sided_30m"
    if upside:
        return "upside_continuation_30m"
    if downside:
        return "downside_extension_30m"
    if abs(future_return) <= cfg.outcome_chop_threshold:
        return "range_chop_30m"
    return "mixed_drift_30m"


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
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_future_return=_mean(_future_return_30m(row) for row in group_rows),
                median_future_return=_median(_future_return_30m(row) for row in group_rows),
                mean_future_max=_mean(row.future.future_max_30m for row in group_rows),
                mean_future_min=_mean(row.future.future_min_30m for row in group_rows),
                reclaim_rate=_bool_rate(row.future.reclaimed_running_high_30m for row in group_rows),
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
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=_unique_count(row.state.symbol for row in group_rows),
                mean_minutes_since_detection=_mean(row.state.minutes_since_detection for row in group_rows),
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_distance_to_running_high=_mean(row.state.distance_to_running_high for row in group_rows),
                upside_continuation_share=_outcome_share(group_rows, "upside_continuation_30m"),
                downside_extension_share=_outcome_share(group_rows, "downside_extension_30m"),
                two_sided_share=_outcome_share(group_rows, "two_sided_30m"),
                range_chop_share=_outcome_share(group_rows, "range_chop_30m"),
                mixed_drift_share=_outcome_share(group_rows, "mixed_drift_30m"),
                missing_future_share=_outcome_share(group_rows, "missing_future_30m"),
            )
        )
    return tuple(result)


def _build_response_surfaces(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasResponseSurfaceRow, ...]:
    surfaces = (
        ("price_shape_x_detection_maturity", "price_shape", "detection_maturity"),
        ("high_position_x_event_speed", "high_position", "event_speed"),
    )
    result: list[AtlasResponseSurfaceRow] = []
    for surface_name, x_axis, y_axis in surfaces:
        groups: dict[tuple[str, str], list[AtlasInputRow]] = defaultdict(list)
        for row in rows:
            contexts = dict(row.contexts)
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
                    row_count=len(group_rows),
                    unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                    mean_future_return=_mean(_future_return_30m(row) for row in group_rows),
                    mean_future_max=_mean(row.future.future_max_30m for row in group_rows),
                    mean_future_min=_mean(row.future.future_min_30m for row in group_rows),
                    reclaim_rate=_bool_rate(row.future.reclaimed_running_high_30m for row in group_rows),
                    dominant_outcome_bin=_dominant_outcome_bin(group_rows),
                )
            )
    return tuple(result)


def _build_market_shock_groups(*, rows: Sequence[AtlasInputRow], config: AtlasConfig) -> tuple[AtlasMarketShockGroupRow, ...]:
    groups: dict[int, list[AtlasInputRow]] = defaultdict(list)
    for row in rows:
        groups[row.state.snapshot_time_ms].append(row)

    result: list[AtlasMarketShockGroupRow] = []
    for snapshot_time_ms, group_rows in sorted(groups.items()):
        symbols = sorted({row.state.symbol for row in group_rows})
        result.append(
            AtlasMarketShockGroupRow(
                atlas_version=config.atlas_version,
                market_shock_group_id=f"snapshot_{snapshot_time_ms}",
                snapshot_time_ms=snapshot_time_ms,
                row_count=len(group_rows),
                unique_event_count=_unique_count(row.state.event_id for row in group_rows),
                unique_symbol_count=len(symbols),
                symbols="|".join(symbols),
                market_shock_candidate=len(symbols) >= config.min_symbols_for_market_shock_candidate,
                mean_current_return_from_start=_mean(row.state.current_return_from_start for row in group_rows),
                mean_future_return_30m=_mean(_future_return_30m(row) for row in group_rows),
                dominant_outcome_bin=_dominant_outcome_bin(group_rows),
                temporal_contract=TEMPORAL_CONTRACT_TEXT,
            )
        )
    return tuple(result)


def _price_shape_bin(state: AnomalyState1mRow) -> str:
    value = state.current_return_from_start
    if value >= 0.05:
        return "strong_up_extension_asof"
    if value >= 0.02:
        return "moderate_up_extension_asof"
    if value <= -0.02:
        return "downside_reversal_asof"
    return "muted_move_asof"


def _high_position_bin(state: AnomalyState1mRow) -> str:
    value = state.distance_to_running_high
    if value >= -0.003:
        return "at_or_near_running_high_asof"
    if value >= -0.015:
        return "shallow_pullback_from_high_asof"
    if value >= -0.05:
        return "deep_pullback_from_high_asof"
    return "far_below_running_high_asof"


def _detection_maturity_bin(state: AnomalyState1mRow) -> str:
    value = state.minutes_since_detection
    if value <= 2:
        return "early_after_detection"
    if value <= 10:
        return "developing_after_detection"
    return "late_after_detection"


def _event_speed_bin(state: AnomalyState1mRow) -> str:
    value = state.minutes_since_event_start
    if value <= 2:
        return "fast_event_clock"
    if value <= 10:
        return "normal_event_clock"
    return "slow_event_clock"


def _running_high_age_bin(state: AnomalyState1mRow) -> str:
    value = state.time_since_running_high_minutes
    if value == 0:
        return "fresh_running_high_asof"
    if value <= 3:
        return "recent_running_high_asof"
    if value <= 10:
        return "aging_running_high_asof"
    return "stale_running_high_asof"


def _unique_by_join_key(rows: Iterable[AnomalyState1mRow] | Iterable[FuturePathRow], *, artifact_name: str) -> dict[tuple[str, str, int, int], object]:
    result: dict[tuple[str, str, int, int], object] = {}
    for row in rows:
        key = _join_key(row)
        if key in result:
            raise AtlasInputError(f"duplicate atlas join key in {artifact_name}: {key}")
        result[key] = row
    return result


def _join_key(row: AnomalyState1mRow | FuturePathRow) -> tuple[str, str, int, int]:
    return (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)


def _enforce_atlas_temporal_contract(*, state: AnomalyState1mRow, future: FuturePathRow) -> None:
    if state.feature_cutoff_time_ms > state.snapshot_time_ms:
        raise MarketDataContractError("atlas state feature_cutoff_time_ms must be <= snapshot_time_ms")
    if future.feature_cutoff_time_ms > future.snapshot_time_ms:
        raise MarketDataContractError("atlas future feature_cutoff_time_ms must be <= snapshot_time_ms")
    if future.future_start_time_ms <= state.snapshot_time_ms:
        raise MarketDataContractError("atlas future_start_time_ms must be > state snapshot_time_ms")
    if state.snapshot_time_ms != future.snapshot_time_ms:
        raise MarketDataContractError("atlas join requires equal state/future snapshot_time_ms")
    if state.feature_cutoff_time_ms != future.feature_cutoff_time_ms:
        raise MarketDataContractError("atlas join requires equal state/future feature_cutoff_time_ms")


def _outcome_share(rows: Sequence[AtlasInputRow], outcome_bin: str) -> float | None:
    if not rows:
        return None
    return sum(1 for row in rows if row.atlas_outcome_bin == outcome_bin) / len(rows)


def _dominant_outcome_bin(rows: Sequence[AtlasInputRow]) -> str:
    if not rows:
        return "none"
    counts = Counter(row.atlas_outcome_bin for row in rows)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _future_return_30m(row: AtlasInputRow) -> float | None:
    return row.future.future_return_30m


def _unique_count(values: Iterable[str]) -> int:
    return len(set(values))


def _mean(values: Iterable[float | int | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return None
    return sum(clean) / len(clean)


def _median(values: Iterable[float | None]) -> float | None:
    clean = [float(value) for value in values if value is not None and math.isfinite(float(value))]
    if not clean:
        return None
    return float(median(clean))


def _bool_rate(values: Iterable[bool | None]) -> float | None:
    clean = [value for value in values if value is not None]
    if not clean:
        return None
    return sum(1 for value in clean if value) / len(clean)


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _optional_int(row: Mapping[str, object], name: str) -> int | None:
    value = row[name]
    if _is_missing(value):
        return None
    return int(value)


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if _is_missing(value):
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when provided")
    return result


def _optional_bool(row: Mapping[str, object], name: str) -> bool | None:
    value = row[name]
    if _is_missing(value):
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean when provided")


def _is_missing(value: object) -> bool:
    if value == "":
        return True
    try:
        return bool(pd.isna(value))
    except TypeError:
        return False


def _row_to_csv_payload(row: object) -> dict[str, object]:
    payload = asdict(row)
    return {key: _csv_value(value) for key, value in payload.items()}


def _csv_value(value: object) -> object:
    return "" if value is None else value
