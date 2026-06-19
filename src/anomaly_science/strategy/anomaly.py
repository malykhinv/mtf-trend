from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

import pandas as pd
import polars as pl

from anomaly_science.contracts.events import StrategyEvent
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


BROAD_ANOMALY_ALLOWED_HORIZONS: tuple[int, ...] = (15, 30, 60)
POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS: tuple[int, ...] = (60, 120, 180)
POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS: tuple[int, ...] = (60, 120, 180)

ANOMALY_STRATEGY_DEFAULTS: dict[str, dict[str, object]] = {
    "broad_anomaly_v1_h15": {
        "horizon_minutes": 15,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
        "take_profit_atr_1440": 1.5,
        "stop_loss_atr_1440": 1.0,
    },
    "broad_anomaly_v1_h30": {
        "horizon_minutes": 30,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
        "take_profit_atr_1440": 2.0,
        "stop_loss_atr_1440": 1.1,
    },
    "broad_anomaly_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": BROAD_ANOMALY_ALLOWED_HORIZONS,
        "default_horizon_minutes": 30,
        "take_profit_atr_1440": 2.5,
        "stop_loss_atr_1440": 1.2,
    },
    "post_anomaly_extension_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 2.5,
        "stop_loss_atr_1440": 1.3,
    },
    "post_anomaly_extension_v1_h120": {
        "horizon_minutes": 120,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 3.0,
        "stop_loss_atr_1440": 1.5,
    },
    "post_anomaly_extension_v1_h180": {
        "horizon_minutes": 180,
        "allowed_horizons": POST_ANOMALY_EXTENSION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 4.0,
        "stop_loss_atr_1440": 2.0,
    },
    "post_pump_distribution_v1_h60": {
        "horizon_minutes": 60,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 2.0,
        "stop_loss_atr_1440": 1.2,
    },
    "post_pump_distribution_v1_h120": {
        "horizon_minutes": 120,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 3.0,
        "stop_loss_atr_1440": 1.5,
    },
    "post_pump_distribution_v1_h180": {
        "horizon_minutes": 180,
        "allowed_horizons": POST_PUMP_DISTRIBUTION_ALLOWED_HORIZONS,
        "default_horizon_minutes": 120,
        "take_profit_atr_1440": 4.0,
        "stop_loss_atr_1440": 2.0,
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
        strategy_contract_version="base_strategy_v1",
        strategy_family="anomaly",
        horizon_minutes=int(defaults["horizon_minutes"]),
        allowed_horizons=tuple(int(horizon) for horizon in defaults["allowed_horizons"]),
        default_horizon_minutes=int(defaults["default_horizon_minutes"]),
        take_profit_atr_1440=float(defaults["take_profit_atr_1440"]),
        stop_loss_atr_1440=float(defaults["stop_loss_atr_1440"]),
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
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(BROAD_ANOMALY_REQUIRED_DATA_STREAMS)
        return BROAD_ANOMALY_REQUIRED_DATA_STREAMS

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        candles = normalize_candles_1m(pandas_frame)
        events = self.generate_events(candles)
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_broad_anomaly_events(candles_1m, config=self.config)

    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame()


@dataclass(frozen=True, slots=True)
class PostAnomalyExtensionStrategy(BaseStrategy):
    config: PostAnomalyExtensionConfig = field(default_factory=PostAnomalyExtensionConfig)
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("post_anomaly_extension_v1_h120"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(POST_ANOMALY_EXTENSION_REQUIRED_DATA_STREAMS)
        return POST_ANOMALY_EXTENSION_REQUIRED_DATA_STREAMS

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        events = self.generate_events(normalize_candles_1m(pandas_frame))
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_post_anomaly_extension_events(candles_1m, config=self.config)

    def generate_custom_features(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        return pl.DataFrame()


@dataclass(frozen=True, slots=True)
class PostPumpDistributionStrategy(BaseStrategy):
    config: PostPumpDistributionConfig = PostPumpDistributionConfig()
    _metadata: StrategyMetadata = field(default_factory=lambda: anomaly_strategy_metadata("post_pump_distribution_v1_h120"))

    @property
    def metadata(self) -> StrategyMetadata:
        return self._metadata

    @property
    def required_data_streams(self) -> Mapping[str, bool]:
        validate_required_data_streams(POST_PUMP_REQUIRED_DATA_STREAMS)
        return POST_PUMP_REQUIRED_DATA_STREAMS

    def generate_triggers(self, market_frame_asof: pl.DataFrame) -> pl.DataFrame:
        if market_frame_asof.height == 0:
            return pl.DataFrame(schema=_TRIGGER_FRAME_SCHEMA)
        pandas_frame = pd.DataFrame(market_frame_asof.to_dicts())
        events = self.generate_events(normalize_candles_1m(pandas_frame))
        trigger_frame = events_to_trigger_frame(events)
        validate_trigger_frame(trigger_frame)
        return trigger_frame

    def generate_events(self, candles_1m: Sequence[Candle1m] | Iterable[Candle1m]) -> tuple[StrategyEvent, ...]:
        return detect_post_pump_distribution_events(candles_1m, config=self.config)

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
