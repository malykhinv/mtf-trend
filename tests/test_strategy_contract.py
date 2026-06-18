from __future__ import annotations

import pandas as pd
import pytest

from anomaly_science.strategy import StrategyContractError, StrategyMetadata
from anomaly_science.strategy.anomaly import BroadAnomalyStrategy

from anomaly_science.contracts.market import Candle1m


BASE_TS = 1_704_067_200_000


def _candle(index: int, *, close: float, quote_volume: float = 100.0) -> Candle1m:
    open_time_ms = BASE_TS + index * 60_000
    high = max(100.0, close) + 0.10
    low = min(100.0, close) - 0.10
    return Candle1m(
        symbol="AAA/USDT:USDT",
        open_time_ms=open_time_ms,
        available_time_ms=open_time_ms + 60_000,
        open=100.0,
        high=high,
        low=low,
        close=close,
        volume=1.0,
        quote_volume=quote_volume,
        number_of_trades=10.0,
        taker_buy_quote_volume=quote_volume * 0.5,
    )


def test_strategy_metadata_validates_required_base_contract_fields() -> None:
    with pytest.raises(StrategyContractError, match="primary_horizon_minutes"):
        StrategyMetadata(
            strategy_name="bad",
            strategy_version="1.0.0",
            strategy_contract_version="base_strategy_v1",
            strategy_family="anomaly",
            label_horizons_minutes=(15, 30, 60),
            primary_horizon_minutes=120,
            take_profit_atr=1.0,
            stop_loss_atr=1.0,
            feature_schema_version="features_v1",
            label_schema_version="labels_v1",
        )


def test_broad_anomaly_strategy_wraps_detector_behind_base_contract() -> None:
    strategy = BroadAnomalyStrategy()
    candles = [_candle(index, close=100.0) for index in range(20)]
    candles.append(_candle(20, close=104.0, quote_volume=10_000.0))

    events = strategy.generate_events(candles)
    custom_features = strategy.generate_custom_features(pd.DataFrame({"x": [1, 2]}))

    assert strategy.metadata.strategy_name == "broad_anomaly_v1"
    assert strategy.metadata.strategy_family == "anomaly"
    assert strategy.metadata.strategy_contract_version == "base_strategy_v1"
    assert len(events) == 1
    assert list(custom_features.index) == [0, 1]
    assert list(custom_features.columns) == []
