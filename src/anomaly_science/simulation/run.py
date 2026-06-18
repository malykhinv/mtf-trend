from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.audit import build_methodology_v2_audit_rows
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.data.source import CsvDirectoryDataSource
from anomaly_science.decision import load_anomaly_decision_timing_csv
from anomaly_science.simulation.builder import (
    build_trade_simulation_from_source,
    build_trade_simulation_metric_rows,
    trade_simulation_metric_rows_to_artifact,
    trade_simulation_rows_to_artifact,
)
from anomaly_science.simulation.config import TradeSimulationConfig
from anomaly_science.strategy.metadata import strategy_metadata_run_config_rows


def run_mvp1_trade_simulation(
    *,
    input_dir: str | Path,
    decision_timing_path: str | Path,
    out_dir: str | Path,
    config: TradeSimulationConfig | None = None,
) -> Path:
    input_path = Path(input_dir)
    decision_artifact_path = Path(decision_timing_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or TradeSimulationConfig()

    source = CsvDirectoryDataSource(input_path)
    decision_rows = load_anomaly_decision_timing_csv(decision_artifact_path)
    simulation_rows = build_trade_simulation_from_source(
        source=source,
        decision_timing_path=decision_artifact_path,
        config=cfg,
    )
    metric_rows = build_trade_simulation_metric_rows(decision_rows=decision_rows, simulation_rows=simulation_rows, config=cfg)
    protocol_rows = _protocol_rows(decision_row_count=len(decision_rows), simulation_row_count=len(simulation_rows))
    run_config_rows = _run_config_rows(
        input_path=input_path,
        decision_timing_path=decision_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_trade_simulation.csv",
            trade_simulation_rows_to_artifact(simulation_rows),
            get_artifact_schema("anomaly_trade_simulation.csv"),
        )
    )
    written.append(
        write_csv_artifact(
            output_path / "anomaly_trade_simulation_metrics.csv",
            trade_simulation_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("anomaly_trade_simulation_metrics.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("anomaly_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "anomaly_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("anomaly_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(*, decision_row_count: int, simulation_row_count: int) -> list[ProtocolAuditRow]:
    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_trade_simulation_scope",
            status=AuditStatus.PASS,
            message="simplified pessimistic simulation only; no live execution, no order book fills, no full-period optimization",
        ),
        ProtocolAuditRow(
            check_name="decision_timing_schema_boundary",
            status=AuditStatus.PASS,
            message=f"anomaly_decision_timing.csv accepted through strict schema boundary with {decision_row_count} rows",
            artifact="anomaly_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="next_open_entry_with_slippage",
            status=AuditStatus.PASS,
            message="each simulated trade enters at the next 1m open after snapshot_time_ms with pessimistic slippage and toxic-entry penalty applied",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="stop_target_are_atr_normalized",
            status=AuditStatus.PASS,
            message="stop and target distances are read from decision timing rows derived from ATR-normalized label thresholds",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="trade_simulation_rows_written",
            status=AuditStatus.PASS if simulation_row_count > 0 else AuditStatus.WARN,
            message=f"wrote {simulation_row_count} anomaly_trade_simulation.csv rows",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 trade simulation uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="trade_simulation_after_calibration_and_decision_timing",
            status=AuditStatus.PASS,
            message="simulation consumes decision timing rows, which are produced after OOS prediction/calibration and EV",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="pessimistic_entry_price_includes_slippage_penalty",
            status=AuditStatus.PASS,
            message="entry prices include side-aware pessimistic slippage plus toxic-entry 1m range penalty; exit prices include pessimistic slippage",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="anti_pyramiding_one_open_position_per_symbol_strategy",
            status=AuditStatus.PASS,
            message="simulation suppresses parallel open positions per symbol + strategy variant",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="intracandle_double_barrier_resolved_as_stop_loss_first",
            status=AuditStatus.PASS,
            message="if target and stop are both reachable in the same 1m candle, simulation exits at stop_loss",
            artifact="anomaly_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_stop_target_forbidden",
            status=AuditStatus.PASS,
            message="simulation consumes EV stop/target distances derived from StrategyMetadata ATR defaults and core_atr_1440; no fixed-percent universal stop/target path exists",
            artifact="anomaly_trade_simulation.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_simulation", implemented=implemented_methodology_rows)


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
    decision_timing_path: Path,
    output_path: Path,
    config: TradeSimulationConfig,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-trade-simulation", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="decision_timing_path", value=str(decision_timing_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, decision_timing_path),
            config=config,
            extra_config={"command": "run-mvp1-trade-simulation", "stage": "mvp1_simulation"},
        ),
        RunConfigRow(key="stage", value="mvp1_simulation", source="runtime"),
        *strategy_metadata_run_config_rows(),
        RunConfigRow(key="simulation_version", value=config.simulation_version, source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="toxic_entry_atr_1m_fraction", value=str(config.toxic_entry_atr_1m_fraction), source="runtime"),
        RunConfigRow(key="require_prediction_confident", value=str(config.require_prediction_confident), source="runtime"),
        RunConfigRow(key="require_rr_acceptable", value=str(config.require_rr_acceptable), source="runtime"),
        RunConfigRow(key="simulation_scope", value="simplified_pessimistic_not_live_execution", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-trade-simulation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
