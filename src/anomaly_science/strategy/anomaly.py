from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd
import polars as pl

from anomaly_science.contracts.events import StrategyEvent
from anomaly_science.contracts.market import Candle1m, ONE_MINUTE_MS
from anomaly_science.data.normalized import normalize_candles_1m
from anomaly_science.strategy.anomaly_config import BroadAnomalyDetectorConfig
from anomaly_science.strategy.anomaly_detector import detect_broad_anomaly_events, detect_broad_anomaly_events_from_frame
from anomaly_science.features.catalog import FEATURE_SCHEMA_VERSION
from anomaly_science.labels.config import OutcomeLabelConfig
from anomaly_science.strategy.base import (
    BaseStrategy,
    INTERNAL_TIME_DTYPE,
    StrategyMetadata,
    StrategyCustomFeatureSpec,
    StrategyFeatureContext,
    utc_ms_to_internal_datetime,
    validate_required_data_streams,
    validate_trigger_frame,
)
from anomaly_science.strategy.execution import (
    GENERIC_ANOMALY_EXECUTION_POLICIES,
    StrategyExecutionPolicies,
)
from anomaly_science.strategy.pump_fade.spec import PUMP_MARKET_MECHANICS_FEATURES


BROAD_ANOMALY_ALLOWED_HORIZONS: tuple[int, ...] = (15, 30, 60)
POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS: tuple[int, ...] = (60, 120, 180)
POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS: tuple[int, ...] = (60, 120, 180)

ANOMALY_STRATEGY_DEFAULTS: dict[str, dict[str, object]] = {
    "broad_anomaly_v1_h15": {
        "horizon_minutes": 15,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
    },
    "broad_anomaly_v1_h30": {
        "horizon_minutes": 30,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
    },
    "broad_anomaly_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
    },
    "post_anomaly_extension_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
    "post_anomaly_extension_v1_h120": {
        "horizon_minutes": 120,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
    "post_anomaly_extension_v1_h180": {
        "horizon_minutes": 180,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
    "post_pump_distribution_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
    "post_pump_distribution_v1_h120": {
        "horizon_minutes": 120,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
    "post_pump_distribution_v1_h180": {
        "horizon_minutes": 180,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
    },
}

BROAD_ANOMALY_REQUIRED_DATA_STREAMS: dict[str, bool] = {
    "open_interest": False,
    "liquidations": False,
}

POST_ANOMALY_EXTENSION_REQUIRED_DATA_STREAMS: dict[str, bool] = {
    "open_interest": True,
    "liquidations": True,
}

POST_PUMP_REQUIRED_DATA_STREAMS: dict[str, bool] = {
    "open_interest": False,
    "liquidations": False,
}

ANOMALY_RELAXED_GEOMETRY_FEATURES: tuple[StrategyCustomFeatureSpec, ...] = tuple(
    StrategyCustomFeatureSpec(
        name=name,
        family="anomaly_relaxed_geometry",
        dtype=dtype,
        description="Strategy-owned causal anomaly geometry compatibility feature.",
    )
    for name, dtype in (
        ("initial_pump_height_core_atr_1440", "float64"),
        ("post_pump_consolidation_minutes", "int64"),
        ("consolidation_width_ratio", "float64"),
        ("shelf_low_asof_t", "float64"),
        ("shelf_high_asof_t", "float64"),
        ("current_low_minus_shelf_low_core_atr_1440", "float64"),
        ("current_close_minus_shelf_low_core_atr_1440", "float64"),
        ("current_high_minus_shelf_high_core_atr_1440", "float64"),
        ("minutes_spent_below_shelf", "int64"),
        ("minutes_since_reclaim", "int64"),
        ("volume_on_sweep_percentile", "float64"),
        ("trade_count_on_sweep_percentile", "float64"),
        ("cvd_change_during_sweep", "float64"),
        ("oi_change_during_sweep", "float64"),
        ("liq_intensity_during_sweep", "float64"),
    )
)


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
    "trigger_component": pl.String,
    "trigger_components": pl.String,
    "technical_noise_shock": pl.Boolean,
    "raw_candle_gap_minutes": pl.Float64,
    "excluded_by_data_quality_gate": pl.Boolean,
    "daily_return_asof_t": pl.Float64,
    "trade_count_market_percentile_asof_t": pl.Float64,
    "detector_version": pl.String,
}


def anomaly_strategy_metadata(strategy_name: str) -> StrategyMetadata:
    defaults = ANOMALY_STRATEGY_DEFAULTS[strategy_name]
    return StrategyMetadata(
        strategy_name=strategy_name,
        strategy_version="1.0.0",
        strategy_contract_version="base_strategy_v2_structural_execution",
        strategy_family="anomaly",
        horizon_minutes=int(defaults["horizon_minutes"]),
        allowed_horizons=tuple(int(horizon) for horizon in defaults["allowed_horizons"]),
        default_horizon_minutes=int(defaults["default_horizon_minutes"]),
        execution_policy_version=GENERIC_ANOMALY_EXECUTION_POLICIES.policy_version,
        feature_schema_version=FEATURE_SCHEMA_VERSION,
        label_schema_version=OutcomeLabelConfig().label_schema_version,
    )


@dataclass(frozen=True, slots=True)
class PostAnomalyExtensionConfig:
    detector_version: str = "post_anomaly_extension_v1"
    broad_detector_config: BroadAnomalyDetectorConfig = field(default_factory=BroadAnomalyDetectorConfig)
    min_abs_extension_return_from_seed_open: float = 0.05
    min_minutes_since_event_start: int = 5
    max_minutes_since_event_start: int = 240

    def __post_init__(self) -> None:
        if not self.detector_version:
            raise ValueError("detector_version is required")
        if self.min_abs_extension_return_from_seed_open <= 0.0:
            raise ValueError("min_abs_extension_return_from_seed_open must be positive")
        if self.min_minutes_since_event_start < 0:
            raise ValueError("min_minutes_since_event_start must be non-negative")
        if self.max_minutes_since_event_start < self.min_minutes_since_event_start:
            raise ValueError("max_minutes_since_event_start must be >= min_minutes_since_event_start")


@dataclass(frozen=True, slots=True)
class PostPumpDistributionConfig:
    detector_version: str = "post_pump_distribution_v1"
    min_daily_return_asof_t: float = 0.30
    min_trade_count_market_percentile_asof_t: float = 0.99

    def __post_init__(self) -> None:
        if not self.detector_version:
            raise ValueError("detector_version is required")
        if self.min_daily_return_asof_t <= 0.0:
            raise ValueError("min_daily_return_asof_t must be positive")
        if not 0.0 <= self.min_trade_count_market_percentile_asof_t <= 1.0:
            raise ValueError("min_trade_count_market_percentile_asof_t must be in [0, 1]")


@dataclass(frozen=True, slots=True)
class BroadAnomalyStrategy(BaseStrategy):
    config: BroadAnomalyDetectorConfig = BroadAnomalyDetectorConfig()
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("broad_anomaly_v1_h30"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def trigger_config(self) -> object:
        return self.config

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(BROAD_ANOMALY_REQUIRED_DATA_STREAMS)
        return BROAD_ANOMALY_REQUIRED_DATA_STREAMS

    @property
    def execution_policies(self) -> StrategyExecutionPolicies:
        return GENERIC_ANOMALY_EXECUTION_POLICIES

    @property
    def custom_feature_catalog(self) -> tuple[StrategyCustomFeatureSpec, ...]:
        return ()

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        events = detect_broad_anomaly_events_from_frame(market_frame_asof.to_pandas(), config=self.config)
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_triggers_from_pandas(self, market_frame_asof: pd.DataFrame) -> pl.DataFrame:
        if market_frame_asof.empty:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        events = detect_broad_anomaly_events_from_frame(market_frame_asof, config=self.config)
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_broad_anomaly_events(candles_1m, config=self.config)

    def generate_custom_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        del context
        return {}

    def generate_artifact_compatibility_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        return _anomaly_relaxed_geometry_features(context)


@dataclass(frozen=True, slots=True)
class PostAnomalyExtensionStrategy(BaseStrategy):
    config: PostAnomalyExtensionConfig = field(default_factory=PostAnomalyExtensionConfig)
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("post_anomaly_extension_v1_h120"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def trigger_config(self) -> object:
        return self.config

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(POST_ANOMALY_EXTENSION_REQUIRED_DATA_STREAMS)
        return POST_ANOMALY_EXTENSION_REQUIRED_DATA_STREAMS

    @property
    def execution_policies(self) -> StrategyExecutionPolicies:
        return GENERIC_ANOMALY_EXECUTION_POLICIES

    @property
    def custom_feature_catalog(self) -> tuple[StrategyCustomFeatureSpec, ...]:
        return PUMP_MARKET_MECHANICS_FEATURES

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        return self.generate_triggers_from_pandas(pandas_frame)

    def generate_triggers_from_pandas(self, market_frame_asof: pd.DataFrame) -> pl.DataFrame:
        if market_frame_asof.empty:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = market_frame_asof
        events = self.generate_events(normalize_candles_1m(pandas_frame))
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_post_anomaly_extension_events(candles_1m, config=self.config)

    def generate_custom_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        return _pump_market_mechanics_features(context)

    def generate_artifact_compatibility_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        return _anomaly_relaxed_geometry_features(context)


@dataclass(frozen=True, slots=True)
class PostPumpDistributionStrategy(BaseStrategy):
    config: PostPumpDistributionConfig = PostPumpDistributionConfig()
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("post_pump_distribution_v1_h120"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def trigger_config(self) -> object:
        return self.config

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(POST_PUMP_REQUIRED_DATA_STREAMS)
        return POST_PUMP_REQUIRED_DATA_STREAMS

    @property
    def execution_policies(self) -> StrategyExecutionPolicies:
        return GENERIC_ANOMALY_EXECUTION_POLICIES

    @property
    def custom_feature_catalog(self) -> tuple[StrategyCustomFeatureSpec, ...]:
        return PUMP_MARKET_MECHANICS_FEATURES

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        return self.generate_triggers_from_pandas(pandas_frame)

    def generate_triggers_from_pandas(self, market_frame_asof: pd.DataFrame) -> pl.DataFrame:
        if market_frame_asof.empty:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = market_frame_asof
        events = self.generate_events(normalize_candles_1m(pandas_frame))
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_post_pump_distribution_events(candles_1m, config=self.config)

    def generate_custom_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        return _pump_market_mechanics_features(context)

    def generate_artifact_compatibility_features(self, context: StrategyFeatureContext) -> Mapping[str, object]:
        return _anomaly_relaxed_geometry_features(context)


def _anomaly_relaxed_geometry_features(context: StrategyFeatureContext) -> Mapping[str, object]:
    missing = {spec.name: None for spec in ANOMALY_RELAXED_GEOMETRY_FEATURES}
    state = context.state_asof
    event = tuple(context.event_rows_asof)
    if state is None or not event:
        return missing
    current = event[-1]
    first = event[0]
    atr_value = context.core_features.get("core_atr_1440")
    atr = float(atr_value) if isinstance(atr_value, (int, float)) and atr_value > 0 else None
    shelf_low = state.structural_low_asof_t
    shelf_high = state.structural_high_asof_t
    pump_height = max(state.running_high_asof_t - first.open, 0.0)
    values = dict(missing)
    values.update(
        initial_pump_height_core_atr_1440=pump_height / atr if atr else None,
        shelf_low_asof_t=shelf_low,
        shelf_high_asof_t=shelf_high,
    )
    if shelf_low is not None:
        values["minutes_spent_below_shelf"] = sum(1 for candle in event if candle.close < shelf_low)
        reclaim_time = None
        previous_close = None
        for candle in event:
            if previous_close is not None and previous_close < shelf_low <= candle.close:
                reclaim_time = candle.available_time_ms
            previous_close = candle.close
        values["minutes_since_reclaim"] = (
            max((context.snapshot_time_ms - reclaim_time) // ONE_MINUTE_MS, 0)
            if reclaim_time is not None
            else None
        )
        if atr:
            values["current_low_minus_shelf_low_core_atr_1440"] = (current.low - shelf_low) / atr
            values["current_close_minus_shelf_low_core_atr_1440"] = (current.close - shelf_low) / atr
    if shelf_high is not None and atr:
        values["current_high_minus_shelf_high_core_atr_1440"] = (current.high - shelf_high) / atr
    if shelf_low is not None and shelf_high is not None:
        values["post_pump_consolidation_minutes"] = state.time_since_running_high_minutes
        values["consolidation_width_ratio"] = (
            max(shelf_high - shelf_low, 0.0) / pump_height if pump_height > 1e-12 else None
        )
    if shelf_low is not None and current.low < shelf_low:
        values["volume_on_sweep_percentile"] = _rank_percentile(current.volume, [row.volume for row in event])
        trades = [row.number_of_trades for row in event if row.number_of_trades is not None]
        values["trade_count_on_sweep_percentile"] = (
            _rank_percentile(current.number_of_trades, trades)
            if current.number_of_trades is not None and trades
            else None
        )
        if current.taker_buy_quote_volume is not None:
            delta = 2.0 * current.taker_buy_quote_volume - current.quote_volume
            values["cvd_change_during_sweep"] = delta / max(current.quote_volume, 1e-12)
        values["oi_change_during_sweep"] = context.core_features.get("oi_change_5m_pct")
        values["liq_intensity_during_sweep"] = context.core_features.get("liq_intensity")
    return values


def _rank_percentile(value: float, values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(float(item) <= float(value) for item in values) / len(values)


def _pump_market_mechanics_features(context: StrategyFeatureContext) -> Mapping[str, object]:
    market = tuple(context.market_rows_asof)
    event = tuple(context.event_rows_asof)
    if not market or not event:
        return {spec.name: None for spec in PUMP_MARKET_MECHANICS_FEATURES}
    first = event[0]
    current = event[-1]
    elapsed = max(len(event), 1)
    event_high = max(row.high for row in event)
    event_low = min(row.low for row in event)
    path = sum(abs(row.close - (event[index - 1].close if index else first.open)) for index, row in enumerate(event))
    ranges = [max(row.high - row.low, 1e-12) for row in event]
    upper_wicks = [(row.high - max(row.open, row.close)) / ranges[index] for index, row in enumerate(event)]
    current_range = ranges[-1]
    turnover = sum(row.quote_volume for row in event)
    trade_count = sum((row.number_of_trades or 0.0) for row in event)
    market_turnover = sum(row.quote_volume for row in market)
    market_trades = sum((row.number_of_trades or 0.0) for row in market)
    event_avg_trade = turnover / max(trade_count, 1.0)
    market_avg_trade = market_turnover / max(market_trades, 1.0)
    event_activity = [row.quote_volume for row in event]
    activity_mean = sum(event_activity) / elapsed
    activity_variance = sum((value - activity_mean) ** 2 for value in event_activity) / elapsed
    activity_cv = math.sqrt(activity_variance) / max(activity_mean, 1e-12)
    taker_values = [row.taker_buy_quote_volume for row in event]
    taker_available = all(value is not None for value in taker_values)
    taker_share = (
        sum(float(value) for value in taker_values if value is not None) / max(turnover, 1e-12)
        if taker_available
        else None
    )
    pullbacks = _event_pullbacks_between_highs(event)
    ema_240 = _ema_values([row.close for row in market], span=240)
    price_return_60 = _window_return(market, 60)
    oi_15 = _oi_change(context.open_interest_rows_asof, 15)
    oi_60 = _oi_change(context.open_interest_rows_asof, 60)
    qv_multiple = event[-1].quote_volume / max(_median([row.quote_volume for row in market[:-1]]), 1e-12)
    trade_multiple = (event[-1].number_of_trades or 0.0) / max(
        _median([(row.number_of_trades or 0.0) for row in market[:-1]]), 1e-12
    )
    return {
        "pump_verticality": (event_high / first.open - 1.0) / elapsed,
        "max_1m_high_return": max(row.high / row.open - 1.0 for row in event),
        "max_1m_close_return": max(row.close / row.open - 1.0 for row in event),
        "path_efficiency": max(current.close - first.open, 0.0) / max(path, 1e-12),
        "mean_pullback_between_highs": sum(pullbacks) / len(pullbacks) if pullbacks else 0.0,
        "max_pullback_between_highs": max(pullbacks) if pullbacks else 0.0,
        "rehigh_count": len(pullbacks),
        "current_upper_wick_fraction": (current.high - max(current.open, current.close)) / current_range,
        "current_lower_wick_fraction": (min(current.open, current.close) - current.low) / current_range,
        "current_body_fraction": abs(current.close - current.open) / current_range,
        "current_close_location": (current.close - current.low) / current_range,
        "mean_upper_wick_fraction": sum(upper_wicks) / elapsed,
        "max_upper_wick_fraction": max(upper_wicks),
        "event_turnover": turnover,
        "turnover_share_24h": turnover / max(market_turnover, 1e-12),
        "event_trade_count": trade_count,
        "average_trade_notional_vs_24h": event_avg_trade / max(market_avg_trade, 1e-12),
        "turnover_top_candle_share": max(event_activity) / max(turnover, 1e-12),
        "trade_count_top_candle_share": max((row.number_of_trades or 0.0) for row in event) / max(trade_count, 1e-12),
        "retail_frenzy_proxy": trade_multiple / max(qv_multiple, 1e-12),
        "large_print_proxy": qv_multiple / max(trade_multiple, 1e-12),
        "algorithmic_persistence_proxy": 1.0 / (1.0 + activity_cv),
        "taker_buy_share_event": taker_share,
        "taker_imbalance_event": None if taker_share is None else 2.0 * taker_share - 1.0,
        "pre_return_15m": _window_return(market, 15),
        "pre_return_60m": price_return_60,
        "pre_return_240m": _window_return(market, 240),
        "pre_dump_depth_60m": current.close / max(row.high for row in market[-60:]) - 1.0 if len(market) >= 60 else 0.0,
        "price_vs_ema_240": current.close / max(ema_240[-1], 1e-12) - 1.0,
        "ema_240_slope_60m": ema_240[-1] / max(ema_240[-61], 1e-12) - 1.0 if len(ema_240) > 60 else 0.0,
        "oi_change_15m": oi_15,
        "oi_change_60m": oi_60,
        "price_up_oi_up": bool(oi_60 is not None and price_return_60 > 0.0 and oi_60 > 0.0),
        "price_up_oi_down": bool(oi_60 is not None and price_return_60 > 0.0 and oi_60 < 0.0),
        "price_down_oi_up": bool(oi_60 is not None and price_return_60 < 0.0 and oi_60 > 0.0),
        "price_down_oi_down": bool(oi_60 is not None and price_return_60 < 0.0 and oi_60 < 0.0),
    }


def _event_pullbacks_between_highs(event: Sequence[Candle1m]) -> list[float]:
    pullbacks: list[float] = []
    running_high = -math.inf
    last_high_index: int | None = None
    last_high = 0.0
    for index, row in enumerate(event):
        if row.high <= running_high:
            continue
        if last_high_index is not None and index > last_high_index + 1:
            valley = min(item.low for item in event[last_high_index + 1 : index])
            pullbacks.append(max(last_high - valley, 0.0) / max(last_high, 1e-12))
        running_high = row.high
        last_high = row.high
        last_high_index = index
    return pullbacks


def _window_return(rows: Sequence[Candle1m], minutes: int) -> float:
    if len(rows) <= minutes:
        return 0.0
    return rows[-1].close / rows[-minutes - 1].close - 1.0


def _ema_values(values: Sequence[float], *, span: int) -> list[float]:
    alpha = 2.0 / (span + 1.0)
    output: list[float] = []
    current = float(values[0])
    for value in values:
        current = alpha * float(value) + (1.0 - alpha) * current
        output.append(current)
    return output


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(float(value) for value in values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _oi_change(rows: Sequence[object], minutes: int) -> float | None:
    if len(rows) < 2:
        return None
    latest = rows[-1]
    cutoff = latest.available_time_ms - minutes * 60_000
    prior = next((row for row in reversed(rows[:-1]) if row.available_time_ms <= cutoff), None)
    if prior is None or prior.open_interest <= 0.0:
        return None
    return latest.open_interest / prior.open_interest - 1.0


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


def make_post_anomaly_extension_strategy(
    *,
    strategy_name: str,
    config: PostAnomalyExtensionConfig | None = None,
) -> PostAnomalyExtensionStrategy:
    if not strategy_name.startswith("post_anomaly_extension_v1_h"):
        raise ValueError(f"not a post-anomaly extension strategy variant: {strategy_name!r}")
    return PostAnomalyExtensionStrategy(
        config=config or PostAnomalyExtensionConfig(),
        _metadata=anomaly_strategy_metadata(strategy_name),
    )


def make_post_pump_distribution_strategy(
    *,
    strategy_name: str,
    config: PostPumpDistributionConfig | None = None,
) -> PostPumpDistributionStrategy:
    if not strategy_name.startswith("post_pump_distribution_v1_h"):
        raise ValueError(f"not a post-pump distribution strategy variant: {strategy_name!r}")
    return PostPumpDistributionStrategy(
        config=config or PostPumpDistributionConfig(),
        _metadata=anomaly_strategy_metadata(strategy_name),
    )


def detect_post_anomaly_extension_events(
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    *,
    config: PostAnomalyExtensionConfig | None = None,
) -> tuple[StrategyEvent, ...]:
    """Detect late extension states after a causal broad anomaly seed.

    The source anomaly is found by the broad detector. The extension trigger is
    emitted only after later closed 1m candles prove that price has continued in
    the seed direction by a configured as-of return from the seed open. Only the
    first qualifying extension row per active same-symbol source window is emitted.
    """
    cfg = config or PostAnomalyExtensionConfig()
    rows = sorted(tuple(candles_1m), key=lambda item: (item.symbol, item.open_time_ms))
    if not rows:
        return ()

    broad_events = detect_broad_anomaly_events(rows, config=cfg.broad_detector_config)
    candles_by_symbol: dict[str, list[Candle1m]] = {}
    for candle in rows:
        candles_by_symbol.setdefault(candle.symbol, []).append(candle)

    events: list[StrategyEvent] = []
    blocked_until_by_symbol: dict[str, int] = {}
    for source_event in sorted(broad_events, key=lambda item: (item.symbol, item.event_start_time_ms, item.event_id)):
        if source_event.event_start_time_ms <= blocked_until_by_symbol.get(source_event.symbol, -1):
            continue
        direction = _seed_direction(source_event)
        source_candles = candles_by_symbol.get(source_event.symbol, [])
        for candle in source_candles:
            if candle.available_time_ms <= source_event.event_detection_time_ms:
                continue
            minutes_since_start = _minutes_between(source_event.event_start_time_ms, candle.available_time_ms)
            if minutes_since_start < cfg.min_minutes_since_event_start:
                continue
            if minutes_since_start > cfg.max_minutes_since_event_start:
                break
            extension_return = (candle.close / source_event.seed_open) - 1.0
            if not _extension_reached(
                extension_return=extension_return,
                direction=direction,
                threshold=cfg.min_abs_extension_return_from_seed_open,
            ):
                continue
            direction_component = "upside_extension" if direction > 0 else "downside_extension"
            events.append(
                StrategyEvent(
                    event_id=_post_anomaly_extension_event_id(
                        cfg.detector_version,
                        source_event.symbol,
                        source_event.event_start_time_ms,
                        candle.open_time_ms,
                    ),
                    symbol=source_event.symbol,
                    event_start_time_ms=source_event.event_start_time_ms,
                    event_detection_time_ms=candle.available_time_ms,
                    seed_time_ms=source_event.seed_time_ms,
                    seed_open=source_event.seed_open,
                    seed_high=source_event.seed_high,
                    seed_low=source_event.seed_low,
                    seed_close=source_event.seed_close,
                    initial_move_pct=extension_return,
                    initial_volume_zscore=source_event.initial_volume_zscore,
                    initial_quote_volume_zscore=source_event.initial_quote_volume_zscore,
                    initial_trade_count_zscore=source_event.initial_trade_count_zscore,
                    trigger_component="post_anomaly_extension",
                    trigger_components=("post_anomaly_extension", direction_component),
                    technical_noise_shock=False,
                    raw_candle_gap_minutes=None,
                    excluded_by_data_quality_gate=False,
                    detector_version=cfg.detector_version,
                )
            )
            blocked_until_by_symbol[source_event.symbol] = (
                source_event.event_start_time_ms + cfg.max_minutes_since_event_start * ONE_MINUTE_MS
            )
            break
    return tuple(events)


def detect_post_pump_distribution_events(
    candles_1m: Sequence[Candle1m] | Iterable[Candle1m],
    *,
    config: PostPumpDistributionConfig | None = None,
) -> tuple[StrategyEvent, ...]:
    """Detect post-pump distribution states using only as-of candles.

    The trigger matches the strategy spec: daily_return_asof_t > 30% and the
    current closed 1m trade_count is above the same-minute point-in-time market
    percentile threshold. Only the first trigger per symbol/UTC-day is emitted.
    """
    cfg = config or PostPumpDistributionConfig()
    rows = sorted(tuple(candles_1m), key=lambda item: (item.open_time_ms, item.symbol))
    if not rows:
        return ()

    first_open_by_symbol_day: dict[tuple[str, int], float] = {}
    emitted_symbol_days: set[tuple[str, int]] = set()
    by_time: dict[int, list[Candle1m]] = {}
    for candle in rows:
        by_time.setdefault(candle.open_time_ms, []).append(candle)

    events: list[StrategyEvent] = []
    for open_time_ms in sorted(by_time):
        same_minute = sorted(by_time[open_time_ms], key=lambda item: item.symbol)
        trade_percentiles = _same_minute_trade_count_percentiles(same_minute)
        for candle in same_minute:
            symbol_day = (candle.symbol, _utc_day_start_ms(candle.open_time_ms))
            first_open_by_symbol_day.setdefault(symbol_day, candle.open)
            if symbol_day in emitted_symbol_days:
                continue
            day_open = first_open_by_symbol_day[symbol_day]
            daily_return_asof_t = (candle.close / day_open) - 1.0
            trade_count_percentile = trade_percentiles.get(candle.symbol)
            if trade_count_percentile is None:
                continue
            if daily_return_asof_t <= cfg.min_daily_return_asof_t:
                continue
            if trade_count_percentile <= cfg.min_trade_count_market_percentile_asof_t:
                continue
            events.append(
                StrategyEvent(
                    event_id=_post_pump_event_id(cfg.detector_version, candle.symbol, candle.open_time_ms),
                    symbol=candle.symbol,
                    event_start_time_ms=candle.open_time_ms,
                    event_detection_time_ms=candle.available_time_ms,
                    seed_time_ms=candle.open_time_ms,
                    seed_open=candle.open,
                    seed_high=candle.high,
                    seed_low=candle.low,
                    seed_close=candle.close,
                    initial_move_pct=daily_return_asof_t,
                    initial_volume_zscore=None,
                    initial_quote_volume_zscore=None,
                    initial_trade_count_zscore=None,
                    trigger_component="post_pump_distribution",
                    trigger_components=("post_pump_distribution", "daily_return_asof_t", "trade_count_market_percentile_asof_t"),
                    technical_noise_shock=False,
                    raw_candle_gap_minutes=None,
                    excluded_by_data_quality_gate=False,
                    daily_return_asof_t=daily_return_asof_t,
                    trade_count_market_percentile_asof_t=trade_count_percentile,
                    detector_version=cfg.detector_version,
                )
            )
            emitted_symbol_days.add(symbol_day)
    return tuple(events)


def events_to_trigger_frame(events: Sequence[StrategyEvent]) -> pl.DataFrame:
    rows = [_event_to_trigger_row(event) for event in events]
    if not rows:
        return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=_TRIGGER_FRAME_SCHEMA, orient="row")


def _event_to_trigger_row(event: StrategyEvent) -> dict[str, object]:
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
        "trigger_component": event.trigger_component,
        "trigger_components": ";".join(event.trigger_components),
        "technical_noise_shock": event.technical_noise_shock,
        "raw_candle_gap_minutes": event.raw_candle_gap_minutes,
        "excluded_by_data_quality_gate": event.excluded_by_data_quality_gate,
        "daily_return_asof_t": event.daily_return_asof_t,
        "trade_count_market_percentile_asof_t": event.trade_count_market_percentile_asof_t,
        "detector_version": event.detector_version,
    }


def _seed_direction(event: StrategyEvent) -> int:
    return 1 if event.seed_close >= event.seed_open else -1


def _extension_reached(*, extension_return: float, direction: int, threshold: float) -> bool:
    if direction > 0:
        return extension_return >= threshold
    return extension_return <= -threshold


def _minutes_between(start_ms: int, end_ms: int) -> int:
    if end_ms < start_ms:
        raise ValueError("end timestamp must be >= start timestamp")
    return (end_ms - start_ms) // ONE_MINUTE_MS


def _same_minute_trade_count_percentiles(candles: Sequence[Candle1m]) -> dict[str, float]:
    values = [(candle.symbol, candle.number_of_trades) for candle in candles if candle.number_of_trades is not None]
    if not values:
        return {}
    sorted_values = sorted(values, key=lambda item: (float(item[1]), item[0]))
    denominator = max(len(sorted_values) - 1, 1)
    percentiles: dict[str, float] = {}
    for rank, (symbol, value) in enumerate(sorted_values):
        # Average rank for ties; deterministic and computed from this closed minute only.
        tied_ranks = [idx for idx, (_, other) in enumerate(sorted_values) if float(other) == float(value)]
        average_rank = sum(tied_ranks) / len(tied_ranks)
        percentiles[symbol] = average_rank / denominator if len(sorted_values) > 1 else 1.0
    return percentiles


def _utc_day_start_ms(timestamp_ms: int) -> int:
    return timestamp_ms - (timestamp_ms % (24 * 60 * ONE_MINUTE_MS))


def _post_anomaly_extension_event_id(detector_version: str, symbol: str, source_event_start_ms: int, extension_time_ms: int) -> str:
    raw = f"{detector_version}|{symbol}|{source_event_start_ms}|{extension_time_ms}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"evt_{digest}"


def _post_pump_event_id(detector_version: str, symbol: str, seed_time_ms: int) -> str:
    raw = f"{detector_version}|{symbol}|{seed_time_ms}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:16]
    return f"evt_{digest}"
