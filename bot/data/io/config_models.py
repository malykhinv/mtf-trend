from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Iterator, Mapping

from ...app_modes import AppMode
from ...domain.enums import Timeframe
from ...domain.models.entities import ThresholdMetric as DomainThresholdMetric
from ...domain.models.entities import Thresholds as DomainThresholds
from ...domain.models.metadata import ThresholdsMetadata, ThresholdsMetadataPayload
from .config_types import (
    AppConfigPayload,
    BacktestConfigPayload,
    DedupConfigPayload,
    LiveConfigPayload,
    LoggingConfigPayload,
    MetricsConfigPayload,
    ModeSelectionPayload,
    ProviderConfigPayload,
    ProviderCredentialPayload,
    ProvidersConfigPayload,
    RangePayload,
    StorageConfigPayload,
    SymbolProviderRoutePayload,
    SymbolSelectionPayload,
    SymbolsConfigPayload,
    ThresholdConfigPayload,
    ThresholdMetricConfigPayload,
    ThresholdsConfigPayload,
    TimeConfigPayload,
)


_DEFAULT_BACKTEST_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
)


def _coerce_positive_int(value: int | None, default: int, *, minimum: int = 1) -> int:
    if value is None:
        return default
    return value if value >= minimum else minimum


def _coerce_non_negative_float(value: float | None, default: float = 0.0) -> float:
    if value is None:
        return default
    return value


def _ranges_to_tuples(ranges: Iterable[RangePayload]) -> tuple[tuple[float | None, float | None], ...]:
    return tuple((item.minimum, item.maximum) for item in ranges)


@dataclass(slots=True)
class StorageConfig:
    path: str = "var/state.xlsx"

    @classmethod
    def from_payload(cls, payload: StorageConfigPayload) -> "StorageConfig":
        path = payload.path.strip() if payload.path else "var/state.xlsx"
        return cls(path=path)


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"

    @classmethod
    def from_payload(cls, payload: LoggingConfigPayload) -> "LoggingConfig":
        level = payload.level.upper().strip() if payload.level else "INFO"
        return cls(level=level)


@dataclass(slots=True)
class TimeConfig:
    zone: str = "UTC"
    mode: AppMode | None = None

    @classmethod
    def from_payload(cls, payload: TimeConfigPayload) -> "TimeConfig":
        zone = payload.zone or "UTC"
        return cls(zone=zone, mode=payload.mode)


@dataclass(slots=True)
class ProviderCredential:
    value: str | None = None
    env_key: str | None = None

    @classmethod
    def from_payload(cls, payload: ProviderCredentialPayload) -> "ProviderCredential":
        env_key = payload.env_key
        if env_key:
            env_key = env_key.strip()
        value = payload.value.strip() if payload.value else None
        return cls(value=value, env_key=env_key or None)

    def resolve(self, env: Mapping[str, str] | None = None) -> str | None:
        if self.value is not None and self.value.strip():
            return self.value.strip()
        if not self.env_key:
            return None
        env_mapping = env or {}
        candidate = env_mapping.get(self.env_key)
        if candidate:
            candidate = candidate.strip()
            if candidate:
                return candidate
        fallback = os.getenv(self.env_key)
        if fallback:
            fallback = fallback.strip()
            if fallback:
                return fallback
        return None

    @property
    def env(self) -> str | None:
        return self.env_key


class ProviderKind(str, Enum):
    GENERIC = "generic"
    BINANCE = "binance"
    BYBIT = "bybit"


@dataclass(slots=True)
class ExchangeProviderConfig:
    name: str
    api_base: str
    rate_limit_per_minute: int
    min_quote_volume: float
    api_key: ProviderCredential = field(default_factory=ProviderCredential)
    api_secret: ProviderCredential = field(default_factory=ProviderCredential)
    kind: ProviderKind = field(init=False, default=ProviderKind.GENERIC)

    @classmethod
    def from_payload(
        cls, name: str, payload: ProviderConfigPayload
    ) -> "ExchangeProviderConfig":
        api_base = payload.api_base.strip() if payload.api_base else ""
        rate_limit = _coerce_positive_int(payload.rate_limit_per_minute, 60)
        min_quote_volume = _coerce_non_negative_float(payload.min_quote_volume)
        api_key = ProviderCredential.from_payload(payload.api_key)
        api_secret = ProviderCredential.from_payload(payload.api_secret)
        instance = cls(
            name=name,
            api_base=api_base,
            rate_limit_per_minute=rate_limit,
            min_quote_volume=min_quote_volume,
            api_key=api_key,
            api_secret=api_secret,
        )
        return instance

    def get_api_key(self, env: Mapping[str, str] | None = None) -> str | None:
        return self.api_key.resolve(env)

    def get_api_secret(self, env: Mapping[str, str] | None = None) -> str | None:
        return self.api_secret.resolve(env)

    @property
    def api_key_env(self) -> str | None:
        return self.api_key.env

    @property
    def api_secret_env(self) -> str | None:
        return self.api_secret.env


@dataclass(slots=True)
class BinanceProviderConfig(ExchangeProviderConfig):
    kind: ProviderKind = field(init=False, default=ProviderKind.BINANCE)


@dataclass(slots=True)
class BybitProviderConfig(ExchangeProviderConfig):
    kind: ProviderKind = field(init=False, default=ProviderKind.BYBIT)


_PROVIDER_CONFIG_TYPES: dict[str, tuple[ProviderKind, type[ExchangeProviderConfig]]] = {
    "binance": (ProviderKind.BINANCE, BinanceProviderConfig),
    "bybit": (ProviderKind.BYBIT, BybitProviderConfig),
}


@dataclass(slots=True)
class ProvidersConfig:
    providers: tuple[ExchangeProviderConfig, ...]
    _index: dict[str, ExchangeProviderConfig] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._index = {provider.name: provider for provider in self.providers}

    def by_name(self, name: str) -> ExchangeProviderConfig:
        return self._index[name]

    def __iter__(self) -> Iterator[ExchangeProviderConfig]:
        return iter(self.providers)

    def __len__(self) -> int:
        return len(self.providers)

    def names(self) -> tuple[str, ...]:
        return tuple(self._index)


def parse_exchange_provider_configs(payload: ProvidersConfigPayload) -> ProvidersConfig:
    providers: list[ExchangeProviderConfig] = []
    for provider_payload in payload.providers:
        name = provider_payload.name
        kind, config_cls = _PROVIDER_CONFIG_TYPES.get(
            name.lower(), (ProviderKind.GENERIC, ExchangeProviderConfig)
        )
        config = config_cls.from_payload(name, provider_payload)
        config.kind = kind
        providers.append(config)
    return ProvidersConfig(providers=tuple(providers))


@dataclass(slots=True)
class MetricsConfig:
    atr_period: int = 14
    volume_period: int = 20
    momentum_period: int = 5

    @classmethod
    def from_payload(cls, payload: MetricsConfigPayload) -> "MetricsConfig":
        return cls(
            atr_period=_coerce_positive_int(payload.atr_period, 14),
            volume_period=_coerce_positive_int(payload.volume_period, 20),
            momentum_period=_coerce_positive_int(payload.momentum_period, 5),
        )


@dataclass(slots=True)
class DedupConfig:
    ttl_seconds: int = 14_400
    max_records: int = 1_000

    @classmethod
    def from_payload(cls, payload: DedupConfigPayload) -> "DedupConfig":
        return cls(
            ttl_seconds=_coerce_positive_int(payload.ttl_seconds, 14_400),
            max_records=_coerce_positive_int(payload.max_records, 1_000),
        )


@dataclass(slots=True)
class ThresholdMetricConfig:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_payload(cls, payload: ThresholdMetricConfigPayload) -> "ThresholdMetricConfig":
        return cls(
            name=payload.name,
            min_value=payload.min_value,
            max_value=payload.max_value,
            min_abs_value=payload.min_abs_value,
        )

    def to_domain(self) -> DomainThresholdMetric:
        return DomainThresholdMetric(
            name=self.name,
            min_value=self.min_value,
            max_value=self.max_value,
            min_abs_value=self.min_abs_value,
        )


@dataclass(slots=True)
class ThresholdConfig:
    id: str | None = None
    min_relative_volume: float | None = None
    max_relative_volume: float | None = None
    min_atr_mult: float | None = None
    min_pct_move: float | None = None
    max_pct_move: float | None = None
    max_upper_wick_pct: float | None = None
    max_lower_wick_pct: float | None = None
    allow_long: bool = True
    allow_short: bool = True
    metrics: tuple[ThresholdMetricConfig, ...] = field(default_factory=tuple)
    short_pct_move_ranges: tuple[tuple[float | None, float | None], ...] = field(
        default_factory=tuple
    )
    short_relative_volume_ranges: tuple[tuple[float | None, float | None], ...] = field(
        default_factory=tuple
    )
    metadata: ThresholdsMetadata | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @classmethod
    def from_payload(cls, payload: ThresholdConfigPayload) -> "ThresholdConfig":
        metadata = None
        if payload.metadata is not None:
            metadata = ThresholdsMetadata.from_mapping(payload.metadata)
        metrics = tuple(
            ThresholdMetricConfig.from_payload(metric_payload)
            for metric_payload in payload.metrics
        )
        short_pct_move_ranges = _ranges_to_tuples(payload.short_pct_move_ranges)
        short_relative_volume_ranges = _ranges_to_tuples(
            payload.short_relative_volume_ranges
        )
        if not short_pct_move_ranges and metadata:
            short_pct_move_ranges = tuple(
                (item.minimum, item.maximum)
                for item in metadata.short_pct_move_ranges
                if not item.is_empty()
            )
        if not short_relative_volume_ranges and metadata:
            short_relative_volume_ranges = tuple(
                (item.minimum, item.maximum)
                for item in metadata.short_relative_volume_ranges
                if not item.is_empty()
            )
        return cls(
            id=payload.id,
            min_relative_volume=payload.min_relative_volume,
            max_relative_volume=payload.max_relative_volume,
            min_atr_mult=payload.min_atr_mult,
            min_pct_move=payload.min_pct_move,
            max_pct_move=payload.max_pct_move,
            max_upper_wick_pct=payload.max_upper_wick_pct,
            max_lower_wick_pct=payload.max_lower_wick_pct,
            allow_long=True if payload.allow_long is None else bool(payload.allow_long),
            allow_short=True if payload.allow_short is None else bool(payload.allow_short),
            metrics=metrics,
            short_pct_move_ranges=short_pct_move_ranges,
            short_relative_volume_ranges=short_relative_volume_ranges,
            metadata=metadata,
            created_at=payload.created_at,
            updated_at=payload.updated_at,
        )

    def to_domain(self) -> DomainThresholds:
        return DomainThresholds(
            id=self.id,
            min_relative_volume=_coerce_non_negative_float(self.min_relative_volume),
            max_relative_volume=_coerce_non_negative_float(self.max_relative_volume),
            min_atr_mult=_coerce_non_negative_float(self.min_atr_mult),
            min_pct_move=_coerce_non_negative_float(self.min_pct_move),
            max_pct_move=_coerce_non_negative_float(self.max_pct_move),
            max_upper_wick_pct=_coerce_non_negative_float(self.max_upper_wick_pct),
            max_lower_wick_pct=_coerce_non_negative_float(self.max_lower_wick_pct),
            short_pct_move_ranges=[*self.short_pct_move_ranges],
            short_relative_volume_ranges=[*self.short_relative_volume_ranges],
            allow_long=self.allow_long,
            allow_short=self.allow_short,
            metrics=[metric.to_domain() for metric in self.metrics],
            metadata=self.metadata,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


@dataclass(slots=True)
class SymbolThresholdConfig:
    symbol: str
    config: ThresholdConfig


@dataclass(slots=True)
class ThresholdsConfig:
    default: ThresholdConfig
    overrides: tuple[SymbolThresholdConfig, ...]

    @classmethod
    def from_payload(cls, payload: ThresholdsConfigPayload) -> "ThresholdsConfig":
        default_config = ThresholdConfig.from_payload(payload.default)
        overrides = tuple(
            SymbolThresholdConfig(symbol=item.symbol, config=ThresholdConfig.from_payload(item.config))
            for item in payload.overrides
        )
        return cls(default=default_config, overrides=overrides)

    def for_symbol(self, symbol: str) -> ThresholdConfig:
        upper_symbol = symbol.upper()
        for override in self.overrides:
            if override.symbol == upper_symbol:
                return override.config
        return self.default


@dataclass(slots=True)
class ModeSelectionOverrides:
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)
    symbols: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_payload(cls, payload: ModeSelectionPayload) -> "ModeSelectionOverrides":
        return cls(
            allow=frozenset(payload.allow),
            deny=frozenset(payload.deny),
            symbols=frozenset(payload.symbols),
        )


@dataclass(slots=True)
class SymbolSelectionConfig:
    quote_suffix: str = "USDT"
    min_quote_volume: float = 5_000_000.0
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_payload(cls, payload: SymbolSelectionPayload) -> "SymbolSelectionConfig":
        suffix = payload.quote_suffix or "USDT"
        min_quote_volume = (
            payload.min_quote_volume if payload.min_quote_volume is not None else 5_000_000.0
        )
        selection = ModeSelectionOverrides.from_payload(payload.selection)
        return cls(
            quote_suffix=suffix,
            min_quote_volume=min_quote_volume,
            allow=selection.allow,
            deny=selection.deny,
        )


@dataclass(slots=True)
class SymbolProviderRoute:
    symbol: str
    provider: str


@dataclass(slots=True)
class SymbolProviderMapping:
    routes: tuple[SymbolProviderRoute, ...]
    _index: dict[str, SymbolProviderRoute] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._index = {route.symbol: route for route in self.routes}

    def provider_for(self, symbol: str) -> str | None:
        normalized = "default" if symbol.lower() == "default" else symbol.upper()
        route = self._index.get(normalized)
        return route.provider if route else None

    def __iter__(self) -> Iterator[SymbolProviderRoute]:
        return iter(self.routes)


def parse_symbol_provider_mapping(
    routes: tuple[SymbolProviderRoutePayload, ...]
) -> SymbolProviderMapping:
    mapping_routes = tuple(
        SymbolProviderRoute(symbol=route.symbol, provider=route.provider)
        for route in routes
    )
    return SymbolProviderMapping(routes=mapping_routes)


@dataclass(slots=True)
class BacktestConfig:
    enabled: bool
    window: int
    timeframes: tuple[Timeframe, ...]
    limit: int | None
    history_batches: int
    selection: ModeSelectionOverrides

    @classmethod
    def from_payload(cls, payload: BacktestConfigPayload) -> "BacktestConfig":
        timeframes = payload.timeframes or _DEFAULT_BACKTEST_TIMEFRAMES
        limit = payload.limit
        selection = ModeSelectionOverrides.from_payload(payload.selection)
        return cls(
            enabled=True if payload.enabled is None else bool(payload.enabled),
            window=_coerce_positive_int(payload.window, 50),
            timeframes=timeframes,
            limit=limit,
            history_batches=_coerce_positive_int(payload.history_batches, 10),
            selection=selection,
        )


@dataclass(slots=True)
class LiveConfig:
    enabled: bool
    providers: tuple[str, ...]
    timeframe: Timeframe
    window: int
    selection: ModeSelectionOverrides

    @classmethod
    def from_payload(cls, payload: LiveConfigPayload) -> "LiveConfig":
        providers = payload.providers or tuple()
        timeframe = payload.timeframe or Timeframe.M5
        selection = ModeSelectionOverrides.from_payload(payload.selection)
        return cls(
            enabled=True if payload.enabled is None else bool(payload.enabled),
            providers=providers or tuple(),
            timeframe=timeframe,
            window=_coerce_positive_int(payload.window, 50),
            selection=selection,
        )


@dataclass(slots=True)
class SymbolsConfig:
    selection: SymbolSelectionConfig
    providers: SymbolProviderMapping

    @classmethod
    def from_payload(cls, payload: SymbolsConfigPayload) -> "SymbolsConfig":
        selection = SymbolSelectionConfig.from_payload(payload.selection)
        providers = parse_symbol_provider_mapping(payload.providers)
        return cls(selection=selection, providers=providers)


@dataclass(slots=True)
class AppConfig:
    payload: AppConfigPayload
    env: dict[str, str] = field(init=False)
    thresholds: ThresholdsConfig = field(init=False)
    symbols: SymbolsConfig = field(init=False)
    backtest: BacktestConfig = field(init=False)
    live: LiveConfig = field(init=False)
    providers: ProvidersConfig = field(init=False)
    metrics: MetricsConfig = field(init=False)
    dedup: DedupConfig = field(init=False)
    storage: StorageConfig = field(init=False)
    logging: LoggingConfig = field(init=False)
    time: TimeConfig = field(init=False)

    def __post_init__(self) -> None:
        self.env = self.payload.env.as_dict()
        self.storage = StorageConfig.from_payload(self.payload.storage)
        self.logging = LoggingConfig.from_payload(self.payload.logging)
        self.time = TimeConfig.from_payload(self.payload.time)
        self.thresholds = ThresholdsConfig.from_payload(self.payload.thresholds)
        self.symbols = SymbolsConfig.from_payload(self.payload.symbols)
        self.backtest = BacktestConfig.from_payload(self.payload.backtest)
        self.live = LiveConfig.from_payload(self.payload.live)
        self.providers = parse_exchange_provider_configs(self.payload.providers)
        self.metrics = MetricsConfig.from_payload(self.payload.metrics)
        self.dedup = DedupConfig.from_payload(self.payload.dedup)

    def selection_overrides_for(self, mode: AppMode) -> ModeSelectionOverrides:
        if mode is AppMode.BACKTEST:
            return self.backtest.selection
        if mode is AppMode.LIVE:
            return self.live.selection
        return ModeSelectionOverrides()

    @property
    def default_mode(self) -> AppMode:
        if self.payload.mode is not None:
            return self.payload.mode
        return AppMode.BACKTEST

    @property
    def symbol_selection(self) -> SymbolSelectionConfig:
        return self.symbols.selection

    @property
    def symbol_provider_mapping(self) -> SymbolProviderMapping:
        return self.symbols.providers
