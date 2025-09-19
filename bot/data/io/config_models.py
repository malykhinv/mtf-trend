from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Iterable, Mapping, Sequence, Tuple, TypeVar, cast

from ...app_modes import AppMode
from ...domain.enums import Timeframe
from ...domain.models.entities import ThresholdMetric as DomainThresholdMetric
from ...domain.models.entities import Thresholds as DomainThresholds
from ...domain.models.metadata import ThresholdsMetadata
from .config_types import (
    BacktestSection,
    DedupSection,
    LiveSection,
    LoggingSection,
    MetricsSection,
    ModeSelectionSection,
    NumberInput,
    ProviderSection,
    ProvidersSection,
    RangeEntry,
    StorageSection,
    SymbolSelectionSection,
    SymbolSetInput,
    ThresholdMetricSection,
    ThresholdSection,
    ThresholdsSection,
    TimeSection,
)


_DEFAULT_BACKTEST_TIMEFRAMES: tuple[Timeframe, ...] = (
    Timeframe.M1,
    Timeframe.M3,
    Timeframe.M5,
    Timeframe.M15,
)


def _to_optional_float(value: NumberInput) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str) and value.strip():
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _to_bool(value: bool | str | int | float | None, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes", "on"}:
            return True
        if lowered in {"false", "0", "no", "off"}:
            return False
    if value is None:
        return default
    return bool(value)


def _to_int(value: NumberInput, default: int, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        candidate: int | None = 1 if value else 0
    elif isinstance(value, (int, float)):
        candidate = int(value)
    elif isinstance(value, str) and value.strip():
        try:
            candidate = int(float(value))
        except ValueError:
            candidate = None
    else:
        candidate = None
    if candidate is None:
        candidate = default
    if minimum is not None and candidate < minimum:
        return minimum
    return candidate


def _to_optional_int(value: NumberInput) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str) and value.strip():
        try:
            return int(float(value))
        except ValueError:
            return None
    return None


def _normalize_symbol_set(value: SymbolSetInput | Sequence[str] | None) -> frozenset[str]:
    if value is None:
        return frozenset()
    symbols: set[str] = set()
    if isinstance(value, str):
        items: Iterable[str] = [value]
    elif isinstance(value, Sequence):
        items = value
    else:
        return frozenset()
    for item in items:
        if isinstance(item, str) and item.strip():
            symbols.add(item.strip().upper())
    return frozenset(symbols)


def _parse_range_entry(entry: RangeEntry) -> tuple[float | None, float | None] | None:
    min_value: float | None = None
    max_value: float | None = None
    if isinstance(entry, Mapping):
        min_raw = entry.get("min")
        if min_raw is None:
            min_raw = entry.get("min_value")
        max_raw = entry.get("max")
        if max_raw is None:
            max_raw = entry.get("max_value")
        min_value = _to_optional_float(min_raw)
        max_value = _to_optional_float(max_raw)
    elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes, bytearray)):
        if len(entry) > 0:
            min_value = _to_optional_float(entry[0])
        if len(entry) > 1:
            max_value = _to_optional_float(entry[1])
    elif isinstance(entry, (int, float)):
        min_value = float(entry)
    if min_value is None and max_value is None:
        return None
    return (min_value, max_value)


def _parse_range_list(
    source: Sequence[RangeEntry] | None,
) -> tuple[tuple[float | None, float | None], ...]:
    if not source:
        return tuple()
    parsed: list[tuple[float | None, float | None]] = []
    for entry in source:
        parsed_entry = _parse_range_entry(entry)
        if parsed_entry is not None:
            parsed.append(parsed_entry)
    return tuple(parsed)


def _parse_datetime(value: datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def _float_or_default(value: float | None) -> float:
    return value if value is not None else 0.0


def _normalize_str(value: str | int | float | bool | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        candidate = value.strip()
        return candidate or None
    return str(value)


@dataclass(slots=True)
class StorageConfig:
    path: str = "var/state.xlsx"

    @classmethod
    def from_raw(cls, raw: StorageSection | None) -> "StorageConfig":
        if not raw:
            return cls()
        path_value = raw.get("path")
        if path_value is None:
            return cls()
        candidate = str(path_value).strip()
        if candidate:
            return cls(path=candidate)
        return cls()


@dataclass(slots=True)
class LoggingConfig:
    level: str = "INFO"

    @classmethod
    def from_raw(cls, raw: LoggingSection | None) -> "LoggingConfig":
        if not raw:
            return cls()
        level_value = raw.get("level")
        if level_value is None:
            return cls()
        candidate = str(level_value).strip()
        if candidate:
            return cls(level=candidate.upper())
        return cls()


def _parse_optional_mode(value: str | AppMode | None) -> AppMode | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return AppMode.parse(value)


@dataclass(slots=True)
class TimeConfig:
    zone: str = "UTC"
    mode: AppMode | None = None

    @classmethod
    def from_raw(cls, raw: TimeSection | None) -> "TimeConfig":
        zone = "UTC"
        mode: AppMode | None = None
        if raw:
            zone_raw = raw.get("zone")
            if zone_raw is not None:
                candidate = str(zone_raw).strip()
                if candidate:
                    zone = candidate
            mode = _parse_optional_mode(raw.get("mode"))
        return cls(zone=zone, mode=mode)


@dataclass(slots=True)
class ProviderCredential:
    value: str | None = None
    env_key: str | None = None

    @classmethod
    def from_mapping(
        cls, raw: ProviderSection, key: str, default_env: str | None = None
    ) -> "ProviderCredential":
        value = _normalize_str(cast(str | int | float | bool | None, raw.get(key)))
        env_key = _normalize_str(
            cast(str | int | float | bool | None, raw.get(f"{key}_env"))
        )
        if env_key is None:
            env_key = default_env
        return cls(value=value, env_key=env_key)

    def resolve(self, env: Mapping[str, str] | None = None) -> str | None:
        if self.value is not None:
            return self.value
        if not self.env_key:
            return None
        env_mapping = env or {}
        candidate = env_mapping.get(self.env_key)
        normalized = _normalize_str(candidate)
        if normalized is not None:
            return normalized
        fallback = os.getenv(self.env_key)
        if isinstance(fallback, str):
            fallback = fallback.strip()
            if fallback:
                return fallback
        return None


class ProviderKind(str, Enum):
    GENERIC = "generic"
    BINANCE = "binance"
    BYBIT = "bybit"


ProviderConfigT = TypeVar("ProviderConfigT", bound="ExchangeProviderConfig")


@dataclass(slots=True)
class ExchangeProviderConfig:
    name: str
    kind: ProviderKind = field(init=False, default=ProviderKind.GENERIC)
    api_base: str
    ws_base: str
    rate_limit_per_minute: int
    min_quote_volume: float
    api_key: ProviderCredential = field(default_factory=ProviderCredential)
    api_secret: ProviderCredential = field(default_factory=ProviderCredential)

    @classmethod
    def from_mapping(
        cls: type[ProviderConfigT],
        name: str,
        raw: ProviderSection | None,
    ) -> ProviderConfigT:
        if raw is None:
            data: ProviderSection = ProviderSection()
        else:
            data = raw
        prefix = name.upper()
        api_base = _normalize_str(data.get("api_base")) or ""
        ws_base = _normalize_str(data.get("ws_base")) or ""
        rate_limit = _to_int(data.get("rate_limit_per_minute"), 60, minimum=1)
        min_volume = _to_optional_float(data.get("min_quote_volume")) or 0.0
        api_key = ProviderCredential.from_mapping(
            data, "api_key", f"{prefix}_API_KEY"
        )
        api_secret = ProviderCredential.from_mapping(
            data, "api_secret", f"{prefix}_API_SECRET"
        )
        instance = cls(
            name=name,
            api_base=api_base,
            ws_base=ws_base,
            rate_limit_per_minute=rate_limit,
            min_quote_volume=min_volume,
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
        return self.api_key.env_key

    @property
    def api_secret_env(self) -> str | None:
        return self.api_secret.env_key


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


def parse_exchange_provider_configs(
    raw: ProvidersSection | None,
) -> dict[str, ExchangeProviderConfig]:
    if not raw:
        return {}
    providers: dict[str, ExchangeProviderConfig] = {}
    for name, value in raw.items():
        kind, config_cls = _PROVIDER_CONFIG_TYPES.get(
            name.lower(), (ProviderKind.GENERIC, ExchangeProviderConfig)
        )
        config = config_cls.from_mapping(name, value)
        config.kind = kind
        providers[name] = config
    return providers


@dataclass(slots=True)
class MetricsConfig:
    atr_period: int = 14
    volume_period: int = 20
    momentum_period: int = 5

    @classmethod
    def from_raw(cls, raw: MetricsSection | None) -> "MetricsConfig":
        if raw is None:
            data: MetricsSection = MetricsSection()
        else:
            data = raw
        return cls(
            atr_period=_to_int(data.get("atr_period"), 14, minimum=1),
            volume_period=_to_int(data.get("volume_period"), 20, minimum=1),
            momentum_period=_to_int(data.get("momentum_period"), 5, minimum=1),
        )


@dataclass(slots=True)
class DedupConfig:
    ttl_seconds: int = 14_400
    max_records: int = 1_000

    @classmethod
    def from_raw(cls, raw: DedupSection | None) -> "DedupConfig":
        if raw is None:
            data: DedupSection = DedupSection()
        else:
            data = raw
        return cls(
            ttl_seconds=_to_int(data.get("ttl_seconds"), 14_400, minimum=1),
            max_records=_to_int(data.get("max_records"), 1_000, minimum=1),
        )


@dataclass(slots=True)
class ThresholdMetricConfig:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_mapping(cls, raw: ThresholdMetricSection) -> "ThresholdMetricConfig" | None:
        name = raw.get("name")
        if name is None:
            return None
        candidate = name.strip()
        if not candidate:
            return None
        return cls(
            name=candidate,
            min_value=_to_optional_float(raw.get("min_value")),
            max_value=_to_optional_float(raw.get("max_value")),
            min_abs_value=_to_optional_float(raw.get("min_abs_value")),
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
    def from_mapping(cls, raw: ThresholdSection | None) -> "ThresholdConfig":
        if raw is None:
            data: ThresholdSection = ThresholdSection()
        else:
            data = raw
        metadata_raw = data.get("metadata")
        metadata = ThresholdsMetadata.from_mapping(
            metadata_raw if isinstance(metadata_raw, Mapping) else None
        )
        metrics_raw = data.get("metrics") or ()
        metrics: list[ThresholdMetricConfig] = []
        for item in metrics_raw:
            metric = ThresholdMetricConfig.from_mapping(item)
            if metric is not None:
                metrics.append(metric)
        short_pct_move_ranges = _parse_range_list(
            data.get("short_pct_move_ranges")
        )
        metadata_extra = metadata.extra if metadata else {}
        if not short_pct_move_ranges and metadata_extra:
            short_pct_move_ranges = _parse_range_list(
                metadata_extra.get("short_pct_move_ranges")
            )
        short_relative_volume_ranges = _parse_range_list(
            data.get("short_relative_volume_ranges")
        )
        if not short_relative_volume_ranges and metadata_extra:
            short_relative_volume_ranges = _parse_range_list(
                metadata_extra.get("short_relative_volume_ranges")
            )
        return cls(
            id=str(data.get("id")) if data.get("id") is not None else None,
            min_relative_volume=_coerce_from_keys(
                data,
                [
                    "min_relative_volume",
                    "minRelativeVolume",
                    "S",
                    "s",
                ],
            ),
            max_relative_volume=_coerce_from_keys(
                data,
                [
                    "max_relative_volume",
                    "maxRelativeVolume",
                    "T",
                    "t",
                ],
            ),
            min_atr_mult=_coerce_from_keys(
                data, ["min_atr_mult", "minAtrMult", "U", "u"]
            ),
            min_pct_move=_coerce_from_keys(
                data, ["min_pct_move", "minPctMove", "V", "v"]
            ),
            max_pct_move=_coerce_from_keys(
                data, ["max_pct_move", "maxPctMove", "W", "w"]
            ),
            max_upper_wick_pct=_coerce_from_keys(
                data, ["max_upper_wick_pct", "maxUpperWickPct", "X", "x"]
            ),
            max_lower_wick_pct=_coerce_from_keys(
                data, ["max_lower_wick_pct", "maxLowerWickPct", "Y", "y"]
            ),
            allow_long=_to_bool(data.get("allow_long"), True),
            allow_short=_to_bool(data.get("allow_short"), True),
            metrics=tuple(metrics),
            short_pct_move_ranges=short_pct_move_ranges,
            short_relative_volume_ranges=short_relative_volume_ranges,
            metadata=metadata,
            created_at=_parse_datetime(data.get("created_at")),
            updated_at=_parse_datetime(data.get("updated_at")),
        )

    def to_domain(self) -> DomainThresholds:
        return DomainThresholds(
            id=self.id,
            min_relative_volume=_float_or_default(self.min_relative_volume),
            max_relative_volume=_float_or_default(self.max_relative_volume),
            min_atr_mult=_float_or_default(self.min_atr_mult),
            min_pct_move=_float_or_default(self.min_pct_move),
            max_pct_move=_float_or_default(self.max_pct_move),
            max_upper_wick_pct=_float_or_default(self.max_upper_wick_pct),
            max_lower_wick_pct=_float_or_default(self.max_lower_wick_pct),
            short_pct_move_ranges=[*self.short_pct_move_ranges],
            short_relative_volume_ranges=[*self.short_relative_volume_ranges],
            allow_long=self.allow_long,
            allow_short=self.allow_short,
            metrics=[metric.to_domain() for metric in self.metrics],
            metadata=self.metadata,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )


def _coerce_from_keys(
    raw: Mapping[str, object],
    keys: Sequence[str],
    fallback: float | None = None,
) -> float | None:
    for key in keys:
        if key in raw:
            candidate = raw.get(key)
            if isinstance(candidate, (str, int, float, bool)) or candidate is None:
                value = _to_optional_float(candidate)
                if value is not None:
                    return value
    return fallback


@dataclass(slots=True)
class ThresholdsConfig:
    default: ThresholdConfig
    symbols: dict[str, ThresholdConfig]

    @classmethod
    def from_raw(cls, raw: ThresholdsSection | None) -> "ThresholdsConfig":
        if raw is None:
            data: ThresholdsSection = ThresholdsSection()
        else:
            data = raw
        default_cfg = ThresholdConfig.from_mapping(data.get("default"))
        symbols_cfg: dict[str, ThresholdConfig] = {}
        symbols_raw = data.get("symbols") or {}
        for key, value in symbols_raw.items():
            symbols_cfg[key] = ThresholdConfig.from_mapping(value)
        return cls(default=default_cfg, symbols=symbols_cfg)


@dataclass(slots=True)
class SymbolSelectionConfig:
    quote_suffix: str = "USDT"
    min_quote_volume: float = 5_000_000.0
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_raw(cls, raw: SymbolSelectionSection | None) -> "SymbolSelectionConfig":
        if raw is None:
            data: SymbolSelectionSection = SymbolSelectionSection()
        else:
            data = raw
        quote_suffix = "USDT"
        quote_suffix_raw = data.get("quote_suffix")
        if quote_suffix_raw is not None:
            candidate = str(quote_suffix_raw).strip().upper()
            if candidate:
                quote_suffix = candidate
        min_volume_value = data.get("min_quote_volume")
        min_quote_volume = _to_optional_float(min_volume_value) or 5_000_000.0
        return cls(
            quote_suffix=quote_suffix,
            min_quote_volume=min_quote_volume,
            allow=_normalize_symbol_set(data.get("allow")),
            deny=_normalize_symbol_set(data.get("deny")),
        )


@dataclass(slots=True)
class ModeSelectionOverrides:
    allow: frozenset[str] = field(default_factory=frozenset)
    deny: frozenset[str] = field(default_factory=frozenset)
    symbols: frozenset[str] = field(default_factory=frozenset)

    @classmethod
    def from_raw(cls, raw: ModeSelectionSection | None) -> "ModeSelectionOverrides":
        if raw is None:
            data: ModeSelectionSection = ModeSelectionSection()
        else:
            data = raw
        return cls(
            allow=_normalize_symbol_set(data.get("allow")),
            deny=_normalize_symbol_set(data.get("deny")),
            symbols=_normalize_symbol_set(data.get("symbols")),
        )


@dataclass(slots=True)
class BacktestConfig:
    enabled: bool
    window: int
    timeframes: tuple[Timeframe, ...]
    limit: int | None
    history_batches: int
    selection: ModeSelectionOverrides

    @classmethod
    def from_raw(cls, raw: BacktestSection | None) -> "BacktestConfig":
        if raw is None:
            data: BacktestSection = BacktestSection()
        else:
            data = raw
        enabled = _to_bool(data.get("enabled"), True)
        window = _to_int(data.get("window"), 50, minimum=1)
        timeframes = _parse_timeframes(data.get("timeframes"))
        if not timeframes:
            single_timeframe = data.get("timeframe")
            timeframe_value = _parse_timeframe(single_timeframe)
            if timeframe_value is not None:
                timeframes = (timeframe_value,)
        if not timeframes:
            timeframes = _DEFAULT_BACKTEST_TIMEFRAMES
        limit = _to_optional_int(data.get("limit"))
        history_batches = _to_int(data.get("history_batches"), 10, minimum=1)
        selection = ModeSelectionOverrides.from_raw(data)
        return cls(
            enabled=enabled,
            window=window,
            timeframes=timeframes,
            limit=limit,
            history_batches=history_batches,
            selection=selection,
        )


def _parse_timeframes(
    value: Sequence[str | Timeframe] | str | Timeframe | None,
) -> tuple[Timeframe, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, Timeframe)):
        candidates: Iterable[str | Timeframe] = [value]
    elif isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
        candidates = value
    else:
        return tuple()
    parsed: list[Timeframe] = []
    for item in candidates:
        timeframe = _parse_timeframe(item)
        if timeframe is not None:
            parsed.append(timeframe)
    return tuple(parsed)


def _parse_timeframe(value: str | Timeframe | None) -> Timeframe | None:
    if isinstance(value, Timeframe):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return Timeframe(value)
        except ValueError:
            return None
    return None


@dataclass(slots=True)
class LiveConfig:
    enabled: bool
    providers: tuple[str, ...]
    timeframe: Timeframe
    window: int
    selection: ModeSelectionOverrides

    @classmethod
    def from_raw(cls, raw: LiveSection | None) -> "LiveConfig":
        if raw is None:
            data: LiveSection = LiveSection()
        else:
            data = raw
        enabled = _to_bool(data.get("enabled"), True)
        providers = _parse_providers(data)
        timeframe = _parse_timeframe(data.get("timeframe")) or Timeframe.M5
        window = _to_int(data.get("window"), 50, minimum=1)
        selection = ModeSelectionOverrides.from_raw(data)
        return cls(
            enabled=enabled,
            providers=providers,
            timeframe=timeframe,
            window=window,
            selection=selection,
        )


def _parse_providers(raw: LiveSection) -> tuple[str, ...]:
    providers_value = raw.get("providers")
    providers: list[str]
    if isinstance(providers_value, str):
        candidate = providers_value.strip()
        providers = [candidate] if candidate else []
    elif isinstance(providers_value, Sequence) and not isinstance(
        providers_value, (bytes, bytearray, str)
    ):
        providers = [
            item.strip()
            for item in providers_value
            if isinstance(item, str) and item.strip()
        ]
    else:
        providers = []
    if not providers:
        single = raw.get("provider")
        if isinstance(single, str):
            candidate = single.strip()
            if candidate:
                providers = [candidate]
    return tuple(providers)


def parse_symbol_provider_mapping(raw: dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    mapping: dict[str, str] = {}
    for key, value in raw.items():
        normalized = "default" if key.lower() == "default" else key.upper()
        mapping[normalized] = value
    return mapping
