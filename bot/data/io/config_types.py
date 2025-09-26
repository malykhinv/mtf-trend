from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Mapping, Sequence

from ...app_modes import AppMode
from ...domain.enums import Timeframe
from ...domain.models.metadata import ThresholdsMetadataPayload


class ConfigStructureError(ValueError):
    """Raised when a configuration file has an unexpected structure."""


_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


def _stringify(value: object) -> str:
    return str(value).strip()


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    raise ConfigStructureError(f"{path} must be a table.")


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    try:
        text = _stringify(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ConfigStructureError(f"Failed to parse numeric value: {value!r}") from exc
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise ConfigStructureError(f"{value!r} is not a valid float") from exc


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return 1 if value else 0
    text = _stringify(value)
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ConfigStructureError(f"{value!r} is not a valid integer") from exc


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = _stringify(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ConfigStructureError(f"{value!r} is not a valid datetime") from exc


def _optional_bool(value: object) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if not lowered:
            return False
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
        return True
    return bool(value)


def _normalize_symbol(value: str) -> str:
    return value.strip().upper()


def _parse_symbol_sequence(value: object, path: str) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        text = value.strip()
        return (_normalize_symbol(text),) if text else tuple()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        symbols: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigStructureError(f"{path} items must be strings.")
            text = item.strip()
            if text:
                symbols.append(_normalize_symbol(text))
        return tuple(dict.fromkeys(symbols))
    raise ConfigStructureError(f"{path} must be a string or sequence of strings.")


@dataclass(slots=True)
class RangePayload:
    minimum: float | None = None
    maximum: float | None = None


def _parse_range_entry(entry: object, path: str) -> RangePayload | None:
    if entry is None:
        return None
    if isinstance(entry, Mapping):
        minimum = _optional_float(entry.get("min", entry.get("min_value")))
        maximum = _optional_float(entry.get("max", entry.get("max_value")))
        if minimum is None and maximum is None:
            return None
        return RangePayload(minimum=minimum, maximum=maximum)
    if isinstance(entry, Sequence) and not isinstance(entry, (bytes, bytearray, str)):
        sequence = list(entry)
        minimum = _optional_float(sequence[0]) if sequence else None
        maximum = _optional_float(sequence[1]) if len(sequence) > 1 else None
        if minimum is None and maximum is None:
            return None
        return RangePayload(minimum=minimum, maximum=maximum)
    minimum = _optional_float(entry)
    if minimum is None:
        return None
    return RangePayload(minimum=minimum, maximum=None)


def _parse_range_sequence(value: object, path: str) -> tuple[RangePayload, ...]:
    if value is None:
        return tuple()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise ConfigStructureError(f"{path} must be a sequence of ranges.")
    ranges: list[RangePayload] = []
    for index, entry in enumerate(value):
        parsed = _parse_range_entry(entry, f"{path}[{index}]")
        if parsed is not None:
            ranges.append(parsed)
    return tuple(ranges)


@dataclass(slots=True)
class ThresholdMetricConfigPayload:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None


@dataclass(slots=True)
class ThresholdConfigPayload:
    id: str | None = None
    min_relative_volume: float | None = None
    max_relative_volume: float | None = None
    min_atr_mult: float | None = None
    min_pct_move: float | None = None
    max_pct_move: float | None = None
    max_upper_wick_pct: float | None = None
    max_lower_wick_pct: float | None = None
    allow_long: bool | None = None
    allow_short: bool | None = None
    metrics: tuple[ThresholdMetricConfigPayload, ...] = ()
    short_pct_move_ranges: tuple[RangePayload, ...] = ()
    short_relative_volume_ranges: tuple[RangePayload, ...] = ()
    metadata: ThresholdsMetadataPayload | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class SymbolThresholdOverridePayload:
    symbol: str
    config: ThresholdConfigPayload


@dataclass(slots=True)
class ThresholdsConfigPayload:
    default: ThresholdConfigPayload
    overrides: tuple[SymbolThresholdOverridePayload, ...] = ()


@dataclass(slots=True)
class ModeSelectionPayload:
    allow: tuple[str, ...] = ()
    deny: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()


@dataclass(slots=True)
class SymbolSelectionPayload:
    quote_suffix: str | None = None
    min_quote_volume: float | None = None
    selection: ModeSelectionPayload = field(default_factory=ModeSelectionPayload)


@dataclass(slots=True)
class SymbolProviderRoutePayload:
    symbol: str
    provider: str


@dataclass(slots=True)
class SymbolsConfigPayload:
    selection: SymbolSelectionPayload = field(default_factory=SymbolSelectionPayload)
    providers: tuple[SymbolProviderRoutePayload, ...] = ()


@dataclass(slots=True)
class MetricsConfigPayload:
    atr_period: int | None = None
    volume_period: int | None = None
    momentum_period: int | None = None


@dataclass(slots=True)
class DedupConfigPayload:
    ttl_seconds: int | None = None
    max_records: int | None = None


@dataclass(slots=True)
class StorageConfigPayload:
    path: str | None = None


@dataclass(slots=True)
class LoggingConfigPayload:
    level: str | None = None


@dataclass(slots=True)
class TimeConfigPayload:
    zone: str | None = None
    mode: AppMode | None = None


@dataclass(slots=True)
class ProviderCredentialPayload:
    value: str | None = None
    env_key: str | None = None


@dataclass(slots=True)
class ProviderConfigPayload:
    name: str
    api_base: str = ""
    rate_limit_per_minute: int | None = None
    min_quote_volume: float | None = None
    api_key: ProviderCredentialPayload = field(default_factory=ProviderCredentialPayload)
    api_secret: ProviderCredentialPayload = field(default_factory=ProviderCredentialPayload)


@dataclass(slots=True)
class ProvidersConfigPayload:
    providers: tuple[ProviderConfigPayload, ...] = ()


@dataclass(slots=True)
class BacktestConfigPayload:
    enabled: bool | None = None
    window: int | None = None
    timeframes: tuple[Timeframe, ...] = ()
    limit: int | None = None
    history_batches: int | None = None
    selection: ModeSelectionPayload = field(default_factory=ModeSelectionPayload)


@dataclass(slots=True)
class LiveConfigPayload:
    enabled: bool | None = None
    providers: tuple[str, ...] = ()
    timeframe: Timeframe | None = None
    window: int | None = None
    selection: ModeSelectionPayload = field(default_factory=ModeSelectionPayload)


@dataclass(slots=True)
class EnvVarPayload:
    key: str
    value: str


@dataclass(slots=True)
class EnvConfigPayload:
    variables: tuple[EnvVarPayload, ...] = ()

    def as_dict(self) -> dict[str, str]:
        return {item.key: item.value for item in self.variables}


@dataclass(slots=True)
class AppConfigPayload:
    mode: AppMode | None = None
    env: EnvConfigPayload = field(default_factory=EnvConfigPayload)
    storage: StorageConfigPayload = field(default_factory=StorageConfigPayload)
    logging: LoggingConfigPayload = field(default_factory=LoggingConfigPayload)
    time: TimeConfigPayload = field(default_factory=TimeConfigPayload)
    thresholds: ThresholdsConfigPayload = field(
        default_factory=lambda: ThresholdsConfigPayload(default=ThresholdConfigPayload())
    )
    symbols: SymbolsConfigPayload = field(default_factory=SymbolsConfigPayload)
    backtest: BacktestConfigPayload = field(default_factory=BacktestConfigPayload)
    live: LiveConfigPayload = field(default_factory=LiveConfigPayload)
    metrics: MetricsConfigPayload = field(default_factory=MetricsConfigPayload)
    dedup: DedupConfigPayload = field(default_factory=DedupConfigPayload)
    providers: ProvidersConfigPayload = field(default_factory=ProvidersConfigPayload)


def parse_app_config_payload(
    raw: Mapping[str, object], *, env_overrides: Mapping[str, str] | None = None
) -> AppConfigPayload:
    payload = AppConfigPayload()

    mode_value = raw.get("mode")
    if isinstance(mode_value, AppMode):
        payload.mode = mode_value
    elif isinstance(mode_value, str):
        payload.mode = AppMode.parse(mode_value)

    payload.env = _parse_env_section(raw.get("env"), env_overrides)
    payload.storage = _parse_storage_section(raw.get("storage"))
    payload.logging = _parse_logging_section(raw.get("logging"))
    payload.time = _parse_time_section(raw.get("time"))
    payload.thresholds = _parse_thresholds_section(raw.get("thresholds"))
    payload.symbols = _parse_symbols_section(raw.get("symbols"))
    payload.backtest = _parse_backtest_section(raw.get("backtest"))
    payload.live = _parse_live_section(raw.get("live"))
    payload.metrics = _parse_metrics_section(raw.get("metrics"))
    payload.dedup = _parse_dedup_section(raw.get("dedup"))
    payload.providers = _parse_providers_section(raw.get("providers"))
    return payload


def _parse_env_section(
    value: object, overrides: Mapping[str, str] | None
) -> EnvConfigPayload:
    env: dict[str, str] = {}
    if value is not None:
        mapping = _as_mapping(value, "env")
        for key, raw_value in mapping.items():
            if not isinstance(key, str):
                raise ConfigStructureError("Environment variable keys must be strings.")
            if raw_value is None:
                continue
            env[key] = _stringify(raw_value)
    if overrides:
        env.update({k: v for k, v in overrides.items()})
    variables = tuple(EnvVarPayload(key=key, value=value) for key, value in env.items())
    return EnvConfigPayload(variables=variables)


def _parse_storage_section(value: object) -> StorageConfigPayload:
    if value is None:
        return StorageConfigPayload()
    if isinstance(value, Mapping):
        path_value = value.get("path")
        if path_value is None:
            return StorageConfigPayload()
        return StorageConfigPayload(path=_stringify(path_value))
    if isinstance(value, (str, int, float, bool)):
        return StorageConfigPayload(path=_stringify(value))
    raise ConfigStructureError("'storage' must be a string or table with a 'path'.")


def _parse_logging_section(value: object) -> LoggingConfigPayload:
    if value is None:
        return LoggingConfigPayload()
    if isinstance(value, Mapping):
        level = value.get("level")
        if level is None:
            return LoggingConfigPayload()
        return LoggingConfigPayload(level=_stringify(level))
    if isinstance(value, (str, int, float, bool)):
        return LoggingConfigPayload(level=_stringify(value))
    raise ConfigStructureError("'logging' must be a string or table with a 'level'.")


def _parse_time_section(value: object) -> TimeConfigPayload:
    if value is None:
        return TimeConfigPayload()
    if isinstance(value, str):
        return TimeConfigPayload(zone=value.strip())
    mapping = _as_mapping(value, "time")
    zone_raw = mapping.get("zone")
    zone = zone_raw.strip() if isinstance(zone_raw, str) else None
    mode_raw = mapping.get("mode")
    mode: AppMode | None = None
    if isinstance(mode_raw, AppMode):
        mode = mode_raw
    elif isinstance(mode_raw, str):
        mode = AppMode.parse(mode_raw)
    return TimeConfigPayload(zone=zone, mode=mode)


def _parse_metrics_section(value: object) -> MetricsConfigPayload:
    if value is None:
        return MetricsConfigPayload()
    mapping = _as_mapping(value, "metrics")
    return MetricsConfigPayload(
        atr_period=_optional_int(mapping.get("atr_period")),
        volume_period=_optional_int(mapping.get("volume_period")),
        momentum_period=_optional_int(mapping.get("momentum_period")),
    )


def _parse_dedup_section(value: object) -> DedupConfigPayload:
    if value is None:
        return DedupConfigPayload()
    mapping = _as_mapping(value, "dedup")
    return DedupConfigPayload(
        ttl_seconds=_optional_int(mapping.get("ttl_seconds")),
        max_records=_optional_int(mapping.get("max_records")),
    )


def _parse_mode_selection_section(value: object, path: str) -> ModeSelectionPayload:
    if value is None:
        return ModeSelectionPayload()
    mapping = _as_mapping(value, path)
    allow = _parse_symbol_sequence(mapping.get("allow"), f"{path}.allow")
    deny = _parse_symbol_sequence(mapping.get("deny"), f"{path}.deny")
    symbols = _parse_symbol_sequence(mapping.get("symbols"), f"{path}.symbols")
    return ModeSelectionPayload(allow=allow, deny=deny, symbols=symbols)


def _parse_symbol_selection_section(value: object) -> SymbolSelectionPayload:
    if value is None:
        return SymbolSelectionPayload()
    mapping = _as_mapping(value, "symbols.selection")
    quote_suffix = mapping.get("quote_suffix")
    suffix = quote_suffix.strip().upper() if isinstance(quote_suffix, str) else None
    min_quote_volume = _optional_float(mapping.get("min_quote_volume"))
    selection = _parse_mode_selection_section(mapping, "symbols.selection")
    return SymbolSelectionPayload(
        quote_suffix=suffix,
        min_quote_volume=min_quote_volume,
        selection=selection,
    )


def _parse_symbol_providers(value: object) -> tuple[SymbolProviderRoutePayload, ...]:
    if value is None:
        return tuple()
    mapping = _as_mapping(value, "symbols.providers")
    routes: list[SymbolProviderRoutePayload] = []
    for key, raw_value in mapping.items():
        if not isinstance(key, str) or not isinstance(raw_value, str):
            raise ConfigStructureError(
                "'symbols.providers' entries must map string symbols to strings."
            )
        symbol = "default" if key.lower() == "default" else _normalize_symbol(key)
        routes.append(SymbolProviderRoutePayload(symbol=symbol, provider=raw_value))
    return tuple(routes)


def _parse_symbols_section(value: object) -> SymbolsConfigPayload:
    if value is None:
        return SymbolsConfigPayload()
    mapping = _as_mapping(value, "symbols")
    selection = _parse_symbol_selection_section(mapping.get("selection"))
    providers = _parse_symbol_providers(mapping.get("providers"))
    return SymbolsConfigPayload(selection=selection, providers=providers)


def _parse_threshold_metric(value: object, path: str) -> ThresholdMetricConfigPayload:
    mapping = _as_mapping(value, path)
    name = mapping.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ConfigStructureError(f"{path}.name must be a non-empty string.")
    return ThresholdMetricConfigPayload(
        name=name.strip(),
        min_value=_optional_float(mapping.get("min_value")),
        max_value=_optional_float(mapping.get("max_value")),
        min_abs_value=_optional_float(mapping.get("min_abs_value")),
    )


def _parse_threshold_metrics(value: object, path: str) -> tuple[ThresholdMetricConfigPayload, ...]:
    if value is None:
        return tuple()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise ConfigStructureError(f"{path} must be an array of tables.")
    metrics: list[ThresholdMetricConfigPayload] = []
    for index, item in enumerate(value):
        metrics.append(_parse_threshold_metric(item, f"{path}[{index}]") )
    return tuple(metrics)


def _parse_threshold_section(value: object, path: str) -> ThresholdConfigPayload:
    mapping = _as_mapping(value or {}, path)
    identifier = mapping.get("id")
    identifier_str = None if identifier is None else _stringify(identifier)
    metadata_raw = mapping.get("metadata")
    metadata_payload = None
    if isinstance(metadata_raw, Mapping):
        metadata_payload = ThresholdsMetadataPayload.from_mapping(metadata_raw)
    metrics = _parse_threshold_metrics(mapping.get("metrics"), f"{path}.metrics")
    short_pct_move_ranges = _parse_range_sequence(
        mapping.get("short_pct_move_ranges"), f"{path}.short_pct_move_ranges"
    )
    short_relative_volume_ranges = _parse_range_sequence(
        mapping.get("short_relative_volume_ranges"), f"{path}.short_relative_volume_ranges"
    )
    return ThresholdConfigPayload(
        id=identifier_str,
        min_relative_volume=_optional_float(
            _first_existing(mapping, ["min_relative_volume", "minRelativeVolume", "S", "s"])
        ),
        max_relative_volume=_optional_float(
            _first_existing(mapping, ["max_relative_volume", "maxRelativeVolume", "T", "t"])
        ),
        min_atr_mult=_optional_float(
            _first_existing(mapping, ["min_atr_mult", "minAtrMult", "U", "u"])
        ),
        min_pct_move=_optional_float(
            _first_existing(mapping, ["min_pct_move", "minPctMove", "V", "v"])
        ),
        max_pct_move=_optional_float(
            _first_existing(mapping, ["max_pct_move", "maxPctMove", "W", "w"])
        ),
        max_upper_wick_pct=_optional_float(
            _first_existing(mapping, ["max_upper_wick_pct", "maxUpperWickPct", "X", "x"])
        ),
        max_lower_wick_pct=_optional_float(
            _first_existing(mapping, ["max_lower_wick_pct", "maxLowerWickPct", "Y", "y"])
        ),
        allow_long=_optional_bool(mapping.get("allow_long")),
        allow_short=_optional_bool(mapping.get("allow_short")),
        metrics=metrics,
        short_pct_move_ranges=short_pct_move_ranges,
        short_relative_volume_ranges=short_relative_volume_ranges,
        metadata=metadata_payload,
        created_at=_optional_datetime(mapping.get("created_at")),
        updated_at=_optional_datetime(mapping.get("updated_at")),
    )


def _first_existing(mapping: Mapping[str, object], keys: Sequence[str]) -> object | None:
    for key in keys:
        if key in mapping:
            return mapping.get(key)
    return None


def _parse_thresholds_section(value: object) -> ThresholdsConfigPayload:
    mapping = _as_mapping(value or {}, "thresholds")
    default_payload = _parse_threshold_section(mapping.get("default", {}), "thresholds.default")
    overrides_raw = mapping.get("symbols")
    overrides: list[SymbolThresholdOverridePayload] = []
    if overrides_raw is not None:
        overrides_mapping = _as_mapping(overrides_raw, "thresholds.symbols")
        for key, threshold_value in overrides_mapping.items():
            if not isinstance(key, str):
                raise ConfigStructureError("Threshold symbol keys must be strings.")
            symbol = _normalize_symbol(key)
            overrides.append(
                SymbolThresholdOverridePayload(
                    symbol=symbol,
                    config=_parse_threshold_section(
                        threshold_value, f"thresholds.symbols.{symbol}"
                    ),
                )
            )
    return ThresholdsConfigPayload(default=default_payload, overrides=tuple(overrides))


def _parse_backtest_section(value: object) -> BacktestConfigPayload:
    if value is None:
        return BacktestConfigPayload()
    mapping = _as_mapping(value, "backtest")
    timeframes = _parse_timeframe_sequence(mapping.get("timeframes"))
    if not timeframes:
        single = _parse_timeframe(mapping.get("timeframe"))
        if single is not None:
            timeframes = (single,)
    return BacktestConfigPayload(
        enabled=_optional_bool(mapping.get("enabled")),
        window=_optional_int(mapping.get("window")),
        timeframes=timeframes,
        limit=_optional_int(mapping.get("limit")),
        history_batches=_optional_int(mapping.get("history_batches")),
        selection=_parse_mode_selection_section(mapping, "backtest"),
    )


def _parse_timeframe(value: object) -> Timeframe | None:
    if isinstance(value, Timeframe):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return Timeframe(text)
        except ValueError:
            return None
    return None


def _parse_timeframe_sequence(value: object) -> tuple[Timeframe, ...]:
    if value is None:
        return tuple()
    if isinstance(value, (str, Timeframe)):
        candidate = _parse_timeframe(value)
        return (candidate,) if candidate else tuple()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        parsed: list[Timeframe] = []
        for item in value:
            timeframe = _parse_timeframe(item)
            if timeframe is not None:
                parsed.append(timeframe)
        return tuple(parsed)
    raise ConfigStructureError("backtest.timeframes must be a string or sequence.")


def _parse_live_section(value: object) -> LiveConfigPayload:
    if value is None:
        return LiveConfigPayload()
    mapping = _as_mapping(value, "live")
    providers = _parse_provider_sequence(mapping.get("providers"))
    if not providers:
        single = mapping.get("provider")
        if isinstance(single, str) and single.strip():
            providers = (single.strip(),)
    return LiveConfigPayload(
        enabled=_optional_bool(mapping.get("enabled")),
        providers=providers,
        timeframe=_parse_timeframe(mapping.get("timeframe")),
        window=_optional_int(mapping.get("window")),
        selection=_parse_mode_selection_section(mapping, "live"),
    )


def _parse_provider_sequence(value: object) -> tuple[str, ...]:
    if value is None:
        return tuple()
    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else tuple()
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        providers: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigStructureError("Provider names must be strings.")
            text = item.strip()
            if text:
                providers.append(text)
        return tuple(dict.fromkeys(providers))
    raise ConfigStructureError("Providers sequence must contain only strings.")


def _parse_provider_credentials(
    mapping: Mapping[str, object], key: str, default_env: str
) -> ProviderCredentialPayload:
    value = mapping.get(key)
    env_key = mapping.get(f"{key}_env")
    return ProviderCredentialPayload(
        value=_stringify(value) if value is not None else None,
        env_key=(
            env_key.strip() if isinstance(env_key, str) and env_key.strip() else default_env
        ),
    )


def _parse_provider_section(name: str, value: object) -> ProviderConfigPayload:
    mapping = _as_mapping(value or {}, f"providers.{name}")
    api_base = _stringify(mapping.get("api_base")) if mapping.get("api_base") is not None else ""
    rate_limit = _optional_int(mapping.get("rate_limit_per_minute"))
    min_quote_volume = _optional_float(mapping.get("min_quote_volume"))
    prefix = name.upper()
    api_key = _parse_provider_credentials(mapping, "api_key", f"{prefix}_API_KEY")
    api_secret = _parse_provider_credentials(mapping, "api_secret", f"{prefix}_API_SECRET")
    return ProviderConfigPayload(
        name=name,
        api_base=api_base,
        rate_limit_per_minute=rate_limit,
        min_quote_volume=min_quote_volume,
        api_key=api_key,
        api_secret=api_secret,
    )


def _parse_providers_section(value: object) -> ProvidersConfigPayload:
    if value is None:
        return ProvidersConfigPayload()
    mapping = _as_mapping(value, "providers")
    providers: list[ProviderConfigPayload] = []
    for key, raw_value in mapping.items():
        if not isinstance(key, str):
            raise ConfigStructureError("Provider names must be strings.")
        providers.append(_parse_provider_section(key, raw_value))
    return ProvidersConfigPayload(providers=tuple(providers))
