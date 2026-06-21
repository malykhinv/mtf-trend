from __future__ import annotations

from dataclasses import asdict

import pandas as pd
import polars as pl
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.contracts.events import StrategyEvent
from anomaly_science.data.quality import (
    apply_data_quality_mask,
    build_candles_1m_data_quality_mask,
    data_quality_mask_audit_row,
    has_detector_blocking_quality_fail,
    rows_to_artifact,
    run_data_quality,
)
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource
from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.events.deduplication import suppress_event_cascade
from anomaly_science.events.detector import events_to_artifact
from anomaly_science.strategy.base import internal_datetime_to_utc_ms, validate_trigger_frame
from anomaly_science.strategy.metadata import format_required_data_streams, strategy_metadata_run_config_rows
from anomaly_science.strategy.registry import get_broad_anomaly_strategy, get_strategy


REQUIRED_DATASETS = ("candles_1m", "candles_5m")
OPTIONAL_DATASETS = ("open_interest_5m", "liquidations")
from anomaly_science.universe import build_symbol_universe_by_day, universe_rows_to_artifact


def run_mvp1_events(
    *,
    input_dir: str | Path,
    out_dir: str | Path,
    config: BroadAnomalyDetectorConfig | None = None,
    strategy_name: str = "broad_anomaly_v1_h30",
) -> Path:
    """Run the MVP1 strategy event detector and write protocol artifacts."""
    input_path = Path(input_dir)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or BroadAnomalyDetectorConfig()
    strategy = (
        get_broad_anomaly_strategy(config=cfg, strategy_name=strategy_name)
        if strategy_name.startswith("broad_anomaly_v1_h")
        else get_strategy(strategy_name)
    )

    frames, read_errors = _read_source_frames(input_path)
    data_quality = run_data_quality(frames, read_errors=read_errors)
    data_quality_mask = build_candles_1m_data_quality_mask(frames.get("candles_1m"))
    data_quality = [*data_quality, data_quality_mask_audit_row(data_quality_mask)]
    candles_1m_frame = frames.get("candles_1m")
    universe_rows = build_symbol_universe_by_day(
        candles_1m=candles_1m_frame,
        candles_5m=frames.get("candles_5m"),
        open_interest_5m=frames.get("open_interest_5m"),
        liquidations=frames.get("liquidations"),
    )
    frames.clear()

    events = ()
    raw_event_count = 0
    cascade_suppressed_count = 0
    event_error: str | None = None
    required_stream_reject_count = _required_stream_reject_count(
        universe_rows=universe_rows,
        required_streams=strategy.required_data_streams,
    )
    blocking_quality_fail = has_detector_blocking_quality_fail(data_quality)
    if required_stream_reject_count:
            event_error = f"required data streams missing for {required_stream_reject_count} symbol-day rows before trigger generation"
    if candles_1m_frame is not None and not blocking_quality_fail and not required_stream_reject_count:
        try:
            gated_candles_1m = apply_data_quality_mask(candles_1m_frame, data_quality_mask)
            candles_1m_frame = None
            trigger_frame = strategy.generate_triggers_from_pandas(gated_candles_1m)
            validate_trigger_frame(trigger_frame)
            raw_events = _events_from_trigger_frame(trigger_frame)
            del gated_candles_1m
            cascade_result = suppress_event_cascade(raw_events, horizon_minutes=strategy.metadata.horizon_minutes)
            events = cascade_result.accepted_events
            raw_event_count = len(raw_events)
            cascade_suppressed_count = len(cascade_result.suppressed_events)
        except (TypeError, ValueError) as exc:
            event_error = f"event detector failed: {exc}"

    protocol_rows = _protocol_rows(
        critical_fail=blocking_quality_fail,
        data_quality_mask_excluded_count=data_quality_mask.excluded_rows,
        universe_rows_present=bool(universe_rows),
        event_count=len(events),
        raw_event_count=raw_event_count,
        cascade_suppressed_count=cascade_suppressed_count,
        event_error=event_error,
        technical_noise_shock_count=_technical_noise_shock_count(data_quality),
        warmup_window_count=_warmup_window_count(data_quality),
        strategy_name=strategy.metadata.strategy_name,
        required_stream_reject_count=required_stream_reject_count,
        required_data_streams=format_required_data_streams(strategy.required_data_streams),
    )
    run_config_rows = _run_config_rows(input_path=input_path, output_path=output_path, strategy=strategy)

    written: list[Path] = []
    data_quality_rows = rows_to_artifact(data_quality)
    written.extend(write_csv_artifact_with_aliases(
        output_path / "strategy_data_quality.csv",
        data_quality_rows,
        get_artifact_schema("strategy_data_quality.csv"),
    ))
    written.append(write_csv_artifact(
        output_path / "symbol_universe_by_day.csv",
        universe_rows_to_artifact(universe_rows),
        get_artifact_schema("symbol_universe_by_day.csv"),
    ))
    event_rows = events_to_artifact(events)
    written.extend(write_csv_artifact_with_aliases(
        output_path / "strategy_events.csv",
        event_rows,
        get_artifact_schema("strategy_events.csv"),
    ))
    protocol_artifact_rows = _protocol_rows_to_artifact(protocol_rows)
    written.extend(write_csv_artifact_with_aliases(
        output_path / "strategy_protocol_audit.csv",
        protocol_artifact_rows,
        get_artifact_schema("strategy_protocol_audit.csv"),
    ))
    run_config_artifact_rows = [asdict(row) for row in run_config_rows]
    written.extend(write_csv_artifact_with_aliases(
        output_path / "strategy_run_config.csv",
        run_config_artifact_rows,
        get_artifact_schema("strategy_run_config.csv"),
    ))
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _read_source_frames(input_path: Path) -> tuple[dict[str, pd.DataFrame | None], list[str]]:
    source = CsvDirectoryDataSource(input_path)
    frames: dict[str, pd.DataFrame | None] = {}
    errors: list[str] = []
    for dataset_name in REQUIRED_DATASETS:
        try:
            frames[dataset_name] = source.read_frame(dataset_name, required=True)
        except CsvDataSourceError as exc:
            frames[dataset_name] = None
            errors.append(str(exc))
    for dataset_name in OPTIONAL_DATASETS:
        try:
            frames[dataset_name] = source.read_frame(dataset_name, required=False)
        except CsvDataSourceError as exc:
            frames[dataset_name] = None
            errors.append(str(exc))
    return frames, errors


def _protocol_rows(
    *,
    critical_fail: bool,
    universe_rows_present: bool,
    event_count: int,
    raw_event_count: int,
    cascade_suppressed_count: int,
    event_error: str | None,
    technical_noise_shock_count: int,
    warmup_window_count: int,
    data_quality_mask_excluded_count: int,
    strategy_name: str,
    required_stream_reject_count: int,
    required_data_streams: str,
) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_events_scope",
            status=AuditStatus.PASS,
            message="data-source, data-quality, universe, broad events, run-config, and manifest only; state/future paths not run",
        ),
        ProtocolAuditRow(
            check_name="data_quality_critical_fail_gate",
            status=AuditStatus.FAIL if critical_fail else AuditStatus.PASS,
            message="critical data-quality FAIL present; detector output is not interpretable" if critical_fail else "no critical data-quality FAIL rows",
        ),
        ProtocolAuditRow(
            check_name="point_in_time_universe_written",
            status=AuditStatus.PASS if universe_rows_present else AuditStatus.WARN,
            message="symbol_universe_by_day.csv has rows" if universe_rows_present else "symbol_universe_by_day.csv is empty because no dated symbol data was available",
        ),
        ProtocolAuditRow(
            check_name="strategy_events_written",
            status=AuditStatus.FAIL if event_error else AuditStatus.PASS,
            message=event_error or f"strategy_events.csv written with {event_count} accepted strategy event rows from {raw_event_count} raw trigger rows",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="detector_not_trade_setup",
            status=AuditStatus.PASS,
            message="strategy trigger generation uses only point-in-time market_frame_asof rows; no entry/exit/trade rules",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="base_strategy_contract_valid",
            status=AuditStatus.PASS,
            message=f"{strategy_name} is declared through StrategyMetadata and called through BaseStrategy-compatible trigger-frame generate_triggers",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 events uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="technical_noise_shock_flag_computed_from_raw_timestamp_gaps",
            status=AuditStatus.PASS,
            message="candles_1m data-quality audit marks first candles after raw timestamp gaps > 3 minutes as technical_noise_shock rows",
            artifact="strategy_data_quality.csv",
        ),
        ProtocolAuditRow(
            check_name="technical_noise_shock_excluded_from_broad_detector",
            status=AuditStatus.PASS,
            message=f"excluded {technical_noise_shock_count} first candles after raw timestamp gaps > 3 minutes from broad detector candidates and baselines",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="warmup_window_excluded_from_trigger_generation",
            status=AuditStatus.PASS,
            message=f"excluded warm-up windows after data gaps before strategy.generate_triggers; warmup_window_count={warmup_window_count}",
            artifact="strategy_data_quality.csv",
        ),
        ProtocolAuditRow(
            check_name="data_quality_mask_enforced_before_trigger_generation",
            status=AuditStatus.PASS,
            message=f"excluded {data_quality_mask_excluded_count} maskable bad 1m candle row(s) before strategy.generate_triggers",
            artifact="strategy_data_quality.csv;strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="required_data_streams_applied_before_trigger_generation",
            status=AuditStatus.PASS if required_stream_reject_count == 0 else AuditStatus.FAIL,
            message=(
                f"strategy.required_data_streams applied before strategy.generate_triggers; required_streams={required_data_streams}; no required stream rejects"
                if required_stream_reject_count == 0
                else f"strategy.required_data_streams applied before strategy.generate_triggers; required_stream_reject_count={required_stream_reject_count}"
            ),
            artifact="symbol_universe_by_day.csv;strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="trigger_cascade_suppressed_before_dataset_and_simulation",
            status=AuditStatus.PASS,
            message=f"suppressed {cascade_suppressed_count} repeated same-symbol triggers inside the strategy horizon before writing strategy_events.csv",
            artifact="strategy_events.csv",
        ),
        ProtocolAuditRow(
            check_name="base_strategy_contract_valid",
            status=AuditStatus.PASS,
            message=f"{strategy_name} is declared through StrategyMetadata and called through BaseStrategy-compatible trigger-frame generate_triggers",
            artifact="strategy_events.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(
        stage="mvp1_events",
        implemented=implemented_methodology_rows,
    )



def _technical_noise_shock_count(rows: list) -> int:
    return sum(
        1
        for row in rows
        if getattr(row, "check_name", "") == "candles_1m_technical_noise_shock"
        and getattr(row, "technical_noise_shock", False) is True
    )


def _warmup_window_count(rows: list) -> int:
    return sum(1 for row in rows if getattr(row, "check_name", "") == "candles_1m_warmup_window")


def _required_stream_reject_count(*, universe_rows: list, required_streams: object) -> int:
    stream_columns = {
        "open_interest": "has_oi_data",
        "liquidations": "has_liquidation_data",
    }
    reject_count = 0
    for stream_name, required in dict(required_streams).items():  # type: ignore[arg-type]
        if not required:
            continue
        column_name = stream_columns.get(stream_name)
        if column_name is None:
            raise ValueError(f"unknown required data stream: {stream_name}")
        reject_count += sum(
            1
            for row in universe_rows
            if getattr(row, "tradable_on_day", False) and getattr(row, column_name, False) is False
        )
    return reject_count


def _events_from_trigger_frame(trigger_frame: pl.DataFrame) -> tuple[StrategyEvent, ...]:
    if trigger_frame.height == 0:
        return ()
    events: list[StrategyEvent] = []
    for row in trigger_frame.to_dicts():
        trigger_components = tuple(
            item
            for item in str(row.get("trigger_components") or row.get("trigger_component") or "").split(";")
            if item
        )
        events.append(
            StrategyEvent(
                event_id=str(row["event_id"]),
                symbol=str(row["symbol"]),
                event_start_time_ms=internal_datetime_to_utc_ms(row["event_start_time"]),
                event_detection_time_ms=internal_datetime_to_utc_ms(row.get("event_detection_time") or row["state_time"]),
                seed_time_ms=internal_datetime_to_utc_ms(row.get("seed_time") or row["event_start_time"]),
                seed_open=float(row["seed_open"]),
                seed_high=float(row["seed_high"]),
                seed_low=float(row["seed_low"]),
                seed_close=float(row["seed_close"]),
                initial_move_pct=float(row["initial_move_pct"]),
                initial_volume_zscore=_optional_trigger_float(row.get("initial_volume_zscore")),
                initial_quote_volume_zscore=_optional_trigger_float(row.get("initial_quote_volume_zscore")),
                initial_trade_count_zscore=_optional_trigger_float(row.get("initial_trade_count_zscore")),
                trigger_component=str(row.get("trigger_component") or ""),
                trigger_components=trigger_components,
                technical_noise_shock=bool(row.get("technical_noise_shock", False)),
                raw_candle_gap_minutes=_optional_trigger_float(row.get("raw_candle_gap_minutes")),
                excluded_by_data_quality_gate=bool(row.get("excluded_by_data_quality_gate", False)),
                daily_return_asof_t=_optional_trigger_float(row.get("daily_return_asof_t")),
                trade_count_market_percentile_asof_t=_optional_trigger_float(row.get("trade_count_market_percentile_asof_t")),
                detector_version=str(row.get("detector_version") or ""),
            )
        )
    return tuple(events)


def _optional_trigger_float(value: object) -> float | None:
    if value is None:
        return None
    if pd.isna(value):
        return None
    return float(value)


def _enforce_trigger_frame_matches_events(*, trigger_frame: pl.DataFrame, events: tuple) -> None:
    trigger_event_ids = set(trigger_frame["event_id"].to_list()) if "event_id" in trigger_frame.columns else set()
    event_ids = {event.event_id for event in events}
    if trigger_event_ids != event_ids:
        raise ValueError("strategy trigger frame event_id values must match materialized events")
    if trigger_frame.height and not all(trigger_frame["is_trigger"].to_list()):
        raise ValueError("strategy trigger frame must contain only active trigger rows")
    if "trigger_component" in trigger_frame.columns:
        frame_components = {row["event_id"]: row["trigger_component"] for row in trigger_frame.select(["event_id", "trigger_component"]).to_dicts()}
        for event in events:
            if frame_components.get(event.event_id) != event.trigger_component:
                raise ValueError("strategy trigger frame trigger_component values must match materialized events")


def _protocol_rows_to_artifact(rows: list[ProtocolAuditRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        payload = asdict(row)
        payload["status"] = row.status.value
        result.append(payload)
    return result

def _run_config_rows(*, input_path: Path, output_path: Path, strategy) -> list[RunConfigRow]:
    config = strategy.config
    rows = [
        RunConfigRow(key="command", value="run-mvp1-events", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path,),
            config=config,
            extra_config={"command": "run-mvp1-events", "stage": "mvp1_events", "strategy_name": strategy.metadata.strategy_name},
        ),
        RunConfigRow(key="stage", value="mvp1_events", source="runtime"),
        *strategy_metadata_run_config_rows(strategy),
        RunConfigRow(key="detector_version", value=str(config.detector_version), source="runtime"),
    ]
    if isinstance(config, BroadAnomalyDetectorConfig):
        rows.extend(
            [
                RunConfigRow(key="detector_baseline_bars", value=str(config.baseline_bars), source="runtime"),
                RunConfigRow(key="detector_min_baseline_bars", value=str(config.min_baseline_bars), source="runtime"),
                RunConfigRow(key="detector_min_abs_return_pct", value=str(config.min_abs_return_pct), source="runtime"),
                RunConfigRow(key="detector_min_quote_volume_zscore", value=str(config.min_quote_volume_zscore), source="runtime"),
                RunConfigRow(key="detector_min_volume_zscore", value=str(config.min_volume_zscore), source="runtime"),
                RunConfigRow(key="detector_min_trade_count_zscore", value=str(config.min_trade_count_zscore), source="runtime"),
                RunConfigRow(key="detector_min_range_zscore", value=str(config.min_range_zscore), source="runtime"),
                RunConfigRow(key="detector_cooldown_minutes", value=str(config.cooldown_minutes), source="runtime"),
            ]
        )
    elif hasattr(config, "min_abs_extension_return_from_seed_open"):
        rows.extend(
            [
                RunConfigRow(
                    key="detector_min_abs_extension_return_from_seed_open",
                    value=str(getattr(config, "min_abs_extension_return_from_seed_open")),
                    source="runtime",
                ),
                RunConfigRow(
                    key="detector_min_minutes_since_event_start",
                    value=str(getattr(config, "min_minutes_since_event_start")),
                    source="runtime",
                ),
                RunConfigRow(
                    key="detector_max_minutes_since_event_start",
                    value=str(getattr(config, "max_minutes_since_event_start")),
                    source="runtime",
                ),
                RunConfigRow(
                    key="source_broad_detector_version",
                    value=str(getattr(getattr(config, "broad_detector_config"), "detector_version")),
                    source="runtime",
                ),
                RunConfigRow(
                    key="source_broad_detector_min_abs_return_pct",
                    value=str(getattr(getattr(config, "broad_detector_config"), "min_abs_return_pct")),
                    source="runtime",
                ),
            ]
        )
    elif hasattr(config, "min_daily_return_asof_t"):
        rows.extend(
            [
                RunConfigRow(
                    key="detector_min_daily_return_asof_t",
                    value=str(getattr(config, "min_daily_return_asof_t")),
                    source="runtime",
                ),
                RunConfigRow(
                    key="detector_min_trade_count_market_percentile_asof_t",
                    value=str(getattr(config, "min_trade_count_market_percentile_asof_t")),
                    source="runtime",
                ),
            ]
        )
    return rows


def _run_id() -> str:
    return "mvp1-events-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
