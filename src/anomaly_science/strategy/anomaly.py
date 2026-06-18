from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

import pandas as pd
import polars as pl

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.events.detector import detect_broad_anomaly_events
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION
from anomaly_science.labels.config import OutcomeLabelConfig
from anomaly_science.strategy.base import StrategyMetadata


@dataclass(frozen=True, slots=True)
class BroadAnomalyStrategy:
    config: BroadAnomalyDetectorConfig = BroadAnomalyDetectorConfig()
    metadata: StrategyMetadata = StrategyMetadata(
        strategy_name="broad_anomaly_v1",
        strategy_version="1.0.0",
        strategy_contract_version="base_strategy_v1",
        strategy_family="anomaly",
        horizon_minutes=30,
        take_profit_atr=1.0,
        stop_loss_atr=1.0,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        label_schema_version=OutcomeLabelConfig().label_schema_version,
    )

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.Series:
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        candles = normalize_candles_1m(pandas_frame)
        event_seed_times = {event.seed_time_ms for event in self.generate_events(candles)}
        values = [int(value) in event_seed_times for value in pandas_frame["open_time_ms"]]
        return pl.Series("is_trigger", values)

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[AnomalyEvent, ...]:
        return detect_broad_anomaly_events(candles_1m, config=self.config)

    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame()
