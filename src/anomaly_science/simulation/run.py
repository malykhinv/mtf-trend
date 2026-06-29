from __future__ import annotations

from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from anomaly_science.artifacts import build_manifest, runtime_reproducibility_rows, write_csv_artifact, write_csv_artifact_with_aliases, write_manifest
from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.audit import AuditStatus, ProtocolAuditRow, RunConfigRow
from anomaly_science.data.normalized import normalize_candles_1m, normalize_funding_rates
from anomaly_science.data.source import CsvDataSourceError, CsvDirectoryDataSource
from anomaly_science.decision import load_anomaly_decision_timing_csv
from anomaly_science.simulation.builder import (
    barrier_outcome_rows_to_artifact,
    build_barrier_outcome_rows,
    build_matched_market_time_control_rows,
    build_random_entry_time_control_rows,
    build_trade_simulation_rows,
    build_trade_simulation_metric_rows,
    trade_simulation_metric_rows_to_artifact,
    trade_simulation_rows_to_artifact,
)
from anomaly_science.simulation.config import TradeSimulationConfig
from anomaly_science.strategy.metadata import strategy_metadata_run_config_rows
from anomaly_science.strategy.registry import get_strategy


def run_mvp1_trade_simulation(
    *,
    input_dir: str | Path,
    decision_timing_path: str | Path,
    out_dir: str | Path,
    config: TradeSimulationConfig | None = None,
    max_input_time_ms: int | None = None,
) -> Path:
    input_path = Path(input_dir)
    decision_artifact_path = Path(decision_timing_path)
    output_path = Path(out_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    cfg = config or TradeSimulationConfig()
    funding_rate_present = _funding_rate_stream_present(input_path)

    decision_rows = load_anomaly_decision_timing_csv(decision_artifact_path)
    strategy = get_strategy(cfg.strategy_name)
    decision_symbols = {
        row.symbol
        for row in decision_rows
        if row.target_horizon_minutes == cfg.target_horizon_minutes
        and row.strategy_name == strategy.metadata.strategy_name
        and row.strategy_version == strategy.metadata.strategy_version
    }

    source = CsvDirectoryDataSource(input_path, max_time_ms=max_input_time_ms)
    candles_frame = source.read_frame("candles_1m", required=True)
    if candles_frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    candles_frame = _filter_frame_to_symbols(candles_frame, decision_symbols)
    funding_frame = source.read_frame("funding_rate", required=False)
    funding_frame = _filter_frame_to_symbols(funding_frame, decision_symbols)
    candles_1m = normalize_candles_1m(candles_frame)
    funding_rates = normalize_funding_rates(funding_frame)
    simulation_rows = build_trade_simulation_rows(
        candles_1m=candles_1m,
        funding_rates=funding_rates,
        decision_rows=decision_rows,
        config=cfg,
    )
    barrier_outcome_rows = build_barrier_outcome_rows(
        candles_1m=candles_1m,
        decision_rows=decision_rows,
        config=cfg,
    )
    random_entry_control_rows = build_random_entry_time_control_rows(
        candles_1m=candles_1m,
        funding_rates=funding_rates,
        decision_rows=decision_rows,
        config=cfg,
    )
    matched_market_time_control_rows = build_matched_market_time_control_rows(
        candles_1m=candles_1m,
        funding_rates=funding_rates,
        decision_rows=decision_rows,
        config=cfg,
    )
    metric_rows = build_trade_simulation_metric_rows(
        decision_rows=decision_rows,
        simulation_rows=simulation_rows,
        random_entry_time_control_rows=random_entry_control_rows,
        matched_market_time_control_rows=matched_market_time_control_rows,
        config=cfg,
    )
    protocol_rows = _protocol_rows(
        decision_row_count=len(decision_rows),
        simulation_row_count=len(simulation_rows),
        barrier_outcome_row_count=len(barrier_outcome_rows),
        funding_rate_present=funding_rate_present,
    )
    run_config_rows = _run_config_rows(
        input_path=input_path,
        decision_timing_path=decision_artifact_path,
        output_path=output_path,
        config=cfg,
    )

    written: list[Path] = []
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_barrier_outcomes.csv",
            barrier_outcome_rows_to_artifact(barrier_outcome_rows),
            get_artifact_schema("strategy_barrier_outcomes.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_trade_simulation.csv",
            trade_simulation_rows_to_artifact(simulation_rows),
            get_artifact_schema("strategy_trade_simulation.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_trade_simulation_metrics.csv",
            trade_simulation_metric_rows_to_artifact(metric_rows),
            get_artifact_schema("strategy_trade_simulation_metrics.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_protocol_audit.csv",
            _protocol_rows_to_artifact(protocol_rows),
            get_artifact_schema("strategy_protocol_audit.csv"),
        )
    )
    written.extend(
        write_csv_artifact_with_aliases(
            output_path / "strategy_run_config.csv",
            [asdict(row) for row in run_config_rows],
            get_artifact_schema("strategy_run_config.csv"),
        )
    )
    manifest = build_manifest(run_id=_run_id(), artifact_paths=written, root=output_path)
    write_manifest(output_path / "artifact_manifest.json", manifest)
    return output_path


def _protocol_rows(*, decision_row_count: int, simulation_row_count: int, barrier_outcome_row_count: int, funding_rate_present: bool) -> list[ProtocolAuditRow]:
    from anomaly_science.audit import build_methodology_v2_audit_rows

    base_rows = [
        ProtocolAuditRow(
            check_name="mvp1_trade_simulation_scope",
            status=AuditStatus.PASS,
            message="simplified pessimistic simulation only; no live execution, no order book fills, no full-period optimization",
        ),
        ProtocolAuditRow(
            check_name="decision_timing_schema_boundary",
            status=AuditStatus.PASS,
            message=f"strategy_decision_timing.csv accepted through strict schema boundary with {decision_row_count} rows",
            artifact="strategy_decision_timing.csv",
        ),
        ProtocolAuditRow(
            check_name="next_open_entry_with_slippage",
            status=AuditStatus.PASS,
            message="each simulated trade enters at the next 1m open after snapshot_time_ms with pessimistic slippage and toxic-entry penalty applied",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="stop_target_are_structural",
            status=AuditStatus.PASS,
            message="stop and target anchors come from the strategy execution-policy contract; Core applies close/touch triggers and causal swing trailing",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="funding_rate_boundary",
            status=AuditStatus.PASS if funding_rate_present else AuditStatus.WARN,
            message=(
                "funding_rate.csv is present; simulation debits side-aware funding costs at cut-off timestamps inside each hold"
                if funding_rate_present
                else "funding_rate stream absent; short-distribution conclusions are audit-limited, not zero-cost funding proof"
            ),
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="trade_simulation_rows_written",
            status=AuditStatus.PASS if simulation_row_count > 0 else AuditStatus.WARN,
            message=f"wrote {simulation_row_count} strategy_trade_simulation.csv rows",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="realized_barrier_outcomes_written",
            status=AuditStatus.PASS if barrier_outcome_row_count > 0 else AuditStatus.WARN,
            message=(
                f"wrote {barrier_outcome_row_count} strategy_barrier_outcomes.csv rows for future utility modeling; "
                "artifact is labels-only and is not consumed by decision selection"
            ),
            artifact="strategy_barrier_outcomes.csv",
        ),
        ProtocolAuditRow(
            check_name="legacy_import_boundary",
            status=AuditStatus.PASS,
            message="mvp1 trade simulation uses anomaly_science modules only; legacy_quarantine is reference-only",
        ),
        ProtocolAuditRow(
            check_name="matched_market_time_control_declared",
            status=AuditStatus.PASS,
            message="simulation metrics include a bounded same-symbol/session/volatility random market-time baseline separate from signal-time shuffling",
            artifact="strategy_trade_simulation_metrics.csv",
        ),
    ]
    implemented_methodology_rows = [
        ProtocolAuditRow(
            check_name="trade_simulation_after_calibration_and_decision_timing",
            status=AuditStatus.PASS,
            message="simulation consumes decision timing rows, which are produced after OOS prediction/calibration and EV",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="execution_reference_model_aligned_between_ev_and_simulation",
            status=AuditStatus.PASS,
            message="simulation validates decision execution_reference_model/cost_model and records the same execution model with actual next-open entry basis",
            artifact="strategy_decision_timing.csv;strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="pessimistic_entry_price_includes_slippage_penalty",
            status=AuditStatus.PASS,
            message="entry prices include side-aware pessimistic slippage plus toxic-entry 1m range penalty; exit prices include pessimistic slippage",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="anti_pyramiding_one_open_position_per_symbol_strategy",
            status=AuditStatus.PASS,
            message="simulation suppresses parallel open positions per symbol + strategy variant",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="intracandle_double_barrier_resolved_as_stop_loss_first",
            status=AuditStatus.PASS,
            message="if target and stop are both reachable in the same 1m candle, simulation exits at stop_loss",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="fixed_percent_stop_target_forbidden",
            status=AuditStatus.PASS,
            message="simulation consumes only strategy-declared structural anchors; no ATR-multiple or fixed-percent stop/target path exists",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="partial_target_fraction_grid_declared_by_strategy",
            status=AuditStatus.PASS,
            message="Core enumerates only the strategy-declared partial-close fraction grid; policy selection must occur on development data before untouched verification",
            artifact="strategy_trade_simulation.csv",
        ),
        ProtocolAuditRow(
            check_name="selection_edge_requires_market_time_control",
            status=AuditStatus.PASS,
            message="signal-time shuffle is not enough for selection-edge claims; matched_market_time metrics are written as a separate negative baseline",
            artifact="strategy_trade_simulation_metrics.csv",
        ),
        ProtocolAuditRow(
            check_name="realized_barrier_outcomes_separate_from_decision",
            status=AuditStatus.PASS,
            message="strategy_barrier_outcomes.csv is written after decisions as a realized label artifact for future utility modeling and is not read by EV or trade selection",
            artifact="strategy_barrier_outcomes.csv",
        ),
    ]
    return base_rows + build_methodology_v2_audit_rows(stage="mvp1_simulation", implemented=implemented_methodology_rows)




def _filter_frame_to_symbols(frame, symbols: set[str]):
    if frame is None or not symbols or "symbol" not in frame.columns:
        return frame
    return frame.loc[frame["symbol"].isin(symbols)].copy()

def _funding_rate_stream_present(input_path: Path) -> bool:
    return (input_path / "funding_rate.csv").is_file()


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
    max_input_time_ms: int | None = None,
) -> list[RunConfigRow]:
    return [
        RunConfigRow(key="command", value="run-mvp1-trade-simulation", source="cli"),
        RunConfigRow(key="input_dir", value=str(input_path), source="cli"),
        RunConfigRow(key="decision_timing_path", value=str(decision_timing_path), source="cli"),
        RunConfigRow(key="output_dir", value=str(output_path), source="cli"),
        RunConfigRow(key="max_input_time_ms", value="" if max_input_time_ms is None else str(max_input_time_ms), source="cli"),
        RunConfigRow(key="data_source", value="csv_directory_v1", source="runtime"),
        *runtime_reproducibility_rows(
            data_paths=(input_path, decision_timing_path),
            config=config,
            extra_config={"command": "run-mvp1-trade-simulation", "stage": "mvp1_simulation"},
        ),
        RunConfigRow(key="stage", value="mvp1_simulation", source="runtime"),
        *strategy_metadata_run_config_rows(strategy_name=config.strategy_name),
        RunConfigRow(key="simulation_version", value=config.simulation_version, source="runtime"),
        RunConfigRow(key="target_horizon_minutes", value=str(config.target_horizon_minutes), source="runtime"),
        RunConfigRow(key="execution_reference_model", value=config.execution_reference_model, source="runtime"),
        RunConfigRow(key="entry_price_basis", value=config.entry_price_basis, source="runtime"),
        RunConfigRow(key="cost_model", value=config.cost_model, source="runtime"),
        RunConfigRow(key="toxic_entry_atr_1m_fraction", value=str(config.toxic_entry_atr_1m_fraction), source="runtime"),
        RunConfigRow(key="random_seed", value=str(config.random_seed), source="runtime"),
        RunConfigRow(key="matched_market_time_control_enabled", value=str(config.matched_market_time_control_enabled), source="runtime"),
        RunConfigRow(key="matched_market_time_lookback_minutes", value=str(config.matched_market_time_lookback_minutes), source="runtime"),
        RunConfigRow(key="matched_market_time_session_minutes", value=str(config.matched_market_time_session_minutes), source="runtime"),
        RunConfigRow(key="matched_market_time_volatility_buckets", value=str(config.matched_market_time_volatility_buckets), source="runtime"),
        RunConfigRow(key="max_matched_market_time_candidates_per_decision", value=str(config.max_matched_market_time_candidates_per_decision), source="runtime"),
        RunConfigRow(key="require_prediction_confident", value=str(config.require_prediction_confident), source="runtime"),
        RunConfigRow(key="require_rr_acceptable", value=str(config.require_rr_acceptable), source="runtime"),
        RunConfigRow(key="barrier_outcome_version", value="mvp1_realized_barrier_outcome_v1", source="runtime"),
        RunConfigRow(key="barrier_outcome_scope", value="labels_only_not_model_feature_not_decision_rule", source="runtime"),
        RunConfigRow(key="simulation_scope", value="simplified_pessimistic_not_live_execution", source="runtime"),
    ]


def _run_id() -> str:
    return "mvp1-trade-simulation-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
