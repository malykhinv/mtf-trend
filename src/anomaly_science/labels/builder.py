from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.future import FuturePathRow
from anomaly_science.contracts.future import BARRIER_RESOLUTION_STOP_LOSS_FIRST
from anomaly_science.contracts.labels import (
    ATR_LABEL_SOURCE,
    MISSING_FUTURE_SCENARIO,
    TEMPORAL_LABEL_CONTRACT,
    StrategyOutcomeLabelRow,
)
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.future import load_strategy_future_paths_csv, load_strategy_state_1m_csv
from anomaly_science.labels.config import OutcomeLabelConfig


class OutcomeLabelInputError(ValueError):
    """Raised when state/future inputs cannot produce one-to-one outcome labels."""


class OutcomeLabelArtifactError(ValueError):
    """Raised when anomaly_outcome_labels.csv violates its strict artifact boundary."""


@dataclass(frozen=True, slots=True)
class OutcomeLabelInputRow:
    state: StrategyState1mRow
    future: FuturePathRow


def load_outcome_label_inputs(
    *,
    state_path: str | Path,
    future_path: str | Path,
) -> tuple[OutcomeLabelInputRow, ...]:
    """Load state and future artifacts through strict schema boundaries."""
    return build_outcome_label_inputs(
        state_rows=load_strategy_state_1m_csv(state_path),
        future_rows=load_strategy_future_paths_csv(future_path),
    )


def build_outcome_label_inputs(
    *,
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
) -> tuple[OutcomeLabelInputRow, ...]:
    states = tuple(state_rows)
    futures = tuple(future_rows)
    state_by_key = _unique_by_join_key(states, artifact_name="anomaly_state_1m.csv")
    future_by_key = _unique_by_join_key(futures, artifact_name="anomaly_future_paths.csv")

    state_keys = set(state_by_key)
    future_keys = set(future_by_key)
    if state_keys != future_keys:
        missing_future = sorted(state_keys - future_keys)[:5]
        orphan_future = sorted(future_keys - state_keys)[:5]
        raise OutcomeLabelInputError(
            "state/future label join must be one-to-one on "
            "event_id,symbol,snapshot_time_ms,feature_cutoff_time_ms; "
            f"missing_future={missing_future}, orphan_future={orphan_future}"
        )

    rows: list[OutcomeLabelInputRow] = []
    for key in sorted(state_keys):
        state = state_by_key[key]
        future = future_by_key[key]
        _enforce_label_temporal_contract(state=state, future=future)
        rows.append(OutcomeLabelInputRow(state=state, future=future))
    return tuple(rows)


def build_strategy_outcome_labels(
    *,
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    future_rows: Sequence[FuturePathRow] | Iterable[FuturePathRow],
    config: OutcomeLabelConfig | None = None,
) -> tuple[StrategyOutcomeLabelRow, ...]:
    inputs = build_outcome_label_inputs(state_rows=state_rows, future_rows=future_rows)
    return build_strategy_outcome_labels_from_inputs(inputs=inputs, config=config)


build_anomaly_outcome_labels = build_strategy_outcome_labels


def build_strategy_outcome_labels_from_inputs(
    *,
    inputs: Sequence[OutcomeLabelInputRow] | Iterable[OutcomeLabelInputRow],
    config: OutcomeLabelConfig | None = None,
) -> tuple[StrategyOutcomeLabelRow, ...]:
    cfg = config or OutcomeLabelConfig()
    rows: list[StrategyOutcomeLabelRow] = []
    for input_row in inputs:
        future = input_row.future
        scenario_15m = assign_future_nature_scenario(future=future, horizon_minutes=15, config=cfg)
        scenario_30m = assign_future_nature_scenario(future=future, horizon_minutes=30, config=cfg)
        scenario_60m = assign_future_nature_scenario(future=future, horizon_minutes=60, config=cfg)
        scenario_120m = assign_future_nature_scenario(future=future, horizon_minutes=120, config=cfg)
        scenario_180m = assign_future_nature_scenario(future=future, horizon_minutes=180, config=cfg)
        rows.append(
            StrategyOutcomeLabelRow(
                label_schema_version=cfg.label_schema_version,
                atr_window_minutes=cfg.atr_window_minutes,
                core_atr_1440=future.core_atr_1440,
                k_continuation=cfg.k_continuation,
                k_fade=cfg.k_fade,
                k_chop=cfg.k_chop,
                event_id=future.event_id,
                symbol=future.symbol,
                snapshot_time_ms=future.snapshot_time_ms,
                feature_cutoff_time_ms=future.feature_cutoff_time_ms,
                future_start_time_ms=future.future_start_time_ms,
                scenario_15m=scenario_15m,
                scenario_30m=scenario_30m,
                scenario_60m=scenario_60m,
                scenario_120m=scenario_120m,
                scenario_180m=scenario_180m,
                label_available_15m=_label_available(scenario_15m),
                label_available_30m=_label_available(scenario_30m),
                label_available_60m=_label_available(scenario_60m),
                label_available_120m=_label_available(scenario_120m),
                label_available_180m=_label_available(scenario_180m),
                label_source=ATR_LABEL_SOURCE,
                temporal_contract=TEMPORAL_LABEL_CONTRACT,
            )
        )
    return tuple(rows)


build_anomaly_outcome_labels_from_inputs = build_strategy_outcome_labels_from_inputs


def assign_future_nature_scenario(
    *,
    future: FuturePathRow,
    horizon_minutes: int,
    config: OutcomeLabelConfig | None = None,
) -> str:
    """Assign a coarse future-nature scenario from ATR-normalized future paths.

    This is a scientific target for prediction calibration. It is not a trade
    direction, not an entry rule, and not an EV/PnL optimization target. Raw
    percent fields are intentionally ignored: labels are ATR-normalized only.
    Ambiguous two-sided / trap-like paths are mapped to ``unclear`` for the MVP
    4-class target contract.
    """
    cfg = config or OutcomeLabelConfig()
    if horizon_minutes not in cfg.horizons_minutes:
        raise ValueError(f"unsupported ATR label horizon: {horizon_minutes}")
    if future.atr_window_minutes != cfg.atr_window_minutes:
        raise MarketDataContractError(
            f"future atr_window_minutes={future.atr_window_minutes} does not match label schema atr_window_minutes={cfg.atr_window_minutes}"
        )

    future_return_atr = _future_value(future=future, base_name="future_return_atr", horizon_minutes=horizon_minutes)
    future_max_atr = _future_value(future=future, base_name="future_max_atr", horizon_minutes=horizon_minutes)
    future_min_atr = _future_value(future=future, base_name="future_min_atr", horizon_minutes=horizon_minutes)
    if future.core_atr_1440 is None or future_return_atr is None or future_max_atr is None or future_min_atr is None:
        return MISSING_FUTURE_SCENARIO
    _enforce_double_barrier_threshold_schema(future=future, config=cfg, horizon_minutes=horizon_minutes)

    if _double_barrier_resolved_stop_first(future=future, horizon_minutes=horizon_minutes):
        return "unclear"

    upside_extension = future_max_atr >= cfg.k_continuation
    downside_extension = future_min_atr <= -cfg.k_fade

    if upside_extension and downside_extension:
        return "unclear"
    if upside_extension and future_return_atr >= cfg.k_chop:
        return "long_continuation"
    if downside_extension and future_return_atr <= -cfg.k_chop:
        return "short_fade"
    if future_max_atr < cfg.k_chop and future_min_atr > -cfg.k_chop:
        return "static_or_chop"
    return "unclear"


def load_strategy_outcome_labels_csv(path: str | Path) -> tuple[StrategyOutcomeLabelRow, ...]:
    """Read anomaly_outcome_labels.csv through the declared strict artifact schema."""
    labels_path = Path(path)
    if not labels_path.exists():
        raise OutcomeLabelArtifactError(f"outcome labels artifact is missing: {labels_path}")

    frame = pd.read_csv(labels_path)
    schema = get_artifact_schema("anomaly_outcome_labels.csv")
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise OutcomeLabelArtifactError(
            f"outcome labels artifact columns must match {expected_columns}, got {actual_columns}"
        )

    rows: list[StrategyOutcomeLabelRow] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(
                StrategyOutcomeLabelRow(
                    label_schema_version=_required_str(row, "label_schema_version"),
                    atr_window_minutes=_required_int(row, "atr_window_minutes"),
                    core_atr_1440=_optional_float(row, "ATR_1d_asof_t"),
                    k_continuation=_required_float(row, "k_continuation"),
                    k_fade=_required_float(row, "k_fade"),
                    k_chop=_required_float(row, "k_chop"),
                    event_id=_required_str(row, "event_id"),
                    symbol=_required_str(row, "symbol"),
                    snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
                    feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
                    future_start_time_ms=_required_int(row, "future_start_time_ms"),
                    scenario_15m=_required_str(row, "scenario_15m"),
                    scenario_30m=_required_str(row, "scenario_30m"),
                    scenario_60m=_required_str(row, "scenario_60m"),
                    scenario_120m=_required_str(row, "scenario_120m"),
                    scenario_180m=_required_str(row, "scenario_180m"),
                    label_available_15m=_required_bool(row, "label_available_15m"),
                    label_available_30m=_required_bool(row, "label_available_30m"),
                    label_available_60m=_required_bool(row, "label_available_60m"),
                    label_available_120m=_required_bool(row, "label_available_120m"),
                    label_available_180m=_required_bool(row, "label_available_180m"),
                    label_source=_required_str(row, "label_source"),
                    temporal_contract=_required_str(row, "temporal_contract"),
                )
            )
        except (TypeError, ValueError) as exc:
            raise OutcomeLabelArtifactError(f"invalid anomaly_outcome_labels.csv row {row_index}: {exc}") from exc
    return tuple(rows)


load_anomaly_outcome_labels_csv = load_strategy_outcome_labels_csv


def outcome_label_rows_to_artifact(rows: Sequence[StrategyOutcomeLabelRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = _with_core_atr_csv_alias(asdict(row))
        result.append({key: _csv_value(value) for key, value in payload.items()})
    return result


def _with_core_atr_csv_alias(payload: dict[str, object]) -> dict[str, object]:
    return {"ATR_1d_asof_t" if key == "core_atr_1440" else key: value for key, value in payload.items()}


def _double_barrier_resolved_stop_first(*, future: FuturePathRow, horizon_minutes: int) -> bool:
    hit = getattr(future, f"intracandle_double_barrier_hit_{horizon_minutes}m")
    resolution = getattr(future, f"barrier_resolution_{horizon_minutes}m")
    return bool(hit is True and resolution == BARRIER_RESOLUTION_STOP_LOSS_FIRST)


def _enforce_double_barrier_threshold_schema(
    *,
    future: FuturePathRow,
    config: OutcomeLabelConfig,
    horizon_minutes: int,
) -> None:
    hit = getattr(future, f"intracandle_double_barrier_hit_{horizon_minutes}m")
    if hit is None:
        return
    if future.double_barrier_k_continuation != config.k_continuation:
        raise MarketDataContractError(
            "future double_barrier_k_continuation must match label k_continuation "
            f"for horizon {horizon_minutes}m"
        )
    if future.double_barrier_k_fade != config.k_fade:
        raise MarketDataContractError(
            "future double_barrier_k_fade must match label k_fade "
            f"for horizon {horizon_minutes}m"
        )


def _future_value(*, future: FuturePathRow, base_name: str, horizon_minutes: int) -> float | None:
    value = getattr(future, f"{base_name}_{horizon_minutes}m")
    if value is not None and not math.isfinite(float(value)):
        raise MarketDataContractError(f"{base_name}_{horizon_minutes}m must be finite when present")
    return value



def _label_available(scenario: str) -> bool:
    return scenario != MISSING_FUTURE_SCENARIO


def _unique_by_join_key(
    rows: Iterable[StrategyState1mRow] | Iterable[FuturePathRow],
    *,
    artifact_name: str,
) -> dict[tuple[str, str, int, int], object]:
    result: dict[tuple[str, str, int, int], object] = {}
    for row in rows:
        key = _join_key(row)
        if key in result:
            raise OutcomeLabelInputError(f"duplicate label join key in {artifact_name}: {key}")
        result[key] = row
    return result


def _join_key(row: StrategyState1mRow | FuturePathRow) -> tuple[str, str, int, int]:
    return (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)


def _enforce_label_temporal_contract(*, state: StrategyState1mRow, future: FuturePathRow) -> None:
    if state.snapshot_time_ms != future.snapshot_time_ms:
        raise MarketDataContractError("label join requires equal state/future snapshot_time_ms")
    if state.feature_cutoff_time_ms != future.feature_cutoff_time_ms:
        raise MarketDataContractError("label join requires equal state/future feature_cutoff_time_ms")
    if state.feature_cutoff_time_ms > state.snapshot_time_ms:
        raise MarketDataContractError("label state feature_cutoff_time_ms must be <= snapshot_time_ms")
    if future.feature_cutoff_time_ms > future.snapshot_time_ms:
        raise MarketDataContractError("label future feature_cutoff_time_ms must be <= snapshot_time_ms")
    if future.future_start_time_ms <= state.snapshot_time_ms:
        raise MarketDataContractError("label future_start_time_ms must be > state snapshot_time_ms")


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


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite")
    return result


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    if pd.isna(value) or value == "":
        return None
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{name} must be finite when present")
    return result


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if isinstance(value, bool):
        return value
    if pd.isna(value):
        raise ValueError(f"{name} is required")
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"true", "1"}:
        return True
    if text in {"false", "0"}:
        return False
    raise ValueError(f"{name} must be a boolean")


def _csv_value(value: object) -> object:
    return "" if value is None else value
