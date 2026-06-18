from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from anomaly_science.strategy import StrategyContractError, StrategyMetadata, validate_trigger_frame
from anomaly_science.strategy.registry import available_strategies, get_strategy
from anomaly_science.strategy.anomaly import BroadAnomalyStrategy


BASE_TS = 1_704_067_200_000


def _dt(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def _candle_row(index: int, *, close: float, quote_volume: float = 100.0) -> dict[str, object]:
    open_time_ms = BASE_TS + index * 60_000
    high = max(100.0, close) + 0.10
    low = min(100.0, close) - 0.10
    return {
        "symbol": "AAA/USDT:USDT",
        "open_time_ms": open_time_ms,
        "available_time_ms": open_time_ms + 60_000,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "quote_volume": quote_volume,
        "number_of_trades": 10.0,
        "taker_buy_quote_volume": quote_volume * 0.5,
    }


def test_strategy_metadata_validates_required_base_contract_fields() -> None:
    with pytest.raises(StrategyContractError, match="horizon_minutes"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v1",
            strategy_family="anomaly",
            horizon_minutes=0,
            take_profit_atr_1440=1.0,
            stop_loss_atr_1440=1.0,
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_strategy_metadata_rejects_multi_horizon_values() -> None:
    with pytest.raises(StrategyContractError, match="one fixed int"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v1",
            strategy_family="anomaly",
            horizon_minutes=(15, 30),  # type: ignore[arg-type]
            take_profit_atr_1440=1.0,
            stop_loss_atr_1440=1.0,
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_broad_anomaly_strategy_wraps_detector_behind_base_contract() -> None:
    strategy = BroadAnomalyStrategy()
    rows = [_candle_row(index, close=100.0) for index in range(20)]
    rows.append(_candle_row(20, close=104.0, quote_volume=10_000.0))

    trigger_frame = strategy.generate_triggers(pl.DataFrame(rows))
    custom_features = strategy.generate_custom_features(pl.DataFrame({"x": [1, 2]}))

    validate_trigger_frame(trigger_frame)
    assert strategy.metadata.strategy_name == "broad_anomaly_v1_h30"
    assert strategy.metadata.strategy_family == "anomaly"
    assert strategy.metadata.strategy_contract_version == "base_strategy_v1"
    assert strategy.metadata.horizon_minutes == 30
    assert strategy.metadata.take_profit_atr_1440 == 2.0
    assert strategy.metadata.stop_loss_atr_1440 == 1.1
    assert strategy.required_data_streams == {"open_interest": False, "liquidations": False}
    assert trigger_frame.height == 1
    assert "state_time" in trigger_frame.columns
    assert "state_time_ms" not in trigger_frame.columns
    assert "event_start_time" in trigger_frame.columns
    assert "minutes_since_start" in trigger_frame.columns
    assert trigger_frame["minutes_since_start"].to_list() == [1]
    assert trigger_frame["is_trigger"].to_list() == [True]
    assert custom_features.height == 0
    assert custom_features.columns == []


def test_trigger_frame_rejects_internal_unix_ms_time_columns() -> None:
    frame = pl.DataFrame({
        "symbol": ["AAA"],
        "state_time_ms": [BASE_TS],
        "event_start_time_ms": [BASE_TS],
        "is_trigger": [True],
        "event_id": ["evt"],
    })

    with pytest.raises(StrategyContractError, match="native datetime"):
        validate_trigger_frame(frame)


def test_trigger_frame_requires_consistent_minutes_since_start() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["AAA"],
            "state_time": [_dt(BASE_TS + 120_000)],
            "event_start_time": [_dt(BASE_TS)],
            "minutes_since_start": [1],
            "is_trigger": [True],
            "event_id": ["evt"],
        },
        schema={
            "symbol": pl.String,
            "state_time": pl.Datetime("ms", "UTC"),
            "event_start_time": pl.Datetime("ms", "UTC"),
            "minutes_since_start": pl.Int64,
            "is_trigger": pl.Boolean,
            "event_id": pl.String,
        },
    )

    with pytest.raises(StrategyContractError, match="minutes_since_start"):
        validate_trigger_frame(frame)


def test_strategy_registry_exposes_broad_anomaly_by_contract_name() -> None:
    entries = available_strategies()
    strategy = get_strategy("broad_anomaly_v1_h30")

    assert [entry.strategy_name for entry in entries] == [
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
    ]
    assert strategy.metadata.strategy_name == "broad_anomaly_v1_h30"
    assert strategy.metadata.strategy_contract_version == "base_strategy_v1"
