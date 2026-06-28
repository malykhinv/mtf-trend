from __future__ import annotations

from datetime import datetime, timezone

import polars as pl
import pytest

from anomaly_science.strategy import StrategyContractError, StrategyMetadata, validate_trigger_frame
from anomaly_science.strategy.base import StrategyFeatureContext
from anomaly_science.strategy.registry import available_strategies, get_strategy, specified_not_implemented_strategy_names
from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.contracts.market import Candle1m, OpenInterest5m
from anomaly_science.strategy.anomaly import (
    BroadAnomalyStrategy,
    PostAnomalyExtensionConfig,
    PostAnomalyExtensionStrategy,
    PostPumpDistributionStrategy,
)


BASE_TS = 1_704_067_200_000


def _dt(value_ms: int) -> datetime:
    return datetime.fromtimestamp(value_ms / 1000, tz=timezone.utc)


def _candle_row(index: int, *, close: float, quote_volume: float = 100.0, symbol: str = "AAA/USDT:USDT", number_of_trades: float = 10.0) -> dict[str, object]:
    open_time_ms = BASE_TS + index * 60_000
    high = max(100.0, close) + 0.10
    low = min(100.0, close) - 0.10
    return {
        "symbol": symbol,
        "open_time_ms": open_time_ms,
        "available_time_ms": open_time_ms + 60_000,
        "open": 100.0,
        "high": high,
        "low": low,
        "close": close,
        "volume": 1.0,
        "quote_volume": quote_volume,
        "number_of_trades": number_of_trades,
        "taker_buy_quote_volume": quote_volume * 0.5,
    }


def test_strategy_metadata_validates_required_base_contract_fields() -> None:
    with pytest.raises(StrategyContractError, match="horizon_minutes"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v2_structural_execution",
            strategy_family="anomaly",
            horizon_minutes=0,
            allowed_horizons=(15, 30, 60),
            default_horizon_minutes=30,
            execution_policy_version="structural_v1",
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_strategy_metadata_rejects_multi_horizon_selected_values() -> None:
    with pytest.raises(StrategyContractError, match="one selected int"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v2_structural_execution",
            strategy_family="anomaly",
            horizon_minutes=(15, 30),  # type: ignore[arg-type]
            allowed_horizons=(15, 30, 60),
            default_horizon_minutes=30,
            execution_policy_version="structural_v1",
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_strategy_metadata_rejects_allowed_horizons_outside_core_whitelist() -> None:
    with pytest.raises(StrategyContractError, match="allowed_horizons"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v2_structural_execution",
            strategy_family="anomaly",
            horizon_minutes=30,
            allowed_horizons=(15, 30, 32),
            default_horizon_minutes=30,
            execution_policy_version="structural_v1",
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_strategy_metadata_rejects_selected_horizon_outside_allowed_set() -> None:
    with pytest.raises(StrategyContractError, match="horizon_minutes must be one of"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v2_structural_execution",
            strategy_family="anomaly",
            horizon_minutes=120,
            allowed_horizons=(15, 30, 60),
            default_horizon_minutes=30,
            execution_policy_version="structural_v1",
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_strategy_metadata_rejects_default_horizon_outside_allowed_set() -> None:
    with pytest.raises(StrategyContractError, match="default_horizon_minutes must be one of"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v2_structural_execution",
            strategy_family="anomaly",
            horizon_minutes=30,
            allowed_horizons=(15, 30, 60),
            default_horizon_minutes=120,
            execution_policy_version="structural_v1",
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )



def test_broad_anomaly_strategy_wraps_detector_behind_base_contract() -> None:
    strategy = BroadAnomalyStrategy()
    rows = [_candle_row(index, close=100.0) for index in range(20)]
    rows.append(_candle_row(20, close=104.0, quote_volume=10_000.0))

    trigger_frame = strategy.generate_triggers(pl.DataFrame(rows))
    custom_features = strategy.generate_custom_features(
        StrategyFeatureContext(
            event_id="event",
            symbol="AAA/USDT:USDT",
            event_start_time_ms=BASE_TS,
            snapshot_time_ms=BASE_TS,
            feature_cutoff_time_ms=BASE_TS,
            market_rows_asof=(),
            event_rows_asof=(),
            open_interest_rows_asof=(),
            liquidation_rows_asof=(),
            core_features={},
        )
    )

    validate_trigger_frame(trigger_frame)
    assert strategy.metadata.strategy_name == "broad_anomaly_v1_h30"
    assert strategy.metadata.strategy_family == "anomaly"
    assert strategy.metadata.strategy_contract_version == "base_strategy_v2_structural_execution"
    assert strategy.metadata.horizon_minutes == 30
    assert strategy.metadata.allowed_horizons == (15, 30, 60)
    assert strategy.metadata.default_horizon_minutes == 30
    assert strategy.metadata.execution_policy_version == "generic_anomaly_structural_execution_v1"
    assert {policy.policy_id for policy in strategy.execution_policies.stop_policies} == {
        "long_running_low_close_then_swing_low_trail",
        "short_running_high_close_then_swing_high_trail",
    }
    assert strategy.required_data_streams == {"open_interest": False, "liquidations": False}
    assert trigger_frame.height == 1
    assert "state_time" in trigger_frame.columns
    assert "state_time_ms" not in trigger_frame.columns
    assert "event_start_time" in trigger_frame.columns
    assert "minutes_since_start" in trigger_frame.columns
    assert trigger_frame["minutes_since_start"].to_list() == [1]
    assert trigger_frame["is_trigger"].to_list() == [True]
    assert trigger_frame["trigger_component"].to_list() == ["one_shot_spike"]
    assert "one_shot_spike" in trigger_frame["trigger_components"].to_list()[0]
    assert custom_features == {}


def test_post_anomaly_extension_strategy_triggers_after_causal_broad_seed_extension() -> None:
    strategy = PostAnomalyExtensionStrategy(
        config=PostAnomalyExtensionConfig(
            broad_detector_config=BroadAnomalyDetectorConfig(
                baseline_bars=5,
                min_baseline_bars=5,
                min_abs_return_pct=0.03,
                min_quote_volume_zscore=99.0,
                min_volume_zscore=99.0,
                min_trade_count_zscore=99.0,
                min_range_zscore=99.0,
            ),
            min_abs_extension_return_from_seed_open=0.05,
            min_minutes_since_event_start=3,
            max_minutes_since_event_start=20,
        )
    )
    rows = [
        _candle_row(0, close=100.0),
        _candle_row(1, close=100.1),
        _candle_row(2, close=99.9),
        _candle_row(3, close=100.0),
        _candle_row(4, close=100.1),
        _candle_row(5, close=103.5),  # broad seed, available at minute 6
        _candle_row(6, close=104.0),
        _candle_row(7, close=105.5),  # first extension trigger, available at minute 8
        _candle_row(8, close=107.0),
    ]

    trigger_frame = strategy.generate_triggers(pl.DataFrame(rows))

    validate_trigger_frame(trigger_frame)
    assert strategy.metadata.strategy_name == "post_anomaly_extension_v1_h120"
    assert strategy.metadata.allowed_horizons == (60, 120, 180)
    assert strategy.required_data_streams == {"open_interest": True, "liquidations": True}
    assert trigger_frame.height == 1
    assert trigger_frame["trigger_component"].to_list() == ["post_anomaly_extension"]
    assert trigger_frame["trigger_components"].to_list() == ["post_anomaly_extension;upside_extension"]
    assert trigger_frame["event_start_time"].to_list()[0] == _dt(BASE_TS + 5 * 60_000)
    assert trigger_frame["state_time"].to_list()[0] == _dt(BASE_TS + 8 * 60_000)
    assert trigger_frame["minutes_since_start"].to_list() == [3]
    assert trigger_frame["initial_move_pct"].to_list()[0] == pytest.approx(0.055)


def test_post_pump_distribution_strategy_uses_causal_daily_return_and_same_minute_cross_section() -> None:
    strategy = PostPumpDistributionStrategy()
    rows: list[dict[str, object]] = []
    for index in range(3):
        rows.append(_candle_row(index, symbol="AAA/USDT:USDT", close=100.0 + index, number_of_trades=10.0))
        rows.append(_candle_row(index, symbol="BBB/USDT:USDT", close=100.0, number_of_trades=20.0))
        rows.append(_candle_row(index, symbol="CCC/USDT:USDT", close=100.0, number_of_trades=30.0))
    rows.append(_candle_row(3, symbol="AAA/USDT:USDT", close=131.0, number_of_trades=1_000.0))
    rows.append(_candle_row(3, symbol="BBB/USDT:USDT", close=100.0, number_of_trades=20.0))
    rows.append(_candle_row(3, symbol="CCC/USDT:USDT", close=100.0, number_of_trades=30.0))
    rows.append(_candle_row(4, symbol="AAA/USDT:USDT", close=132.0, number_of_trades=1_100.0))
    rows.append(_candle_row(4, symbol="BBB/USDT:USDT", close=100.0, number_of_trades=20.0))
    rows.append(_candle_row(4, symbol="CCC/USDT:USDT", close=100.0, number_of_trades=30.0))

    trigger_frame = strategy.generate_triggers(pl.DataFrame(rows))

    validate_trigger_frame(trigger_frame)
    assert strategy.metadata.strategy_name == "post_pump_distribution_v1_h120"
    assert strategy.metadata.allowed_horizons == (60, 120, 180)
    assert strategy.required_data_streams == {"open_interest": False, "liquidations": False}
    assert trigger_frame.height == 1
    assert trigger_frame["symbol"].to_list() == ["AAA/USDT:USDT"]
    assert trigger_frame["trigger_component"].to_list() == ["post_pump_distribution"]
    assert trigger_frame["daily_return_asof_t"].to_list()[0] == pytest.approx(0.31)
    assert trigger_frame["trade_count_market_percentile_asof_t"].to_list()[0] == pytest.approx(1.0)


def test_post_pump_custom_features_describe_mechanics_and_positioning_without_actor_claims() -> None:
    strategy = PostPumpDistributionStrategy()
    candles = tuple(
        Candle1m(
            symbol="AAA/USDT:USDT",
            open_time_ms=BASE_TS + index * 60_000,
            available_time_ms=BASE_TS + (index + 1) * 60_000,
            open=100.0 + index * 0.1,
            high=100.4 + index * 0.1,
            low=99.8 + index * 0.1,
            close=100.2 + index * 0.1,
            volume=10.0,
            quote_volume=1_000.0 + index * 10.0,
            number_of_trades=100.0 + index,
            taker_buy_quote_volume=600.0 + index * 6.0,
        )
        for index in range(70)
    )
    oi = (
        OpenInterest5m("AAA/USDT:USDT", BASE_TS, BASE_TS + 300_000, 1_000.0, "test"),
        OpenInterest5m("AAA/USDT:USDT", BASE_TS + 3_300_000, BASE_TS + 3_600_000, 1_100.0, "test"),
        OpenInterest5m("AAA/USDT:USDT", BASE_TS + 3_900_000, BASE_TS + 4_200_000, 1_200.0, "test"),
    )
    context = StrategyFeatureContext(
        event_id="pump",
        symbol="AAA/USDT:USDT",
        event_start_time_ms=candles[-10].open_time_ms,
        snapshot_time_ms=candles[-1].available_time_ms,
        feature_cutoff_time_ms=candles[-1].available_time_ms,
        market_rows_asof=candles,
        event_rows_asof=candles[-10:],
        open_interest_rows_asof=oi,
        liquidation_rows_asof=(),
        core_features={},
    )

    features = strategy.generate_custom_features(context)

    assert set(features) == {spec.name for spec in strategy.custom_feature_catalog}
    assert features["max_1m_high_return"] > 0.0
    assert features["taker_imbalance_event"] > 0.0
    assert features["oi_change_60m"] > 0.0
    assert features["price_up_oi_up"] is True
    proxy_specs = {spec.name: spec.identifiability for spec in strategy.custom_feature_catalog}
    assert proxy_specs["retail_frenzy_proxy"] == "proxy"
    assert proxy_specs["price_up_oi_up"] == "latent_hypothesis"


def test_trigger_frame_rejects_internal_unix_ms_time_columns() -> None:
    frame = pl.DataFrame({
        "symbol": ["AAA"],
        "state_time_ms": [BASE_TS],
        "event_start_time_ms": [BASE_TS],
        "event_detection_time_ms": [BASE_TS],
        "is_trigger": [True],
        "event_id": ["evt"],
    })

    with pytest.raises(StrategyContractError, match="native datetime"):
        validate_trigger_frame(frame)


def test_trigger_frame_requires_optional_lifecycle_times_to_be_native_datetime() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["AAA"],
            "state_time": [_dt(BASE_TS)],
            "event_start_time": [_dt(BASE_TS)],
            "event_detection_time": [BASE_TS],
            "minutes_since_start": [0],
            "is_trigger": [True],
            "event_id": ["evt"],
        },
        schema={
            "symbol": pl.String,
            "state_time": pl.Datetime("ms", "UTC"),
            "event_start_time": pl.Datetime("ms", "UTC"),
            "event_detection_time": pl.Int64,
            "minutes_since_start": pl.Int64,
            "is_trigger": pl.Boolean,
            "event_id": pl.String,
        },
    )

    with pytest.raises(StrategyContractError, match="event_detection_time"):
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


def test_trigger_frame_rejects_event_id_collision_across_lifecycle_identity() -> None:
    frame = pl.DataFrame(
        {
            "symbol": ["AAA", "BBB"],
            "state_time": [_dt(BASE_TS), _dt(BASE_TS)],
            "event_start_time": [_dt(BASE_TS), _dt(BASE_TS)],
            "minutes_since_start": [0, 0],
            "is_trigger": [True, True],
            "event_id": ["evt", "evt"],
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

    with pytest.raises(StrategyContractError, match="event_id collision"):
        validate_trigger_frame(frame)


def test_strategy_registry_exposes_broad_anomaly_by_contract_name() -> None:
    entries = available_strategies()
    strategy = get_strategy("broad_anomaly_v1_h30")

    assert [entry.strategy_name for entry in entries] == [
        "broad_anomaly_v1_h15",
        "broad_anomaly_v1_h30",
        "broad_anomaly_v1_h60",
        "post_anomaly_extension_v1_h60",
        "post_anomaly_extension_v1_h120",
        "post_anomaly_extension_v1_h180",
        "post_pump_distribution_v1_h60",
        "post_pump_distribution_v1_h120",
        "post_pump_distribution_v1_h180",
    ]
    assert strategy.metadata.strategy_name == "broad_anomaly_v1_h30"
    assert strategy.metadata.strategy_contract_version == "base_strategy_v2_structural_execution"
    post_extension = get_strategy("post_anomaly_extension_v1_h120")
    assert post_extension.metadata.strategy_name == "post_anomaly_extension_v1_h120"
    assert post_extension.required_data_streams == {"open_interest": True, "liquidations": True}
    post_pump = get_strategy("post_pump_distribution_v1_h120")
    assert post_pump.metadata.strategy_name == "post_pump_distribution_v1_h120"
    assert post_pump.required_data_streams == {"open_interest": False, "liquidations": False}
    assert specified_not_implemented_strategy_names() == ()
