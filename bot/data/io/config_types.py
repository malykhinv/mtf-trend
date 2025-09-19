from __future__ import annotations

from datetime import datetime
from typing import Mapping, Sequence, TypedDict

from ...domain.enums import Timeframe

TomlPrimitive = str | int | float | bool | None
NumberInput = int | float | str | bool | None
SymbolSetInput = str | Sequence[str]
RangeEntry = (
    Mapping[str, NumberInput | None]
    | Sequence[NumberInput | None]
    | NumberInput
    | None
)


class StorageSection(TypedDict, total=False):
    path: str


class LoggingSection(TypedDict, total=False):
    level: str


class TimeSection(TypedDict, total=False):
    zone: str
    mode: str


class MetricsSection(TypedDict, total=False):
    atr_period: NumberInput
    volume_period: NumberInput
    momentum_period: NumberInput


class DedupSection(TypedDict, total=False):
    ttl_seconds: NumberInput
    max_records: NumberInput


class ModeSelectionSection(TypedDict, total=False):
    allow: SymbolSetInput
    deny: SymbolSetInput
    symbols: SymbolSetInput


class SymbolSelectionSection(ModeSelectionSection, total=False):
    quote_suffix: str
    min_quote_volume: NumberInput


class SymbolsSection(TypedDict, total=False):
    selection: SymbolSelectionSection
    providers: dict[str, str]


class BacktestSection(ModeSelectionSection, total=False):
    enabled: bool | str | int | float
    window: NumberInput
    timeframes: Sequence[str | Timeframe]
    timeframe: str | Timeframe
    limit: NumberInput
    history_batches: NumberInput


class LiveSection(ModeSelectionSection, total=False):
    enabled: bool | str | int | float
    providers: Sequence[str] | str
    provider: str
    timeframe: str | Timeframe
    window: NumberInput


class ProviderSection(TypedDict, total=False):
    api_base: str
    ws_base: str
    rate_limit_per_minute: NumberInput
    min_quote_volume: NumberInput
    api_key: str
    api_key_env: str
    api_secret: str
    api_secret_env: str


ProvidersSection = dict[str, ProviderSection]


class ThresholdMetricSection(TypedDict, total=False):
    name: str
    min_value: NumberInput
    max_value: NumberInput
    min_abs_value: NumberInput


class ThresholdSection(TypedDict, total=False):
    id: str | int | float | bool
    min_relative_volume: NumberInput
    max_relative_volume: NumberInput
    min_atr_mult: NumberInput
    min_pct_move: NumberInput
    max_pct_move: NumberInput
    max_upper_wick_pct: NumberInput
    max_lower_wick_pct: NumberInput
    allow_long: bool | str | int | float
    allow_short: bool | str | int | float
    metrics: Sequence[ThresholdMetricSection]
    short_pct_move_ranges: Sequence[RangeEntry]
    short_relative_volume_ranges: Sequence[RangeEntry]
    metadata: Mapping[str, object]
    created_at: str | datetime
    updated_at: str | datetime


class ThresholdsSection(TypedDict, total=False):
    default: ThresholdSection
    symbols: dict[str, ThresholdSection]


class AppConfigData(TypedDict, total=False):
    mode: str
    env: dict[str, str]
    storage: StorageSection
    logging: LoggingSection
    time: TimeSection
    thresholds: ThresholdsSection
    symbols: SymbolsSection
    backtest: BacktestSection
    live: LiveSection
    metrics: MetricsSection
    dedup: DedupSection
    providers: ProvidersSection
