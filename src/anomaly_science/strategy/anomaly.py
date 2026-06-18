from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd
import polars as pl

from anomaly_science.contracts.events import AnomalyEvent
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.events.config import BroadAnomalyDetectorConfig
from anomaly_science.events.detector import detect_broad_anomaly_events
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION
from anomaly_science.labels.config import OutcomeLabelConfig
from anomaly_science.strategy.base import (
    BaseStrategy,
    INTERNAL_TIME_DTYPE,
    StrategyMetadata,
    utc_ms_to_internal_datetime,
    validate_required_data_streams,
    validate_trigger_frame,
)


ANOMALY_STRATEGY_DEFAULTS: dict[str, dict[str, float | int | str]] = {
    "broad_anomaly_v1_h15": {"horizon_minutes": 15, "take_profit_atr_1440": 1.5, "stop_loss_atr_1440": 1.0},
    "broad_anomaly_v1_h30": {"horizon_minutes": 30, "take_profit_atr_1440": 2.0, "stop_loss_atr_1440": 1.1},
    "broad_anomaly_v1_h60": {"horizon_minutes": 60, "take_profit_atr_1440": 2.5, "stop_loss_atr_1440": 1.2},
    "post_anomaly_extension_v1_h60": {"horizon_minutes": 60, "take_profit_atr_1440": 2.5, "stop_loss_atr_1440": 1.3},
    "post_anomaly_extension_v1_h120": {"horizon_minutes": 120, "take_profit_atr_1440": 3.0, "stop_loss_atr_1440": 1.5},
    "post_pump_distribution_v1_h60": {"horizon_minutes": 60, "take_profit_atr_1440": 2.0, "stop_loss_atr_1440": 1.2},
    "post_pump_distribution_v1_h120": {"horizon_minutes": 120, "take_profit_atr_1440": 3.0, "stop_loss_atr_1440": 1.5},
    "post_pump_distribution_v1_h180": {"horizon_minutes": 180, "take_profit_atr_1440": 4.0, "stop_loss_atr_1440": 2.0},
}

BROAD_ANOMALY_REQUIRED_DATA_STREAMS: dict[str, bool] = {
    "open_interest": False,
    "liquidations": False,
}

POST_PUMP_REQUIRED_DATA_STREAMS: dict[str, bool] = {
    "open_interest": True,
    "liquidations": True,
}

_TRIGGER_FRAME_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.String,
    "symbol": pl.String,
    "state_time": INTERNAL_TIME_DTYPE,
    "event_start_time": INTERNAL_TIME_DTYPE,
    "minutes_since_start": pl.Int64,
    "is_trigger": pl.Boolean,
    "event_detection_time": INTERNAL_TIME_DTYPE,
    "seed_time": INTERNAL_TIME_DTYPE,
    "seed_open": pl.Float64,
    "seed_high": pl.Float64,
    "seed_low": pl.Float64,
    "seed_close": pl.Float64,
    "initial_move_pct": pl.Float64,
    "initial_volume_zscore": pl.Float64,
    "initial_quote_volume_zscore": pl.Float64,
    "initial_trade_count_zscore": pl.Float64,
    "technical_noise_shock": pl.Boolean,
    "raw_candle_gap_minutes": pl.Float64,
    "excluded_by_data_quality_gate": pl.Boolean,
    "detector_version": pl.String,
}


def anomaly_strategy_metadata(strategy_name: str) -> StrategyMetadata:
    defaults = ANOMALY_STRATEGY_DEFAULTS[strategy_name]
    return StrategyMetadata(
        strategy_name=strategy_name,
        strategy_version="1.0.0",
        strategy_contract_version="base_strategy_v1",
        strategy_family="anomaly",
        horizon_minutes=int(defaults["horizon_minutes"]),
        take_profit_atr_1440=float(defaults["take_profit_atr_1440"]),
        stop_loss_atr_1440=float(defaults["stop_loss_atr_1440"]),
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        label_schema_version=OutcomeLabelConfig().label_schema_version,
    )


@dataclass(frozen=True, slots=True)
class BroadAnomalyStrategy(BaseStrategy):
    config: BroadAnomalyDetectorConfig = BroadAnomalyDetectorConfig()
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("broad_anomaly_v1_h30"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(BROAD_ANOMALY_REQUIRED_DATA_STREAMS)
        return BROAD_ANOMALY_REQUIRED_DATA_STREAMS

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        candles = normalize_candles_1m(pandas_frame)
        events = self.generate_events(candles)
        trigger_frame = _events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[AnomalyEvent, ...]:
        return detect_broad_anomaly_events(candles_1m, config=self.config)

    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame()


def make_broad_anomaly_strategy(
    *,
    strategy_name: str,
    config: BroadAnomalyDetectorConfig | None = None,
) -> BroadAnomalyStrategy:
    if not strategy_name.startswith("broad_anomaly_v1_h"):
        raise ValueError(f"not a broad anomaly strategy variant: {strategy_name!r}")
    return BroadAnomalyStrategy(
        config=config or BroadAnomalyDetectorConfig(),
        _metadata=anomaly_strategy_metadata(strategy_name),
    )


def _events_to_trigger_frame(events: Sequence[AnomalyEvent]) -> pl.DataFrame:
    rows = [_event_to_trigger_row(event) for event in events]
    if not rows:
        return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=_TRIGGER_FRAME_SCHEMA, orient="row")


def _event_to_trigger_row(event: AnomalyEvent) -> dict[str, object]:
    state_time_ms = event.event_detection_time_ms
    return {
        "event_id": event.event_id,
        "symbol": event.symbol,
        "state_time": utc_ms_to_internal_datetime(state_time_ms),
        "event_start_time": utc_ms_to_internal_datetime(event.event_start_time_ms),
        "minutes_since_start": (state_time_ms - event.event_start_time_ms) // ONE_MINUTE_MS,
        "is_trigger": True,
        "event_detection_time": utc_ms_to_internal_datetime(event.event_detection_time_ms),
        "seed_time": utc_ms_to_internal_datetime(event.seed_time_ms),
        "seed_open": event.seed_open,
        "seed_high": event.seed_high,
        "seed_low": event.seed_low,
        "seed_close": event.seed_close,
        "initial_move_pct": event.initial_move_pct,
        "initial_volume_zscore": event.initial_volume_zscore,
        "initial_quote_volume_zscore": event.initial_quote_volume_zscore,
        "initial_trade_count_zscore": event.initial_trade_count_zscore,
        "technical_noise_shock": event.technical_noise_shock,
        "raw_candle_gap_minutes": event.raw_candle_gap_minutes,
        "excluded_by_data_quality_gate": event.excluded_by_data_quality_gate,
        "detector_version": event.detector_version,
    }
