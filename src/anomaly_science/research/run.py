from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

from anomaly_science.atlas import run_mvp1_atlas
from anomaly_science.cache_export import CacheMvp1CsvExportConfig, export_cache_to_mvp1_csv
from anomaly_science.controls import ControlsConfig, run_mvp1_controls
from anomaly_science.data import run_mvp1_data_audit
from anomaly_science.decision import ExpectedValueConfig, run_mvp1_expected_value
from anomaly_science.events.run import run_mvp1_events
from anomaly_science.features import run_mvp1_feature_matrix, run_mvp1_features
from anomaly_science.future import run_mvp1_future
from anomaly_science.labels import run_mvp1_labels
from anomaly_science.prediction import WalkForwardPredictionConfig, run_mvp1_prediction
from anomaly_science.simulation import TradeSimulationConfig, run_mvp1_trade_simulation
from anomaly_science.state import run_mvp1_state
from anomaly_science.strategy.registry import get_strategy
from anomaly_science.validation import run_mvp1_holdout_governance


DEFAULT_RESEARCH_OUTPUT_ROOT = Path(".output/results/research_runs")


@dataclass(frozen=True, slots=True)
class ResearchRunConfig:
    strategy_name: str
    cache_dir: Path
    days: int | None = None
    output_root: Path = DEFAULT_RESEARCH_OUTPUT_ROOT

    def __post_init__(self) -> None:
        if not self.strategy_name:
            raise ValueError("strategy_name is required")
        if self.days is not None and self.days <= 0:
            raise ValueError("days must be positive when provided")


def run_research_pipeline(config: ResearchRunConfig) -> Path:
    strategy = get_strategy(config.strategy_name)
    run_dir = config.output_root / _run_id(strategy_name=config.strategy_name)
    input_dir = run_dir / "input"
    stages_dir = run_dir / "stages"

    export_cache_to_mvp1_csv(
        CacheMvp1CsvExportConfig(
            cache_dir=config.cache_dir,
            out_dir=input_dir,
            days=config.days,
        )
    )
    start_date, end_date = _input_date_range(input_dir / "candles_1m.csv")
    governance_dir = run_mvp1_holdout_governance(
        out_dir=stages_dir / "holdout_governance",
        start_date=start_date,
        end_date=end_date,
        protocol_freeze_id=_protocol_freeze_id(strategy_name=config.strategy_name, start_date=start_date, end_date=end_date),
    )

    run_mvp1_data_audit(input_dir=input_dir, out_dir=stages_dir / "data_audit")
    events_dir = run_mvp1_events(input_dir=input_dir, out_dir=stages_dir / "events")
    state_dir = run_mvp1_state(
        input_dir=input_dir,
        events_path=events_dir / "strategy_events.csv",
        out_dir=stages_dir / "state",
    )
    future_dir = run_mvp1_future(
        input_dir=input_dir,
        state_path=state_dir / "strategy_state_1m.csv",
        out_dir=stages_dir / "future",
    )
    run_mvp1_features(out_dir=stages_dir / "features")
    feature_matrix_dir = run_mvp1_feature_matrix(
        input_dir=input_dir,
        state_path=state_dir / "strategy_state_1m.csv",
        out_dir=stages_dir / "feature_matrix",
    )
    run_mvp1_atlas(
        state_path=state_dir / "strategy_state_1m.csv",
        future_path=future_dir / "strategy_future_paths.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "atlas",
    )
    labels_dir = run_mvp1_labels(
        state_path=state_dir / "strategy_state_1m.csv",
        future_path=future_dir / "strategy_future_paths.csv",
        out_dir=stages_dir / "labels",
    )
    prediction_dir = run_mvp1_prediction(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "prediction",
        config=WalkForwardPredictionConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    run_mvp1_controls(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        feature_matrix_path=feature_matrix_dir / "strategy_feature_matrix.csv",
        out_dir=stages_dir / "controls",
        config=ControlsConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    ev_dir = run_mvp1_expected_value(
        state_path=state_dir / "strategy_state_1m.csv",
        labels_path=labels_dir / "strategy_outcome_labels.csv",
        predictions_path=prediction_dir / "strategy_oos_predictions.csv",
        out_dir=stages_dir / "expected_value",
        config=ExpectedValueConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    run_mvp1_trade_simulation(
        input_dir=input_dir,
        decision_timing_path=ev_dir / "strategy_decision_timing.csv",
        out_dir=stages_dir / "simulation",
        config=TradeSimulationConfig(
            strategy_name=config.strategy_name,
            target_horizon_minutes=strategy.metadata.horizon_minutes,
        ),
    )
    _write_summary(run_dir=run_dir, config=config, start_date=start_date, end_date=end_date, governance_dir=governance_dir)
    return run_dir


def _write_summary(*, run_dir: Path, config: ResearchRunConfig, start_date: date, end_date: date, governance_dir: Path) -> None:
    candles = pd.read_csv(run_dir / "input" / "candles_1m.csv", usecols=["open_time_ms"])
    min_time = int(candles["open_time_ms"].min()) if not candles.empty else 0
    max_time = int(candles["open_time_ms"].max()) if not candles.empty else 0
    lines = [
        "key,value",
        f"strategy_name,{config.strategy_name}",
        f"days,{'' if config.days is None else config.days}",
        f"cache_dir,{config.cache_dir}",
        f"run_dir,{run_dir}",
        f"input_min_open_time_ms,{min_time}",
        f"input_max_open_time_ms,{max_time}",
        f"research_start_date,{start_date.isoformat()}",
        f"research_end_date,{end_date.isoformat()}",
        f"holdout_governance_dir,{governance_dir}",
    ]
    (run_dir / "research_run_summary.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _input_date_range(candles_path: Path) -> tuple[date, date]:
    candles = pd.read_csv(candles_path, usecols=["open_time_ms"])
    if candles.empty:
        raise ValueError(f"cannot derive research date range from empty candles file: {candles_path}")
    timestamps = pd.to_datetime(candles["open_time_ms"].astype("int64"), unit="ms", utc=True)
    return timestamps.min().date(), timestamps.max().date()


def _protocol_freeze_id(*, strategy_name: str, start_date: date, end_date: date) -> str:
    return f"run_research_{strategy_name}_{start_date.isoformat()}_{end_date.isoformat()}_protocol_freeze_v1"


def _run_id(*, strategy_name: str) -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{timestamp}_{strategy_name}"
