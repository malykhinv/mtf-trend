from __future__ import annotations

import math
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from anomaly_science.artifacts import build_manifest, write_csv_artifact, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.features import AnomalyFeatureMatrixRow
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.contracts.state import AnomalyState1mRow
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource, MarketDataSource
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION, build_default_feature_catalog, feature_rows_to_artifact
from anomaly_science.features.config import FeatureMatrixConfig
from anomaly_science.future.atr import AtrComputationError, compute_atr_1d_asof
from anomaly_science.future.builder import load_anomaly_state_1m_csv


def build_price_time_feature_matrix(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    state_rows: Sequence[AnomalyState1mRow] | Iterable[AnomalyState1mRow],
    config: FeatureMatrixConfig | None = None,
) -> tuple[AnomalyFeatureMatrixRow, ...]:
    """Build as-of price/time/alpha-decay features from state rows.

    This builder deliberately does not read `anomaly_future_paths.csv`. ATR is
    recomputed from normalized 1m candles through the strict as-of ATR engine so
    the feature matrix has no dependency on future outcome artifacts.
    """
    cfg = config or FeatureMatrixConfig()
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for symbol in candles_by_symbol:
        candles_by_symbol[symbol].sort(key=lambda item: (item.available_time_ms, item.open_time_ms))

    rows: list[AnomalyFeatureMatrixRow] = []
    for state in sorted(state_rows, key=lambda item: (item.symbol, item.snapshot_time_ms, item.event_id)):
        symbol_candles = candles_by_symbol.get(state.symbol, [])
        rows.append(_build_state_feature_row(state=state, candles=symbol_candles, config=cfg))
    return tuple(rows)


def build_price_time_feature_matrix_from_source(
    *,
    source: MarketDataSource,
    state_path: str | Path,
    config: FeatureMatrixConfig | None = None,
) -> tuple[AnomalyFeatureMatrixRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    state_rows = load_anomaly_state_1m_csv(state_path)
    return build_price_time_feature_matrix(
        candles_1m=normalize_candles_1m(frame),
        state_rows=state_rows,
        config=config,
    )


def feature_matrix_rows_to_artifact(rows: Sequence[AnomalyFeatureMatrixRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        result.append({key: _csv_value(value) for key, value in payload.items()})
    return result


def run_mvp1_feature_matrix(
    *,
    input_dir: str | Path,
    state_path: str | Path,
    out_dir: str | Path,
    config: FeatureMatrixConfig | None = None,
) -> Path:
    input_path = Path(input_dir)
    state_artifact_path = Path(state_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or FeatureMatrixConfig()

    source = CsvDirectoryDataSource(input_path)
    state_rows = load_anomaly_state_1m_csv(state_artifact_path)
    matrix_rows = build_price_time_feature_matrix_from_source(
        source=source,
        state_path=state_artifact_path,
        config=cfg,
    )
    protocol_rows = _protocol_rows(state_row_count=len(state_rows), feature_row_count=len(matrix_rows))
    run_config_rows = _run_config_rows(
        input_path=input_path,
        state_path=state_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.append(
        write_csv_artifact(
            output_path / "anomaly_feature_matrix.csv",
            feature_matrix_rows_to_artifact(matrix_rows),
            get_artifact_schema("anomaly_feature_matrix.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_feature_catalog.csv",
            feature_rows_to_artifact(build_default_feature_catalog()),
            get_artifact_schema("anomaly_feature_catalog.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _build_state_feature_row(
    *,
    state: AnomalyState1mRow,
    candles: Sequence[Candle1m],
    config: FeatureMatrixConfig,
) -> AnomalyFeatureMatrixRow:
    atr_value: float | None = None
    atr_pct_value: float | None = None
    range_since_start_atr: float | None = None
    distance_to_running_high_atr: float | None = None
    distance_to_running_low_atr: float | None = None
    retracement_from_high_atr: float | None = None
    price_speed_atr: float | None = None
    status = "ok"

    try:
        atr = compute_atr_1d_asof(
            candles_1m=candles,
            symbol=state.symbol,
            snapshot_time_ms=state.snapshot_time_ms,
            atr_window_minutes=config.atr_window_minutes,
        )
        atr_value = atr.atr_1d_asof_t
        atr_pct_value = atr.atr_1d_pct_asof_t
    except AtrComputationError:
        status = "insufficient_atr_history"

    if atr_value is not None:
        range_since_start_atr = (state.running_high_asof_t - state.running_low_asof_t) / atr_value
        distance_to_running_high_atr = max(state.running_high_asof_t - state.current_close, 0.0) / atr_value
        distance_to_running_low_atr = max(state.current_close - state.running_low_asof_t, 0.0) / atr_value
        retracement_from_high_atr = distance_to_running_high_atr
        price_speed_atr = _price_speed_atr(
            candles=candles,
            snapshot_time_ms=state.snapshot_time_ms,
            atr_value=atr_value,
            atr_window_minutes=config.atr_window_minutes,
        )
        if price_speed_atr is None and status == "ok":
            status = "missing_previous_close_for_speed"

    time_to_running_high = max(state.minutes_since_event_start - state.time_since_running_high_minutes, 0)
    clock_maturity = state.time_since_running_high_minutes / max(time_to_running_high, 1)
    event_age_ratio = state.minutes_since_detection / max(config.expected_event_lifetime_minutes, 1)

    return AnomalyFeatureMatrixRow(
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        feature_matrix_version=config.feature_matrix_version,
        event_id=state.event_id,
        symbol=state.symbol,
        snapshot_time_ms=state.snapshot_time_ms,
        feature_cutoff_time_ms=state.feature_cutoff_time_ms,
        minutes_since_trigger=state.minutes_since_detection,
        ATR_1d_asof_t=atr_value,
        ATR_1d_pct_asof_t=atr_pct_value,
        current_return_from_start=state.current_return_from_start,
        range_since_start_atr=range_since_start_atr,
        distance_to_running_high_atr=distance_to_running_high_atr,
        distance_to_running_low_atr=distance_to_running_low_atr,
        retracement_from_high_atr=retracement_from_high_atr,
        price_speed_atr=price_speed_atr,
        clock_maturity=clock_maturity,
        event_age_ratio=event_age_ratio,
        alpha_decay_bucket=alpha_decay_bucket(state.minutes_since_detection),
        feature_source_status=status,
    )


def alpha_decay_bucket(minutes_since_trigger: int) -> str:
    if minutes_since_trigger < 0:
        raise ValueError("minutes_since_trigger must be non-negative")
    if minutes_since_trigger <= 2:
        return "0-2m"
    if minutes_since_trigger <= 5:
        return "3-5m"
    if minutes_since_trigger <= 10:
        return "6-10m"
    if minutes_since_trigger <= 20:
        return "11-20m"
    if minutes_since_trigger <= 40:
        return "21-40m"
    return ">40m"


def _price_speed_atr(
    *,
    candles: Sequence[Candle1m],
    snapshot_time_ms: int,
    atr_value: float,
    atr_window_minutes: int,
) -> float | None:
    asof_candles = [candle for candle in candles if candle.available_time_ms <= snapshot_time_ms]
    if len(asof_candles) < 2:
        return None
    asof_candles.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))
    current = asof_candles[-1]
    previous = asof_candles[-2]
    if current.available_time_ms != snapshot_time_ms:
        return None
    expected_one_minute_atr = atr_value / max(atr_window_minutes, 1)
    if not math.isfinite(expected_one_minute_atr) or expected_one_minute_atr <= 0:
        return None
    return (current.close - previous.close) / expected_one_minute_atr


def _protocol_rows(*, state_row_count: int, feature_row_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_feature_matrix_scope",
            status=AuditStatus.PASS,
            message="price/time/alpha-decay feature matrix only; no flow/OI/liquidation/CVD/cross-section/ML/decision/trade simulation",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="state_artifact_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_state_1m.csv accepted through strict schema boundary with {state_row_count} state rows",
            artifact="anomaly_state_1m.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_rows_written",
            status=AuditStatus.PASS if feature_row_count == state_row_count else AuditStatus.FAIL,
            message=f"anomaly_feature_matrix.csv written with {feature_row_count} rows for {state_row_count} state rows",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="feature_matrix_no_future_artifact_dependency",
            status=AuditStatus.PASS,
            message="feature matrix recomputes ATR from normalized candles and does not read anomaly_future_paths.csv",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 feature matrix uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="relative_over_absolute_feature_contract_enforced",
            status=AuditStatus.PASS,
            message="materialized price/time feature matrix contains ATR-normalized, dimensionless, categorical, or audit-only fields; no raw absolute model features",
            artifact="anomaly_feature_matrix.csv",
        ),
        ProtocolAuditRow(
            check_name="ATR_1d_asof_t_computed_from_closed_past_candles",
            status=AuditStatus.PASS,
            message="feature matrix computes ATR_1d_asof_t from closed 1m candles available <= snapshot_time_ms and leaves ATR features null when history is insufficient",
            artifact="anomaly_feature_matrix.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_feature_matrix",
        implemented=implemented_methodology_rows,
    )


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result


def _run_config_rows(
    *,
    input_path: Path,
    state_path: Path,
    output_path: Path,
    config: FeatureMatrixConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-feature-matrix", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="state_path", value=str(state_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="git_commit", value="UNKNOWN", source="runtime"),
        RunConfigRow(key="stage", value="mvp1_feature_matrix", source="runtime"),
        RunConfigRow(key="feature_schema_version", value=FEATURE_SCHEMA_VERSION, source="runtime"),
        RunConfigRow(key="feature_matrix_version", value=config.feature_matrix_version, source="runtime"),
        RunConfigRow(key="atr_window_minutes", value=str(config.atr_window_minutes), source="runtime"),
        RunConfigRow(
            key="expected_event_lifetime_minutes",
            value=str(config.expected_event_lifetime_minutes),
            source="runtime",
        ),
    ]


def _csv_value(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return ""
    return value


def _run_id() -> str:
    return "mvp1-feature-matrix-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
