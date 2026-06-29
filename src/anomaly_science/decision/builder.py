from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.decision import (
    EXPECTED_VALUE_TEMPORAL_CONTRACT,
    ExpectedValueMetricRow,
    ExpectedValueRow,
)
from anomaly_science.contracts.labels import StrategyOutcomeLabelRow
from anomaly_science.contracts.market import MarketDataContractError
from anomaly_science.contracts.prediction import OosPredictionRow
from anomaly_science.contracts.state import StrategyState1mRow
from anomaly_science.decision.config import ExpectedValueConfig
from anomaly_science.future import load_strategy_state_1m_csv
from anomaly_science.labels import load_strategy_outcome_labels_csv
from anomaly_science.prediction import load_anomaly_oos_predictions_csv
from anomaly_science.strategy.base import BaseStrategy
from anomaly_science.strategy.execution import PositionSide, StructuralAnchor
from anomaly_science.strategy.registry import get_strategy


class ExpectedValueInputError(ValueError):
    """Raised when EV input artifacts cannot be joined or priced cleanly."""


class ExpectedValueArtifactError(ValueError):
    """Raised when EV artifacts violate strict schemas."""


def load_expected_value_inputs(
    *,
    state_path: str | Path,
    labels_path: str | Path,
    predictions_path: str | Path,
    config: ExpectedValueConfig | None = None,
) -> tuple[ExpectedValueRow, ...]:
    return build_expected_value_rows(
        state_rows=load_strategy_state_1m_csv(state_path),
        label_rows=load_strategy_outcome_labels_csv(labels_path),
        prediction_rows=load_anomaly_oos_predictions_csv(predictions_path),
        config=config,
    )


def build_expected_value_rows(
    *,
    state_rows: Sequence[StrategyState1mRow] | Iterable[StrategyState1mRow],
    label_rows: Sequence[StrategyOutcomeLabelRow] | Iterable[StrategyOutcomeLabelRow],
    prediction_rows: Sequence[OosPredictionRow] | Iterable[OosPredictionRow],
    config: ExpectedValueConfig | None = None,
) -> tuple[ExpectedValueRow, ...]:
    cfg = config or ExpectedValueConfig()
    strategy = get_strategy(cfg.strategy_name)
    if strategy.metadata.horizon_minutes != cfg.target_horizon_minutes:
        raise ExpectedValueInputError(
            f"EV target_horizon_minutes={cfg.target_horizon_minutes} does not match strategy horizon {strategy.metadata.horizon_minutes}"
        )
    state_by_key = _unique_by_join_key(state_rows, artifact_name="anomaly_state_1m.csv")
    label_by_key = _unique_by_join_key(label_rows, artifact_name="anomaly_outcome_labels.csv")
    rows: list[ExpectedValueRow] = []
    for prediction in sorted(prediction_rows, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id)):
        if prediction.target_horizon_minutes != cfg.target_horizon_minutes:
            continue
        if prediction.target_label_column != cfg.target_label_column:
            raise ExpectedValueInputError(
                "prediction target_label_column does not match EV target_horizon_minutes: "
                f"got {prediction.target_label_column!r}, expected {cfg.target_label_column!r}"
            )
        key = _join_key(prediction)
        try:
            state = state_by_key[key]
            label = label_by_key[key]
        except KeyError as exc:
            raise ExpectedValueInputError(f"prediction EV join key is missing from state or labels: {key}") from exc
        rows.append(_build_row(state=state, label=label, prediction=prediction, config=cfg, strategy=strategy))
    return tuple(rows)


def build_expected_value_metric_rows(
    *,
    expected_value_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    config: ExpectedValueConfig | None = None,
) -> tuple[ExpectedValueMetricRow, ...]:
    cfg = config or ExpectedValueConfig()
    rows = tuple(expected_value_rows)
    metrics: list[ExpectedValueMetricRow] = []

    def add(name: str, value: str | float | int, row_count: int, notes: str) -> None:
        metrics.append(
            ExpectedValueMetricRow(
                ev_version=cfg.ev_version,
                target_horizon_minutes=cfg.target_horizon_minutes,
                metric_name=name,
                metric_value=_metric_value(value),
                row_count=row_count,
                notes=notes,
            )
        )

    add("ev_rows", len(rows), len(rows), "nature-proxy utility rows computed from OOS predictions")
    add("utility_model_kind", cfg.utility_model_kind, len(rows), "utility model kind recorded for interpretation; nature_proxy is not final EV proof")
    add("utility_evidence_status", cfg.utility_evidence_status, len(rows), "NON_FINAL until realized barrier first-hit/timeout outcome modeling exists")
    add("utility_evidence_claim_allowed", str(cfg.utility_evidence_claim_allowed).lower(), len(rows), "false for nature-proxy utility artifacts")
    add("confident_rows", sum(1 for row in rows if row.is_prediction_confident), len(rows), "rows meeting min_prediction_confidence")
    add("rr_acceptable_rows", sum(1 for row in rows if row.is_RR_still_acceptable), len(rows), "rows where either side meets min_rr proxy")
    add("selected_rr_acceptable_rows", sum(1 for row in rows if row.selected_RR_acceptable), len(rows), "rows where selected long/short action meets its own side-specific min_rr proxy")
    add("positive_long_ev_rows", sum(1 for row in rows if row.EV_long > 0.0), len(rows), "rows with positive long EV proxy")
    add("positive_short_ev_rows", sum(1 for row in rows if row.EV_short > 0.0), len(rows), "rows with positive short EV proxy")
    add("actionable_positive_ev_rows", sum(1 for row in rows if row.best_action in {"long", "short"}), len(rows), "rows where long/short beats wait and no_trade")
    if rows:
        add("mean_EV_long", _mean(row.EV_long for row in rows), len(rows), "mean long nature-proxy utility in price units")
        add("mean_EV_short", _mean(row.EV_short for row in rows), len(rows), "mean short nature-proxy utility in price units")
        add("max_EV_long", max(row.EV_long for row in rows), len(rows), "maximum long nature-proxy utility in price units")
        add("max_EV_short", max(row.EV_short for row in rows), len(rows), "maximum short nature-proxy utility in price units")
        add("mean_cost_penalty", _mean(row.cost_penalty for row in rows), len(rows), "mean round-trip fee plus slippage penalty in price units")
    return tuple(metrics)


def expected_value_rows_to_artifact(rows: Sequence[ExpectedValueRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        result.append(_with_core_atr_csv_alias(asdict(row)))
    return result


def _with_core_atr_csv_alias(payload: dict[str, object]) -> dict[str, object]:
    return {"ATR_1d_asof_t" if key == "core_atr_1440" else key: value for key, value in payload.items()}


def expected_value_metric_rows_to_artifact(rows: Sequence[ExpectedValueMetricRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def load_anomaly_decision_timing_csv(path: str | Path) -> tuple[ExpectedValueRow, ...]:
    return tuple(
        _load_ev_artifact(
            path=path,
            schema_name="anomaly_decision_timing.csv",
            row_builder=_expected_value_from_mapping,
        )
    )




def load_anomaly_ev_metrics_csv(path: str | Path) -> tuple[ExpectedValueMetricRow, ...]:
    return tuple(
        _load_ev_artifact(
            path=path,
            schema_name="anomaly_ev_metrics.csv",
            row_builder=_metric_from_mapping,
        )
    )


def _build_row(
    *,
    state: StrategyState1mRow,
    label: StrategyOutcomeLabelRow,
    prediction: OosPredictionRow,
    config: ExpectedValueConfig,
    strategy: BaseStrategy,
) -> ExpectedValueRow:
    _enforce_ev_input_temporal_contract(state=state, label=label, prediction=prediction)
    if label.core_atr_1440 is None:
        raise ExpectedValueInputError(f"core_atr_1440 is required for EV row {prediction.event_id}")
    entry_reference_price = state.current_close
    long_geometry = _structural_geometry(state=state, strategy=strategy, side=PositionSide.LONG)
    short_geometry = _structural_geometry(state=state, strategy=strategy, side=PositionSide.SHORT)
    cost_penalty = entry_reference_price * ((2.0 * config.fee_bps + config.slippage_bps) / 10_000.0)
    p_follow_long = prediction.p_long_continuation
    p_adverse_long = prediction.p_short_fade
    p_follow_short = prediction.p_short_fade
    p_adverse_short = prediction.p_long_continuation
    rr_long = long_geometry.rr
    rr_short = short_geometry.rr
    ev_long = _structural_ev(
        resolved=long_geometry.resolved,
        follow_probability=p_follow_long,
        adverse_probability=p_adverse_long,
        target_distance=long_geometry.target_distance,
        stop_distance=long_geometry.stop_distance,
        cost_penalty=cost_penalty,
    )
    ev_short = _structural_ev(
        resolved=short_geometry.resolved,
        follow_probability=p_follow_short,
        adverse_probability=p_adverse_short,
        target_distance=short_geometry.target_distance,
        stop_distance=short_geometry.stop_distance,
        cost_penalty=cost_penalty,
    )
    ev_wait = 0.0
    ev_no_trade = 0.0
    best_action = _best_action(EV_long=ev_long, EV_short=ev_short, EV_wait=ev_wait, EV_no_trade=ev_no_trade)
    rr_long_acceptable = rr_long >= config.min_rr
    rr_short_acceptable = rr_short >= config.min_rr
    selected_rr = _selected_rr(best_action=best_action, rr_long=rr_long, rr_short=rr_short)
    selected_rr_acceptable = _selected_rr_acceptable(
        best_action=best_action,
        rr_long_acceptable=rr_long_acceptable,
        rr_short_acceptable=rr_short_acceptable,
    )
    selected_geometry = long_geometry if best_action == "long" else short_geometry
    metadata = strategy.metadata
    return ExpectedValueRow(
        ev_version=config.ev_version,
        utility_model_kind=config.utility_model_kind,
        utility_evidence_status=config.utility_evidence_status,
        utility_evidence_claim_allowed=config.utility_evidence_claim_allowed,
        strategy_name=metadata.strategy_name,
        strategy_version=metadata.strategy_version,
        event_id=prediction.event_id,
        symbol=prediction.symbol,
        state_time_ms=state.state_time_ms,
        snapshot_time_ms=prediction.snapshot_time_ms,
        feature_cutoff_time_ms=prediction.feature_cutoff_time_ms,
        future_start_time_ms=prediction.future_start_time_ms,
        target_horizon_minutes=prediction.target_horizon_minutes,
        execution_reference_model=config.execution_reference_model,
        entry_price_basis=config.entry_price_basis,
        entry_reference_price=entry_reference_price,
        core_atr_1440=label.core_atr_1440,
        execution_policy_version=metadata.execution_policy_version,
        stop_policy_id=selected_geometry.stop_policy_id,
        target_policy_id=selected_geometry.target_policy_id,
        stop_anchor=selected_geometry.stop_anchor,
        target_anchor=selected_geometry.target_anchor,
        stop_trigger=selected_geometry.stop_trigger,
        target_trigger=selected_geometry.target_trigger,
        stop_reference_price=selected_geometry.stop_price if selected_geometry.resolved else None,
        target_reference_price=selected_geometry.target_price if selected_geometry.resolved else None,
        stop_distance=selected_geometry.stop_distance if selected_geometry.resolved else None,
        target_distance=selected_geometry.target_distance if selected_geometry.resolved else None,
        execution_policy_resolved=selected_geometry.resolved,
        fee_bps=config.fee_bps,
        slippage_bps=config.slippage_bps,
        cost_model=config.cost_model,
        cost_penalty=cost_penalty,
        p_follow_through_long=p_follow_long,
        p_adverse_long=p_adverse_long,
        p_follow_through_short=p_follow_short,
        p_adverse_short=p_adverse_short,
        confidence_calibrated=prediction.prediction_confidence,
        RR_long_proxy=rr_long,
        RR_short_proxy=rr_short,
        RR_long_acceptable=rr_long_acceptable,
        RR_short_acceptable=rr_short_acceptable,
        selected_RR=selected_rr,
        selected_RR_acceptable=selected_rr_acceptable,
        EV_long=ev_long,
        EV_short=ev_short,
        EV_wait=ev_wait,
        EV_no_trade=ev_no_trade,
        best_action=best_action,
        is_prediction_confident=prediction.prediction_confidence >= config.min_prediction_confidence,
        is_RR_still_acceptable=rr_long_acceptable or rr_short_acceptable,
        temporal_contract=EXPECTED_VALUE_TEMPORAL_CONTRACT,
    )


@dataclass(frozen=True, slots=True)
class _StructuralGeometry:
    stop_policy_id: str
    target_policy_id: str
    stop_anchor: str
    target_anchor: str
    stop_trigger: str
    target_trigger: str
    stop_price: float | None
    target_price: float | None
    stop_distance: float | None
    target_distance: float | None

    @property
    def resolved(self) -> bool:
        return self.stop_distance is not None and self.target_distance is not None

    @property
    def rr(self) -> float:
        if not self.resolved or self.stop_distance is None or self.target_distance is None:
            return 0.0
        return self.target_distance / self.stop_distance


def _structural_geometry(*, state: StrategyState1mRow, strategy: BaseStrategy, side: PositionSide) -> _StructuralGeometry:
    stop_policy = strategy.execution_policies.stop_for_side(side)
    target_policies = strategy.execution_policies.take_profits_for_side(side)
    if len(target_policies) != 1:
        raise ExpectedValueInputError(
            f"EV requires exactly one structural target policy for {side.value}; got {len(target_policies)}"
        )
    target_policy = target_policies[0]
    stop_price = _resolve_anchor_price(state=state, anchor=stop_policy.initial_anchor)
    target_price = _resolve_anchor_price(state=state, anchor=target_policy.anchor)
    entry = state.current_close
    if stop_price is None or target_price is None:
        stop_distance = None
        target_distance = None
    elif side is PositionSide.LONG and stop_price < entry < target_price:
        stop_distance = entry - stop_price
        target_distance = target_price - entry
    elif side is PositionSide.SHORT and target_price < entry < stop_price:
        stop_distance = stop_price - entry
        target_distance = entry - target_price
    else:
        stop_distance = None
        target_distance = None
    return _StructuralGeometry(
        stop_policy_id=stop_policy.policy_id,
        target_policy_id=target_policy.policy_id,
        stop_anchor=stop_policy.initial_anchor.value,
        target_anchor=target_policy.anchor.value,
        stop_trigger=stop_policy.trigger.value,
        target_trigger=target_policy.trigger.value,
        stop_price=stop_price,
        target_price=target_price,
        stop_distance=stop_distance,
        target_distance=target_distance,
    )


def _resolve_anchor_price(*, state: StrategyState1mRow, anchor: StructuralAnchor) -> float | None:
    if anchor in {StructuralAnchor.RUNNING_HIGH, StructuralAnchor.EVENT_MAIN_HIGH}:
        return state.running_high_asof_t
    if anchor in {StructuralAnchor.RUNNING_LOW, StructuralAnchor.EVENT_BASE}:
        return state.running_low_asof_t
    if anchor is StructuralAnchor.CONFIRMED_SWING_HIGH:
        return state.structural_high_asof_t
    if anchor is StructuralAnchor.CONFIRMED_SWING_LOW:
        return state.structural_low_asof_t
    raise ExpectedValueInputError(f"unsupported structural anchor: {anchor.value}")


def _structural_ev(
    *,
    resolved: bool,
    follow_probability: float,
    adverse_probability: float,
    target_distance: float | None,
    stop_distance: float | None,
    cost_penalty: float,
) -> float:
    if not resolved or target_distance is None or stop_distance is None:
        return -cost_penalty
    return follow_probability * target_distance - adverse_probability * stop_distance - cost_penalty


def _enforce_ev_input_temporal_contract(
    *,
    state: StrategyState1mRow,
    label: StrategyOutcomeLabelRow,
    prediction: OosPredictionRow,
) -> None:
    if _join_key(state) != _join_key(label) or _join_key(state) != _join_key(prediction):
        raise ExpectedValueInputError("EV requires equal state/label/prediction join keys")
    if state.state_time_ms != prediction.snapshot_time_ms:
        raise ExpectedValueInputError("EV state_time_ms must equal prediction snapshot_time_ms")
    if label.future_start_time_ms != prediction.future_start_time_ms:
        raise ExpectedValueInputError("EV label and prediction future_start_time_ms must match")
    if prediction.target_scenario != _target_for_horizon(label, prediction.target_horizon_minutes):
        raise ExpectedValueInputError("EV prediction target_scenario must match label scenario for the selected horizon")


def _target_for_horizon(label: StrategyOutcomeLabelRow, horizon_minutes: int) -> str:
    if horizon_minutes == 15:
        return label.scenario_15m
    if horizon_minutes == 30:
        return label.scenario_30m
    if horizon_minutes == 60:
        return label.scenario_60m
    if horizon_minutes == 120:
        return label.scenario_120m
    if horizon_minutes == 180:
        return label.scenario_180m
    raise ExpectedValueInputError(f"unsupported EV target horizon: {horizon_minutes}")


def _selected_rr(*, best_action: str, rr_long: float, rr_short: float) -> float:
    if best_action == "long":
        return rr_long
    if best_action == "short":
        return rr_short
    return 0.0


def _selected_rr_acceptable(*, best_action: str, rr_long_acceptable: bool, rr_short_acceptable: bool) -> bool:
    if best_action == "long":
        return rr_long_acceptable
    if best_action == "short":
        return rr_short_acceptable
    return False


def _best_action(*, EV_long: float, EV_short: float, EV_wait: float, EV_no_trade: float) -> str:
    passive_ev = max(EV_wait, EV_no_trade)
    if EV_long > passive_ev and EV_long >= EV_short:
        return "long"
    if EV_short > passive_ev:
        return "short"
    if EV_wait > EV_no_trade:
        return "wait"
    return "no_trade"


def _unique_by_join_key(
    rows: Iterable[StrategyState1mRow] | Iterable[StrategyOutcomeLabelRow],
    *,
    artifact_name: str,
) -> dict[tuple[str, str, int, int], object]:
    result: dict[tuple[str, str, int, int], object] = {}
    for row in rows:
        key = _join_key(row)
        if key in result:
            raise ExpectedValueInputError(f"duplicate EV join key in {artifact_name}: {key}")
        result[key] = row
    return result


def _join_key(row: StrategyState1mRow | StrategyOutcomeLabelRow | OosPredictionRow) -> tuple[str, str, int, int]:
    return (row.event_id, row.symbol, row.snapshot_time_ms, row.feature_cutoff_time_ms)


def _load_ev_artifact(*, path: str | Path, schema_name: str, row_builder: object) -> tuple[object, ...]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise ExpectedValueArtifactError(f"EV artifact is missing: {artifact_path}")
    schema = get_artifact_schema(schema_name)
    expected_columns = list(schema.required_columns)
    rows: list[object] = []
    with artifact_path.open(encoding="utf-8-sig", newline="") as file_obj:
        reader = csv.DictReader(file_obj)
        actual_columns = list(reader.fieldnames or [])
        if actual_columns != expected_columns:
            raise ExpectedValueArtifactError(f"{schema_name} columns must match {expected_columns}, got {actual_columns}")
        for row_index, row in enumerate(reader):
            try:
                rows.append(row_builder(row))  # type: ignore[operator]
            except (TypeError, ValueError, MarketDataContractError) as exc:
                raise ExpectedValueArtifactError(f"invalid {schema_name} row {row_index}: {exc}") from exc
    return tuple(rows)


def _expected_value_from_mapping(row: Mapping[str, object]) -> ExpectedValueRow:
    return ExpectedValueRow(
        ev_version=_required_str(row, "ev_version"),
        utility_model_kind=_required_str(row, "utility_model_kind"),
        utility_evidence_status=_required_str(row, "utility_evidence_status"),
        utility_evidence_claim_allowed=_required_bool(row, "utility_evidence_claim_allowed"),
        strategy_name=_required_str(row, "strategy_name"),
        strategy_version=_required_str(row, "strategy_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        state_time_ms=_required_int(row, "state_time_ms"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        future_start_time_ms=_required_int(row, "future_start_time_ms"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        execution_reference_model=_required_str(row, "execution_reference_model"),
        entry_price_basis=_required_str(row, "entry_price_basis"),
        entry_reference_price=_required_float(row, "entry_reference_price"),
        core_atr_1440=_required_float(row, "ATR_1d_asof_t"),
        execution_policy_version=_required_str(row, "execution_policy_version"),
        stop_policy_id=_required_str(row, "stop_policy_id"),
        target_policy_id=_required_str(row, "target_policy_id"),
        stop_anchor=_required_str(row, "stop_anchor"),
        target_anchor=_required_str(row, "target_anchor"),
        stop_trigger=_required_str(row, "stop_trigger"),
        target_trigger=_required_str(row, "target_trigger"),
        stop_reference_price=_optional_float(row, "stop_reference_price"),
        target_reference_price=_optional_float(row, "target_reference_price"),
        stop_distance=_optional_float(row, "stop_distance"),
        target_distance=_optional_float(row, "target_distance"),
        execution_policy_resolved=_required_bool(row, "execution_policy_resolved"),
        fee_bps=_required_float(row, "fee_bps"),
        slippage_bps=_required_float(row, "slippage_bps"),
        cost_model=_required_str(row, "cost_model"),
        cost_penalty=_required_float(row, "cost_penalty"),
        p_follow_through_long=_required_float(row, "p_follow_through_long"),
        p_adverse_long=_required_float(row, "p_adverse_long"),
        p_follow_through_short=_required_float(row, "p_follow_through_short"),
        p_adverse_short=_required_float(row, "p_adverse_short"),
        confidence_calibrated=_required_float(row, "confidence_calibrated"),
        RR_long_proxy=_required_float(row, "RR_long_proxy"),
        RR_short_proxy=_required_float(row, "RR_short_proxy"),
        RR_long_acceptable=_required_bool(row, "RR_long_acceptable"),
        RR_short_acceptable=_required_bool(row, "RR_short_acceptable"),
        selected_RR=_required_float(row, "selected_RR"),
        selected_RR_acceptable=_required_bool(row, "selected_RR_acceptable"),
        EV_long=_required_float(row, "EV_long"),
        EV_short=_required_float(row, "EV_short"),
        EV_wait=_required_float(row, "EV_wait"),
        EV_no_trade=_required_float(row, "EV_no_trade"),
        best_action=_required_str(row, "best_action"),
        is_prediction_confident=_required_bool(row, "is_prediction_confident"),
        is_RR_still_acceptable=_required_bool(row, "is_RR_still_acceptable"),
        temporal_contract=_required_str(row, "temporal_contract"),
    )


def _metric_from_mapping(row: Mapping[str, object]) -> ExpectedValueMetricRow:
    return ExpectedValueMetricRow(
        ev_version=_required_str(row, "ev_version"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        metric_name=_required_str(row, "metric_name"),
        metric_value=_required_str(row, "metric_value"),
        row_count=_required_int(row, "row_count"),
        notes=_required_str(row, "notes"),
    )


def _mean(values: Iterable[float]) -> float:
    items = list(values)
    if not items:
        return 0.0
    return float(sum(items)) / float(len(items))


def _metric_value(value: str | float | int) -> str:
    if isinstance(value, float):
        return f"{value:.12g}"
    return str(value)


def _required_str(row: Mapping[str, object], name: str) -> str:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    result = str(value)
    if not result:
        raise ValueError(f"{name} is required")
    return result


def _required_int(row: Mapping[str, object], name: str) -> int:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return int(value)


def _required_float(row: Mapping[str, object], name: str) -> float:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    return float(value)


def _optional_float(row: Mapping[str, object], name: str) -> float | None:
    value = row[name]
    return None if _is_missing(value) else float(value)


def _required_bool(row: Mapping[str, object], name: str) -> bool:
    value = row[name]
    if _is_missing(value):
        raise ValueError(f"{name} is required")
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text == "true":
        return True
    if text == "false":
        return False
    raise ValueError(f"{name} must be true or false")


def _is_missing(value: object) -> bool:
    if value is None or value == "":
        return True
    return isinstance(value, float) and math.isnan(value)
