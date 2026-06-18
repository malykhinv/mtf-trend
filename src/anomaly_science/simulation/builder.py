from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Mapping, Sequence

import pandas as pd

from anomaly_science.contracts.artifacts import get_artifact_schema
from anomaly_science.contracts.decision import ExpectedValueRow
from anomaly_science.contracts.market import Candle1m, MarketDataContractError, ONE_MINUTE_MS
from anomaly_science.contracts.simulation import (
    TRADE_SIMULATION_TEMPORAL_CONTRACT,
    TradeSimulationMetricRow,
    TradeSimulationRow,
)
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.data.source import CsvDataSourceError, MarketDataSource
from anomaly_science.decision import load_anomaly_decision_timing_csv
from anomaly_science.simulation.config import TradeSimulationConfig
from anomaly_science.strategy.registry import get_strategy


class TradeSimulationInputError(ValueError):
    """Raised when simulation inputs cannot produce clean trade rows."""


class TradeSimulationArtifactError(ValueError):
    """Raised when simulation artifacts violate strict schemas."""


def build_trade_simulation_from_source(
    *,
    source: MarketDataSource,
    decision_timing_path: str | Path,
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    frame = source.read_frame("candles_1m", required=True)
    if frame is None:
        raise CsvDataSourceError("required dataset 'candles_1m.csv' resolved to None")
    return build_trade_simulation_rows(
        candles_1m=normalize_candles_1m(frame),
        decision_rows=load_anomaly_decision_timing_csv(decision_timing_path),
        config=config,
    )


def build_trade_simulation_rows(
    *,
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationRow, ...]:
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in candles_1m:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)
    for candles in candles_by_symbol.values():
        candles.sort(key=lambda item: (item.available_time_ms, item.open_time_ms))

    rows: list[TradeSimulationRow] = []
    active_until_by_strategy_symbol: dict[tuple[str, str, str], int] = {}
    for decision in sorted(decision_rows, key=lambda item: (item.snapshot_time_ms, item.symbol, item.event_id)):
        if decision.target_horizon_minutes != cfg.target_horizon_minutes:
            continue
        if decision.strategy_version != cfg.strategy_version:
            continue
        if not _is_simulatable_decision(decision, config=cfg):
            continue
        key = (decision.strategy_name, decision.strategy_version, decision.symbol)
        active_until_ms = active_until_by_strategy_symbol.get(key)
        if active_until_ms is not None and decision.snapshot_time_ms <= active_until_ms:
            continue
        symbol_candles = candles_by_symbol.get(decision.symbol, [])
        row = _simulate_decision(decision=decision, candles=symbol_candles, config=cfg)
        rows.append(row)
        active_until_by_strategy_symbol[key] = row.exit_time_ms
    return tuple(rows)


def build_trade_simulation_metric_rows(
    *,
    decision_rows: Sequence[ExpectedValueRow] | Iterable[ExpectedValueRow],
    simulation_rows: Sequence[TradeSimulationRow] | Iterable[TradeSimulationRow],
    config: TradeSimulationConfig | None = None,
) -> tuple[TradeSimulationMetricRow, ...]:
    cfg = config or TradeSimulationConfig()
    _validate_strategy_horizon(config=cfg)
    decisions = tuple(
        row
        for row in decision_rows
        if row.target_horizon_minutes == cfg.target_horizon_minutes and row.strategy_version == cfg.strategy_version
    )
    trades = tuple(simulation_rows)
    metrics: list[TradeSimulationMetricRow] = []

    def add(name: str, value: str | float | int, row_count: int, notes: str) -> None:
        metrics.append(
            TradeSimulationMetricRow(
                simulation_version=cfg.simulation_version,
                target_horizon_minutes=cfg.target_horizon_minutes,
                metric_name=name,
                metric_value=_metric_value(value),
                row_count=row_count,
                notes=notes,
            )
        )

    add("decision_rows", len(decisions), len(decisions), "decision timing rows considered for this horizon")
    add("simulated_trade_rows", len(trades), len(trades), "executed long/short rows with pessimistic fills")
    add("non_trade_decision_rows", len(decisions) - len(trades), len(decisions), "decision rows skipped by no_trade/wait/flags or missing future candles")
    if trades:
        add("win_rate", _mean(1.0 if row.net_pnl > 0.0 else 0.0 for row in trades), len(trades), "share of positive net_pnl simulated trades")
        add("mean_net_pnl", _mean(row.net_pnl for row in trades), len(trades), "mean net PnL in price units")
        add("mean_net_pnl_r", _mean(row.net_pnl_r for row in trades), len(trades), "mean net PnL in stop-distance R units")
        add("total_net_pnl", sum(row.net_pnl for row in trades), len(trades), "sum net PnL in price units")
        add("stop_loss_share", _mean(1.0 if row.exit_reason == "stop_loss" else 0.0 for row in trades), len(trades), "share exited by stop loss")
        add("target_hit_share", _mean(1.0 if row.exit_reason == "target_hit" else 0.0 for row in trades), len(trades), "share exited by target")
    return tuple(metrics)


def trade_simulation_rows_to_artifact(rows: Sequence[TradeSimulationRow]) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for row in rows:
        result.append(_with_core_atr_csv_alias(asdict(row)))
    return result


def _with_core_atr_csv_alias(payload: dict[str, object]) -> dict[str, object]:
    return {"ATR_1d_asof_t" if key == "core_atr_1440" else key: value for key, value in payload.items()}


def trade_simulation_metric_rows_to_artifact(rows: Sequence[TradeSimulationMetricRow]) -> list[dict[str, object]]:
    return [asdict(row) for row in rows]


def load_anomaly_trade_simulation_csv(path: str | Path) -> tuple[TradeSimulationRow, ...]:
    return tuple(
        _load_simulation_artifact(
            path=path,
            schema_name="anomaly_trade_simulation.csv",
            row_builder=_simulation_from_mapping,
        )
    )


def _is_simulatable_decision(decision: ExpectedValueRow, *, config: TradeSimulationConfig) -> bool:
    if decision.best_action not in {"long", "short"}:
        return False
    if config.require_prediction_confident and not decision.is_prediction_confident:
        return False
    if config.require_rr_acceptable and not decision.is_RR_still_acceptable:
        return False
    return True


def _validate_strategy_horizon(*, config: TradeSimulationConfig) -> None:
    strategy = get_strategy(config.strategy_version)
    if strategy.metadata.horizon_minutes != config.target_horizon_minutes:
        raise TradeSimulationInputError(
            f"simulation target_horizon_minutes={config.target_horizon_minutes} "
            f"does not match strategy horizon {strategy.metadata.horizon_minutes}"
        )


def _simulate_decision(
    *,
    decision: ExpectedValueRow,
    candles: Sequence[Candle1m],
    config: TradeSimulationConfig,
) -> TradeSimulationRow:
    entry_candle = _entry_candle(decision=decision, candles=candles)
    exit_candles = [
        candle
        for candle in candles
        if candle.available_time_ms >= entry_candle.available_time_ms
        and candle.available_time_ms <= decision.snapshot_time_ms + decision.target_horizon_minutes * ONE_MINUTE_MS
    ]
    if not exit_candles:
        raise TradeSimulationInputError(f"no simulation candles for decision {decision.event_id}")
    side = decision.best_action
    toxic_entry_penalty = _toxic_entry_penalty(decision=decision, candles=candles, config=config)
    entry_price = _entry_price(
        side=side,
        entry_open=entry_candle.open,
        slippage_bps=decision.slippage_bps,
        toxic_entry_penalty=toxic_entry_penalty,
    )
    target_price, stop_price = _barrier_prices(
        side=side,
        entry_price=entry_price,
        target_distance=decision.target_distance,
        stop_distance=decision.stop_distance,
    )
    exit_candle, exit_reason, barrier_resolution = _resolve_exit(
        side=side,
        candles=exit_candles,
        target_price=target_price,
        stop_price=stop_price,
    )
    exit_price = _exit_price(
        side=side,
        exit_reason=exit_reason,
        candle=exit_candle,
        target_price=target_price,
        stop_price=stop_price,
        slippage_bps=decision.slippage_bps,
    )
    gross_pnl = (exit_price - entry_price) if side == "long" else (entry_price - exit_price)
    total_cost = (entry_price + exit_price) * (decision.fee_bps / 10_000.0)
    net_pnl = gross_pnl - total_cost
    return TradeSimulationRow(
        simulation_version=config.simulation_version,
        strategy_name=decision.strategy_name,
        strategy_version=decision.strategy_version,
        event_id=decision.event_id,
        symbol=decision.symbol,
        snapshot_time_ms=decision.snapshot_time_ms,
        feature_cutoff_time_ms=decision.feature_cutoff_time_ms,
        target_horizon_minutes=decision.target_horizon_minutes,
        decision_action=decision.best_action,
        simulated_side=side,
        entry_reference_time_ms=entry_candle.open_time_ms,
        entry_reference_open=entry_candle.open,
        entry_price=entry_price,
        core_atr_1440=decision.core_atr_1440,
        stop_distance=decision.stop_distance,
        target_distance=decision.target_distance,
        stop_price=stop_price,
        target_price=target_price,
        fee_bps=decision.fee_bps,
        slippage_bps=decision.slippage_bps,
        total_cost=total_cost,
        exit_time_ms=exit_candle.open_time_ms,
        exit_price=exit_price,
        exit_reason=exit_reason,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        net_pnl_r=net_pnl / decision.stop_distance,
        net_return=net_pnl / entry_price,
        barrier_resolution=barrier_resolution,
        temporal_contract=TRADE_SIMULATION_TEMPORAL_CONTRACT,
    )


def _entry_candle(*, decision: ExpectedValueRow, candles: Sequence[Candle1m]) -> Candle1m:
    for candle in candles:
        if candle.open_time_ms > decision.snapshot_time_ms and candle.available_time_ms > decision.snapshot_time_ms:
            return candle
    raise TradeSimulationInputError(f"next 1m open is missing for decision {decision.event_id}")


def _entry_price(*, side: str, entry_open: float, slippage_bps: float, toxic_entry_penalty: float) -> float:
    penalty = slippage_bps / 10_000.0
    if side == "long":
        return entry_open * (1.0 + penalty) + toxic_entry_penalty
    return entry_open * (1.0 - penalty) - toxic_entry_penalty


def _toxic_entry_penalty(*, decision: ExpectedValueRow, candles: Sequence[Candle1m], config: TradeSimulationConfig) -> float:
    if config.toxic_entry_atr_1m_fraction == 0.0:
        return 0.0
    asof_candles = [candle for candle in candles if candle.available_time_ms <= decision.snapshot_time_ms]
    if not asof_candles:
        return 0.0
    last_closed = max(asof_candles, key=lambda item: item.available_time_ms)
    atr_1m_proxy = max(last_closed.high - last_closed.low, 0.0)
    return atr_1m_proxy * config.toxic_entry_atr_1m_fraction


def _barrier_prices(*, side: str, entry_price: float, target_distance: float, stop_distance: float) -> tuple[float, float]:
    if side == "long":
        return entry_price + target_distance, entry_price - stop_distance
    return entry_price - target_distance, entry_price + stop_distance


def _resolve_exit(
    *,
    side: str,
    candles: Sequence[Candle1m],
    target_price: float,
    stop_price: float,
) -> tuple[Candle1m, str, str]:
    for candle in candles:
        if side == "long":
            target_hit = candle.high >= target_price
            stop_hit = candle.low <= stop_price
        else:
            target_hit = candle.low <= target_price
            stop_hit = candle.high >= stop_price
        if target_hit and stop_hit:
            return candle, "stop_loss", "stop_loss_first"
        if stop_hit:
            return candle, "stop_loss", "single_barrier"
        if target_hit:
            return candle, "target_hit", "single_barrier"
    return candles[-1], "horizon_close", "horizon_close"


def _exit_price(
    *,
    side: str,
    exit_reason: str,
    candle: Candle1m,
    target_price: float,
    stop_price: float,
    slippage_bps: float,
) -> float:
    penalty = slippage_bps / 10_000.0
    if exit_reason == "target_hit":
        raw = target_price
    elif exit_reason == "stop_loss":
        raw = stop_price
    else:
        raw = candle.close
    if side == "long":
        return raw * (1.0 - penalty)
    return raw * (1.0 + penalty)


def _load_simulation_artifact(*, path: str | Path, schema_name: str, row_builder: object) -> tuple[object, ...]:
    artifact_path = Path(path)
    if not artifact_path.exists():
        raise TradeSimulationArtifactError(f"simulation artifact is missing: {artifact_path}")
    frame = pd.read_csv(artifact_path)
    schema = get_artifact_schema(schema_name)
    expected_columns = list(schema.required_columns)
    actual_columns = list(frame.columns)
    if actual_columns != expected_columns:
        raise TradeSimulationArtifactError(f"{schema_name} columns must match {expected_columns}, got {actual_columns}")
    rows: list[object] = []
    for row_index, row in frame.iterrows():
        try:
            rows.append(row_builder(row))  # type: ignore[operator]
        except (TypeError, ValueError, MarketDataContractError) as exc:
            raise TradeSimulationArtifactError(f"invalid {schema_name} row {row_index}: {exc}") from exc
    return tuple(rows)


def _simulation_from_mapping(row: Mapping[str, object]) -> TradeSimulationRow:
    return TradeSimulationRow(
        simulation_version=_required_str(row, "simulation_version"),
        strategy_name=_required_str(row, "strategy_name"),
        strategy_version=_required_str(row, "strategy_version"),
        event_id=_required_str(row, "event_id"),
        symbol=_required_str(row, "symbol"),
        snapshot_time_ms=_required_int(row, "snapshot_time_ms"),
        feature_cutoff_time_ms=_required_int(row, "feature_cutoff_time_ms"),
        target_horizon_minutes=_required_int(row, "target_horizon_minutes"),
        decision_action=_required_str(row, "decision_action"),
        simulated_side=_required_str(row, "simulated_side"),
        entry_reference_time_ms=_required_int(row, "entry_reference_time_ms"),
        entry_reference_open=_required_float(row, "entry_reference_open"),
        entry_price=_required_float(row, "entry_price"),
        core_atr_1440=_required_float(row, "ATR_1d_asof_t"),
        stop_distance=_required_float(row, "stop_distance"),
        target_distance=_required_float(row, "target_distance"),
        stop_price=_required_float(row, "stop_price"),
        target_price=_required_float(row, "target_price"),
        fee_bps=_required_float(row, "fee_bps"),
        slippage_bps=_required_float(row, "slippage_bps"),
        total_cost=_required_float(row, "total_cost"),
        exit_time_ms=_required_int(row, "exit_time_ms"),
        exit_price=_required_float(row, "exit_price"),
        exit_reason=_required_str(row, "exit_reason"),
        gross_pnl=_required_float(row, "gross_pnl"),
        net_pnl=_required_float(row, "net_pnl"),
        net_pnl_r=_required_float(row, "net_pnl_r"),
        net_return=_required_float(row, "net_return"),
        barrier_resolution=_required_str(row, "barrier_resolution"),
        temporal_contract=_required_str(row, "temporal_contract"),
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
    return float(value)
