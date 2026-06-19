from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Mapping, Sequence

from anomaly_science.artifacts.writer import write_csv_artifact_with_aliases
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.rejection import REJECTION_FUNNEL_VERSION, RejectionFunnelRow


_STATUS_INCLUDED = "INCLUDED"
_STATUS_EXCLUDED = "EXCLUDED"
_STATUS_SKIPPED = "SKIPPED"
_EMPTY = ""


class RejectionFunnelError(ValueError):
    """Raised when already-written research artifacts cannot be converted to a funnel."""


def write_rejection_funnel(*, run_dir: str | Path, out_dir: str | Path | None = None) -> Path:
    """Build and write the strategy-neutral rejection funnel for a full research run.

    The funnel is artifact-driven: it reads stage outputs already produced by the
    pipeline and records where rows/events remain included or become explicitly
    excluded. It does not rerun models, detectors, thresholds, or strategy logic.
    """
    root = Path(run_dir)
    output_path = Path(out_dir) if out_dir is not None else root / "stages" / "rejection_funnel"
    output_path.mkdir(parents=True, exist_ok=True)
    rows = build_rejection_funnel_rows(root)
    written = write_csv_artifact_with_aliases(
        output_path / "strategy_rejection_funnel.csv",
        [asdict(row) for row in rows],
        get_artifact_schema("strategy_rejection_funnel.csv"),
    )
    return written[0].parent


def build_rejection_funnel_rows(run_dir: str | Path) -> tuple[RejectionFunnelRow, ...]:
    root = Path(run_dir)
    if not root.exists() or not root.is_dir():
        raise RejectionFunnelError(f"run_dir must be an existing directory: {root}")

    run_config = _run_config(root / "strategy_run_config.csv")
    context = _Context(
        run_id=root.name,
        strategy_name=run_config.get("strategy_name", _EMPTY),
        strategy_version=run_config.get("strategy_version", _EMPTY),
        strategy_contract_version=run_config.get("strategy_contract_version", _EMPTY),
        target_horizon_minutes=run_config.get("target_horizon_minutes", _EMPTY),
    )

    events_path = root / "stages" / "events" / "strategy_events.csv"
    state_path = root / "stages" / "state" / "strategy_state_1m.csv"
    future_path = root / "stages" / "future" / "strategy_future_paths.csv"
    labels_path = root / "stages" / "labels" / "strategy_outcome_labels.csv"
    predictions_path = root / "stages" / "prediction" / "strategy_oos_predictions.csv"
    decision_path = root / "stages" / "expected_value" / "strategy_decision_timing.csv"
    if not decision_path.exists():
        decision_path = root / "stages" / "decision" / "strategy_decision_timing.csv"
    simulation_path = root / "stages" / "simulation" / "strategy_trade_simulation.csv"

    event_rows = _read_csv(events_path)
    state_rows = _read_csv(state_path)
    future_rows = _read_csv(future_path)
    label_rows = _read_csv(labels_path)
    prediction_rows = _read_csv(predictions_path)
    decision_rows = _read_csv(decision_path)
    simulation_rows = _read_csv(simulation_path)

    rows: list[RejectionFunnelRow] = []
    rows.extend(_data_quality_rows(root=root, context=context))
    rows.extend(_universe_rows(root=root, context=context))
    rows.extend(_event_rows(context=context, event_rows=event_rows))
    rows.extend(_state_rows(context=context, event_rows=event_rows, state_rows=state_rows))
    rows.extend(_future_rows(context=context, state_rows=state_rows, future_rows=future_rows))
    rows.extend(_label_rows(context=context, label_rows=label_rows))
    rows.extend(_prediction_rows(context=context, label_rows=label_rows, prediction_rows=prediction_rows))
    rows.extend(_decision_rows(context=context, prediction_rows=prediction_rows, decision_rows=decision_rows))
    rows.extend(_simulation_rows(context=context, decision_rows=decision_rows, simulation_rows=simulation_rows))
    rows.extend(_empty_stage_placeholder_rows(context=context, rows=rows))
    rows.extend(_summary_rows(context=context, rows=rows))
    return tuple(rows)


class _Context:
    def __init__(
        self,
        *,
        run_id: str,
        strategy_name: str,
        strategy_version: str,
        strategy_contract_version: str,
        target_horizon_minutes: str,
    ) -> None:
        self.run_id = run_id
        self.strategy_name = strategy_name
        self.strategy_version = strategy_version
        self.strategy_contract_version = strategy_contract_version
        self.target_horizon_minutes = target_horizon_minutes

    def row(
        self,
        *,
        stage: str,
        source_artifact: str,
        row_key: str,
        status: str,
        reason_code: str = _EMPTY,
        reason_detail: str = _EMPTY,
        upstream_stage: str = _EMPTY,
        downstream_stage: str = _EMPTY,
        event_id: str = _EMPTY,
        symbol: str = _EMPTY,
        snapshot_time_ms: str | int = _EMPTY,
        row_count: int = 1,
    ) -> RejectionFunnelRow:
        return RejectionFunnelRow(
            funnel_version=REJECTION_FUNNEL_VERSION,
            run_id=self.run_id,
            strategy_name=self.strategy_name,
            strategy_version=self.strategy_version,
            strategy_contract_version=self.strategy_contract_version,
            target_horizon_minutes=self.target_horizon_minutes,
            stage=stage,
            source_artifact=source_artifact,
            row_key=row_key,
            event_id=event_id,
            symbol=symbol,
            snapshot_time_ms=snapshot_time_ms,
            status=status,
            reason_code=reason_code,
            reason_detail=reason_detail,
            upstream_stage=upstream_stage,
            downstream_stage=downstream_stage,
            row_count=row_count,
        )


def _data_quality_rows(*, root: Path, context: _Context) -> list[RejectionFunnelRow]:
    path = root / "stages" / "events" / "strategy_data_quality.csv"
    result: list[RejectionFunnelRow] = []
    for index, row in enumerate(_read_csv(path), start=2):
        affected = _to_int(row.get("affected_rows")) or 0
        status_value = row.get("status", _EMPTY)
        excluded = _truthy(row.get("excluded_from_detector")) or _truthy(row.get("excluded_from_ml_dataset")) or status_value == "FAIL"
        result.append(
            context.row(
                stage="data_quality",
                source_artifact="strategy_data_quality.csv",
                row_key=f"strategy_data_quality.csv:{index}:{row.get('check_name', _EMPTY)}",
                status=_STATUS_EXCLUDED if excluded else _STATUS_INCLUDED,
                reason_code=_reason_code(row.get("reason") or row.get("check_name") or "data_quality_condition") if excluded else _EMPTY,
                reason_detail=row.get("message", _EMPTY),
                downstream_stage="events",
                row_count=affected,
            )
        )
    return result


def _universe_rows(*, root: Path, context: _Context) -> list[RejectionFunnelRow]:
    path = root / "stages" / "events" / "symbol_universe_by_day.csv"
    result: list[RejectionFunnelRow] = []
    for index, row in enumerate(_read_csv(path), start=2):
        reason = row.get("reason_if_excluded", _EMPTY)
        result.append(
            context.row(
                stage="point_in_time_universe",
                source_artifact="symbol_universe_by_day.csv",
                row_key=f"{row.get('trade_date', _EMPTY)}|{row.get('symbol', _EMPTY)}",
                status=_STATUS_EXCLUDED if reason else _STATUS_INCLUDED,
                reason_code=_reason_code(reason) if reason else _EMPTY,
                reason_detail=reason,
                downstream_stage="events",
                symbol=row.get("symbol", _EMPTY),
                row_count=1,
            )
        )
    return result


def _event_rows(*, context: _Context, event_rows: Sequence[Mapping[str, str]]) -> list[RejectionFunnelRow]:
    result: list[RejectionFunnelRow] = []
    for row in event_rows:
        excluded = _truthy(row.get("technical_noise_shock")) or _truthy(row.get("excluded_by_data_quality_gate"))
        components = row.get("trigger_components") or row.get("trigger_component") or "strategy_trigger"
        result.append(
            context.row(
                stage="events",
                source_artifact="strategy_events.csv",
                row_key=_event_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("state_time_ms", _EMPTY),
                status=_STATUS_EXCLUDED if excluded else _STATUS_INCLUDED,
                reason_code="data_quality_fail" if excluded else _EMPTY,
                reason_detail=("event excluded by technical/data-quality gate" if excluded else components),
                upstream_stage="point_in_time_universe",
                downstream_stage="state",
            )
        )
    return result


def _state_rows(
    *,
    context: _Context,
    event_rows: Sequence[Mapping[str, str]],
    state_rows: Sequence[Mapping[str, str]],
) -> list[RejectionFunnelRow]:
    state_event_ids = {row.get("event_id", _EMPTY) for row in state_rows if row.get("event_id", _EMPTY)}
    result: list[RejectionFunnelRow] = []
    for event in event_rows:
        event_id = event.get("event_id", _EMPTY)
        if event_id and event_id not in state_event_ids:
            result.append(
                context.row(
                    stage="state",
                    source_artifact="strategy_state_1m.csv",
                    row_key=_event_key(event),
                    event_id=event_id,
                    symbol=event.get("symbol", _EMPTY),
                    snapshot_time_ms=event.get("state_time_ms", _EMPTY),
                    status=_STATUS_EXCLUDED,
                    reason_code="outside_strategy_lifecycle",
                    reason_detail="event produced no online state rows",
                    upstream_stage="events",
                    downstream_stage="future",
                )
            )
    for row in state_rows:
        result.append(
            context.row(
                stage="state",
                source_artifact="strategy_state_1m.csv",
                row_key=_state_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_INCLUDED,
                upstream_stage="events",
                downstream_stage="future",
            )
        )
    return result


def _future_rows(
    *,
    context: _Context,
    state_rows: Sequence[Mapping[str, str]],
    future_rows: Sequence[Mapping[str, str]],
) -> list[RejectionFunnelRow]:
    future_keys = {_join_key(row) for row in future_rows}
    result: list[RejectionFunnelRow] = []
    for state in state_rows:
        key = _join_key(state)
        if key not in future_keys:
            result.append(
                context.row(
                    stage="future_path",
                    source_artifact="strategy_future_paths.csv",
                    row_key=key,
                    event_id=state.get("event_id", _EMPTY),
                    symbol=state.get("symbol", _EMPTY),
                    snapshot_time_ms=state.get("snapshot_time_ms", _EMPTY),
                    status=_STATUS_EXCLUDED,
                    reason_code="future_path_incomplete",
                    reason_detail="state row has no matching future path row",
                    upstream_stage="state",
                    downstream_stage="labels",
                )
            )
    for row in future_rows:
        result.append(
            context.row(
                stage="future_path",
                source_artifact="strategy_future_paths.csv",
                row_key=_join_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_INCLUDED,
                upstream_stage="state",
                downstream_stage="labels",
            )
        )
    return result


def _label_rows(*, context: _Context, label_rows: Sequence[Mapping[str, str]]) -> list[RejectionFunnelRow]:
    result: list[RejectionFunnelRow] = []
    horizon = str(context.target_horizon_minutes)
    available_field = f"label_available_{horizon}m" if horizon else _EMPTY
    scenario_field = f"scenario_{horizon}m" if horizon else _EMPTY
    for row in label_rows:
        available = _truthy(row.get(available_field)) if available_field else False
        scenario = row.get(scenario_field, _EMPTY) if scenario_field else _EMPTY
        excluded = (not available) or scenario == "missing_future"
        result.append(
            context.row(
                stage="labels",
                source_artifact="strategy_outcome_labels.csv",
                row_key=_join_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_EXCLUDED if excluded else _STATUS_INCLUDED,
                reason_code="horizon_not_available" if excluded else _EMPTY,
                reason_detail=f"{scenario_field}={scenario}; {available_field}={row.get(available_field, _EMPTY)}" if excluded else scenario,
                upstream_stage="future_path",
                downstream_stage="prediction",
            )
        )
    return result


def _prediction_rows(
    *,
    context: _Context,
    label_rows: Sequence[Mapping[str, str]],
    prediction_rows: Sequence[Mapping[str, str]],
) -> list[RejectionFunnelRow]:
    prediction_keys = {_join_key(row) for row in prediction_rows}
    label_included = [row for row in label_rows if _label_is_available(row, context.target_horizon_minutes)]
    result: list[RejectionFunnelRow] = []
    for label in label_included:
        key = _join_key(label)
        if key not in prediction_keys:
            result.append(
                context.row(
                    stage="prediction",
                    source_artifact="strategy_oos_predictions.csv",
                    row_key=key,
                    event_id=label.get("event_id", _EMPTY),
                    symbol=label.get("symbol", _EMPTY),
                    snapshot_time_ms=label.get("snapshot_time_ms", _EMPTY),
                    status=_STATUS_EXCLUDED,
                    reason_code="not_in_oos_window_or_insufficient_training",
                    reason_detail="available label row has no OOS prediction row",
                    upstream_stage="labels",
                    downstream_stage="decision",
                )
            )
    for row in prediction_rows:
        result.append(
            context.row(
                stage="prediction",
                source_artifact="strategy_oos_predictions.csv",
                row_key=_join_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_INCLUDED,
                upstream_stage="labels",
                downstream_stage="decision",
            )
        )
    return result


def _decision_rows(
    *,
    context: _Context,
    prediction_rows: Sequence[Mapping[str, str]],
    decision_rows: Sequence[Mapping[str, str]],
) -> list[RejectionFunnelRow]:
    decision_keys = {_join_key(row) for row in decision_rows}
    result: list[RejectionFunnelRow] = []
    for prediction in prediction_rows:
        key = _join_key(prediction)
        if key not in decision_keys:
            result.append(
                context.row(
                    stage="decision",
                    source_artifact="strategy_decision_timing.csv",
                    row_key=key,
                    event_id=prediction.get("event_id", _EMPTY),
                    symbol=prediction.get("symbol", _EMPTY),
                    snapshot_time_ms=prediction.get("snapshot_time_ms", _EMPTY),
                    status=_STATUS_EXCLUDED,
                    reason_code="decision_timing_missing",
                    reason_detail="prediction row has no EV/decision timing row",
                    upstream_stage="prediction",
                    downstream_stage="simulation",
                )
            )
    for row in decision_rows:
        action = row.get("best_action", _EMPTY)
        actionable = action in {"long", "short"}
        result.append(
            context.row(
                stage="decision",
                source_artifact="strategy_decision_timing.csv",
                row_key=_join_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_INCLUDED if actionable else _STATUS_EXCLUDED,
                reason_code=_EMPTY if actionable else "EV_no_trade_or_wait",
                reason_detail=f"best_action={action}",
                upstream_stage="prediction",
                downstream_stage="simulation",
            )
        )
    return result


def _simulation_rows(
    *,
    context: _Context,
    decision_rows: Sequence[Mapping[str, str]],
    simulation_rows: Sequence[Mapping[str, str]],
) -> list[RejectionFunnelRow]:
    simulation_keys = {_join_key(row) for row in simulation_rows}
    result: list[RejectionFunnelRow] = []
    for decision in decision_rows:
        key = _join_key(decision)
        action = decision.get("best_action", _EMPTY)
        if key not in simulation_keys:
            reason_code = "EV_no_trade_or_wait" if action not in {"long", "short"} else "simulation_skipped_by_execution_gate"
            result.append(
                context.row(
                    stage="simulation",
                    source_artifact="strategy_trade_simulation.csv",
                    row_key=key,
                    event_id=decision.get("event_id", _EMPTY),
                    symbol=decision.get("symbol", _EMPTY),
                    snapshot_time_ms=decision.get("snapshot_time_ms", _EMPTY),
                    status=_STATUS_EXCLUDED,
                    reason_code=reason_code,
                    reason_detail=f"decision best_action={action} has no simulated trade row",
                    upstream_stage="decision",
                    downstream_stage="final_artifacts",
                )
            )
    for row in simulation_rows:
        result.append(
            context.row(
                stage="simulation",
                source_artifact="strategy_trade_simulation.csv",
                row_key=_join_key(row),
                event_id=row.get("event_id", _EMPTY),
                symbol=row.get("symbol", _EMPTY),
                snapshot_time_ms=row.get("snapshot_time_ms", _EMPTY),
                status=_STATUS_INCLUDED,
                reason_detail=row.get("exit_reason", _EMPTY),
                upstream_stage="decision",
                downstream_stage="final_artifacts",
            )
        )
    return result


def _summary_rows(*, context: _Context, rows: Sequence[RejectionFunnelRow]) -> list[RejectionFunnelRow]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in rows:
        key = (row.stage, row.status, row.reason_code)
        counts[key] = counts.get(key, 0) + max(1, row.row_count)
    result: list[RejectionFunnelRow] = []
    for (stage, status, reason_code), count in sorted(counts.items()):
        result.append(
            context.row(
                stage=f"summary:{stage}",
                source_artifact="strategy_rejection_funnel.csv",
                row_key=f"{stage}|{status}|{reason_code}",
                status=_STATUS_SKIPPED,
                reason_code=reason_code,
                reason_detail=f"aggregate {status} row_count for {stage}",
                row_count=count,
            )
        )
    return result


def _empty_stage_placeholder_rows(*, context: _Context, rows: Sequence[RejectionFunnelRow]) -> list[RejectionFunnelRow]:
    required = {
        "data_quality": "strategy_data_quality.csv",
        "point_in_time_universe": "symbol_universe_by_day.csv",
        "events": "strategy_events.csv",
        "state": "strategy_state_1m.csv",
        "future_path": "strategy_future_paths.csv",
        "labels": "strategy_outcome_labels.csv",
        "prediction": "strategy_oos_predictions.csv",
        "decision": "strategy_decision_timing.csv",
        "simulation": "strategy_trade_simulation.csv",
    }
    present = {row.stage for row in rows}
    result: list[RejectionFunnelRow] = []
    for stage, artifact in sorted(required.items()):
        if stage in present:
            continue
        result.append(
            context.row(
                stage=stage,
                source_artifact=artifact,
                row_key=f"{stage}|no_rows",
                status=_STATUS_SKIPPED,
                reason_code="no_rows_for_stage",
                reason_detail="stage artifact produced no row-level records in this run",
                row_count=0,
            )
        )
    return result


def _run_config(path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in _read_csv(path):
        key = row.get("key", _EMPTY)
        if key:
            result[key] = row.get("value", _EMPTY)
    return result


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file_obj:
        return [dict(row) for row in csv.DictReader(file_obj)]


def _truthy(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _to_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _reason_code(value: object) -> str:
    text = str(value or "unknown").strip().lower()
    out = []
    for char in text:
        out.append(char if char.isalnum() else "_")
    code = "".join(out).strip("_")
    while "__" in code:
        code = code.replace("__", "_")
    return code or "unknown"


def _event_key(row: Mapping[str, str]) -> str:
    return "|".join(
        (
            row.get("event_id", _EMPTY),
            row.get("symbol", _EMPTY),
            row.get("state_time_ms", row.get("snapshot_time_ms", _EMPTY)),
        )
    )


def _state_key(row: Mapping[str, str]) -> str:
    return _join_key(row)


def _join_key(row: Mapping[str, str]) -> str:
    return "|".join(
        (
            row.get("event_id", _EMPTY),
            row.get("symbol", _EMPTY),
            row.get("snapshot_time_ms", _EMPTY),
            row.get("feature_cutoff_time_ms", _EMPTY),
        )
    )


def _label_is_available(row: Mapping[str, str], horizon: str | int) -> bool:
    horizon_text = str(horizon)
    available_field = f"label_available_{horizon_text}m"
    scenario_field = f"scenario_{horizon_text}m"
    return _truthy(row.get(available_field)) and row.get(scenario_field, _EMPTY) != "missing_future"
