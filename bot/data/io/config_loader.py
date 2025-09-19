from __future__ import annotations

import os
import tomllib
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Mapping, Sequence, cast

from bot.app_modes import AppMode
from bot.domain.enums import Timeframe

from .config_models import (
    BacktestConfig,
    DedupConfig,
    ExchangeProviderConfig,
    LiveConfig,
    LoggingConfig,
    MetricsConfig,
    ModeSelectionOverrides,
    StorageConfig,
    SymbolSelectionConfig,
    ThresholdsConfig,
    TimeConfig,
    parse_exchange_provider_configs,
    parse_symbol_provider_mapping,
)
from .config_types import (
    AppConfigData,
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
    SymbolsSection,
    ThresholdMetricSection,
    ThresholdSection,
    ThresholdsSection,
    TimeSection,
)


class ConfigStructureError(ValueError):
    """Raised when a configuration file has an unexpected structure."""


@dataclass(slots=True)
class AppConfig:
    raw: Mapping[str, object] | AppConfigData
    is_typed: bool = False
    env: dict[str, str] = field(init=False)
    thresholds: ThresholdsConfig = field(init=False)
    symbol_selection: SymbolSelectionConfig = field(init=False)
    symbol_provider_mapping: dict[str, str] = field(init=False)
    backtest: BacktestConfig = field(init=False)
    live: LiveConfig = field(init=False)
    providers: dict[str, ExchangeProviderConfig] = field(init=False)
    metrics: MetricsConfig = field(init=False)
    dedup: DedupConfig = field(init=False)
    storage: StorageConfig = field(init=False)
    logging: LoggingConfig = field(init=False)
    time: TimeConfig = field(init=False)
    _default_mode_raw: object = field(init=False, repr=False)

    def __post_init__(self) -> None:
        data = (
            cast(AppConfigData, self.raw)
            if self.is_typed
            else _coerce_app_config_data(self.raw)
        )
        self.raw = data

        self.storage = StorageConfig.from_raw(data.get("storage"))
        self.logging = LoggingConfig.from_raw(data.get("logging"))
        self.time = TimeConfig.from_raw(data.get("time"))
        self._default_mode_raw = data.get("mode")

        self.thresholds = ThresholdsConfig.from_raw(data.get("thresholds"))

        symbols_raw = data.get("symbols")
        selection_raw = symbols_raw.get("selection") if symbols_raw else None
        providers_raw = symbols_raw.get("providers") if symbols_raw else None
        self.symbol_selection = SymbolSelectionConfig.from_raw(selection_raw)
        self.symbol_provider_mapping = parse_symbol_provider_mapping(providers_raw)

        self.backtest = BacktestConfig.from_raw(data.get("backtest"))
        self.live = LiveConfig.from_raw(data.get("live"))
        self.metrics = MetricsConfig.from_raw(data.get("metrics"))
        self.dedup = DedupConfig.from_raw(data.get("dedup"))

        env_raw = data.get("env", {})
        self.env = dict(env_raw)

        providers_section = data.get("providers")
        self.providers = parse_exchange_provider_configs(providers_section)

    def selection_overrides_for(self, mode: AppMode) -> ModeSelectionOverrides:
        if mode is AppMode.BACKTEST:
            return self.backtest.selection
        if mode is AppMode.LIVE:
            return self.live.selection
        return ModeSelectionOverrides()

    @property
    def default_mode(self) -> AppMode:
        parsed = _parse_optional_mode(self._default_mode_raw)
        if parsed is not None:
            return parsed
        return AppMode.BACKTEST


def _parse_optional_mode(value: object) -> AppMode | None:
    if value is None:
        return None
    if isinstance(value, AppMode):
        return value
    if isinstance(value, str) and not value.strip():
        return None
    return AppMode.parse(value)


class ConfigLoader:
    def __init__(self, settings_path: Path, env_path: Path | None = None) -> None:
        self._settings_path = settings_path
        self._env_path = env_path

    def _load_env(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self._env_path and self._env_path.exists():
            for line in self._env_path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, value = line.split('=', 1)
                    env[key.strip()] = value.strip()
        return env

    def load(self) -> AppConfig:
        data = tomllib.loads(self._settings_path.read_text(encoding="utf-8"))
        env_overrides = self._load_env()
        typed = _coerce_app_config_data(data, env_overrides=env_overrides)
        for key, value in typed.get("env", {}).items():
            os.environ.setdefault(key, value)
        return AppConfig(raw=typed, is_typed=True)


def _coerce_app_config_data(
    raw: Mapping[str, object] | AppConfigData | None,
    *,
    env_overrides: Mapping[str, str] | None = None,
) -> AppConfigData:
    if raw is None or not isinstance(raw, Mapping):
        raise ConfigStructureError("Top-level configuration must be a mapping.")
    mapping = raw

    data: AppConfigData = {}

    mode_value = mapping.get("mode")
    if mode_value is not None:
        if isinstance(mode_value, AppMode):
            data["mode"] = mode_value.value
        elif isinstance(mode_value, str):
            data["mode"] = mode_value
        else:
            raise ConfigStructureError("'mode' must be a string or AppMode value.")

    env_section = _coerce_env_section(mapping.get("env"))
    if env_overrides:
        env_section.update(env_overrides)
    if env_section:
        data["env"] = env_section

    storage_section = _coerce_storage_section(mapping.get("storage"))
    if storage_section is not None:
        data["storage"] = storage_section

    logging_section = _coerce_logging_section(mapping.get("logging"))
    if logging_section is not None:
        data["logging"] = logging_section

    time_section = _coerce_time_section(mapping.get("time"))
    if time_section is not None:
        data["time"] = time_section

    thresholds_section = _coerce_thresholds_section(mapping.get("thresholds"))
    if thresholds_section is not None:
        data["thresholds"] = thresholds_section

    symbols_section = _coerce_symbols_section(mapping.get("symbols"))
    if symbols_section is not None:
        data["symbols"] = symbols_section

    backtest_section = _coerce_backtest_section(mapping.get("backtest"))
    if backtest_section is not None:
        data["backtest"] = backtest_section

    live_section = _coerce_live_section(mapping.get("live"))
    if live_section is not None:
        data["live"] = live_section

    metrics_section = _coerce_metrics_section(mapping.get("metrics"))
    if metrics_section is not None:
        data["metrics"] = metrics_section

    dedup_section = _coerce_dedup_section(mapping.get("dedup"))
    if dedup_section is not None:
        data["dedup"] = dedup_section

    providers_section = _coerce_providers_section(mapping.get("providers"))
    if providers_section is not None:
        data["providers"] = providers_section

    return data


def _coerce_env_section(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'env' must be a table of key/value pairs.")
    env: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            raise ConfigStructureError("Environment variable keys must be strings.")
        if raw_value is None:
            continue
        env[key] = str(raw_value)
    return env


def _coerce_storage_section(value: object) -> StorageSection | None:
    if value is None:
        return None
    section: StorageSection = {}
    if isinstance(value, Mapping):
        path_value = value.get("path")
        if path_value is None:
            return section
        section["path"] = str(path_value)
        return section
    if isinstance(value, (str, int, float, bool)):
        section["path"] = str(value)
        return section
    raise ConfigStructureError("'storage' must be a string or table with a 'path'.")


def _coerce_logging_section(value: object) -> LoggingSection | None:
    if value is None:
        return None
    section: LoggingSection = {}
    if isinstance(value, Mapping):
        level = value.get("level")
        if level is None:
            return section
        section["level"] = str(level)
        return section
    if isinstance(value, (str, int, float, bool)):
        section["level"] = str(value)
        return section
    raise ConfigStructureError("'logging' must be a string or table with a 'level'.")


def _coerce_time_section(value: object) -> TimeSection | None:
    if value is None:
        return None
    section: TimeSection = {}
    if isinstance(value, str):
        section["zone"] = value
        return section
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'time' must be a table or string zone identifier.")
    zone_raw = value.get("zone")
    if zone_raw is not None:
        section["zone"] = str(zone_raw)
    mode_raw = value.get("mode")
    if mode_raw is not None:
        if isinstance(mode_raw, AppMode):
            section["mode"] = mode_raw.value
        elif isinstance(mode_raw, str):
            section["mode"] = mode_raw
        else:
            raise ConfigStructureError("'time.mode' must be a string or AppMode value.")
    return section


def _coerce_metrics_section(value: object) -> MetricsSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'metrics' must be a table.")
    section: MetricsSection = {}
    for key in ("atr_period", "volume_period", "momentum_period"):
        if key in value:
            section[key] = _coerce_number(value.get(key), f"metrics.{key}")
    return section


def _coerce_dedup_section(value: object) -> DedupSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'dedup' must be a table.")
    section: DedupSection = {}
    for key in ("ttl_seconds", "max_records"):
        if key in value:
            section[key] = _coerce_number(value.get(key), f"dedup.{key}")
    return section


def _coerce_symbols_section(value: object) -> SymbolsSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'symbols' must be a table.")
    section: SymbolsSection = {}

    selection = _coerce_symbol_selection_section(value.get("selection"))
    if selection:
        section["selection"] = selection

    providers_raw = value.get("providers")
    if providers_raw is not None:
        section["providers"] = _coerce_symbol_provider_mapping(providers_raw)

    return section


def _coerce_symbol_selection_section(value: object) -> SymbolSelectionSection:
    section: SymbolSelectionSection = {}
    if value is None:
        return section
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'symbols.selection' must be a table.")
    quote_suffix = value.get("quote_suffix")
    if quote_suffix is not None:
        section["quote_suffix"] = str(quote_suffix)
    if "min_quote_volume" in value:
        section["min_quote_volume"] = _coerce_number(
            value.get("min_quote_volume"), "symbols.selection.min_quote_volume"
        )
    for key in ("allow", "deny"):
        if key in value:
            section[key] = _coerce_symbol_list(value.get(key), f"symbols.selection.{key}")
    return section


def _coerce_symbol_list(value: object, path: str) -> Sequence[str] | str:
    if value is None:
        return ()
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        items: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigStructureError(f"{path} items must be strings.")
            items.append(item)
        return tuple(items)
    raise ConfigStructureError(f"{path} must be a string or sequence of strings.")


def _coerce_symbol_provider_mapping(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'symbols.providers' must be a table.")
    providers: dict[str, str] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str) or not isinstance(raw_value, str):
            raise ConfigStructureError(
                "'symbols.providers' entries must map string symbols to strings."
            )
        providers[key] = raw_value
    return providers


def _coerce_thresholds_section(value: object) -> ThresholdsSection | None:
    mapping: Mapping[str, object]
    if value is None:
        mapping = {}
    elif isinstance(value, Mapping):
        mapping = value
    else:
        raise ConfigStructureError("'thresholds' must be a table.")
    section: ThresholdsSection = {}
    section["default"] = _coerce_threshold_section(
        mapping.get("default"), "thresholds.default"
    )
    symbols_raw = mapping.get("symbols")
    if symbols_raw is not None:
        if not isinstance(symbols_raw, Mapping):
            raise ConfigStructureError("'thresholds.symbols' must be a table.")
        symbols: dict[str, ThresholdSection] = {}
        for key, raw_value in symbols_raw.items():
            if not isinstance(key, str):
                raise ConfigStructureError("Threshold symbol keys must be strings.")
            symbols[key] = _coerce_threshold_section(
                raw_value, f"thresholds.symbols.{key}"
            )
        section["symbols"] = symbols
    return section


def _coerce_threshold_section(value: object, path: str) -> ThresholdSection:
    if value is None:
        mapping: Mapping[str, object] = {}
    elif isinstance(value, Mapping):
        mapping = value
    else:
        raise ConfigStructureError(f"{path} must be a table.")

    section: ThresholdSection = {}
    identifier = mapping.get("id")
    if identifier is not None:
        if isinstance(identifier, (str, int, float, bool)):
            section["id"] = identifier
        else:
            section["id"] = str(identifier)

    min_relative = _coerce_float_from_keys(
        mapping,
        ["min_relative_volume", "minRelativeVolume", "S", "s"],
        path,
    )
    if min_relative is not None:
        section["min_relative_volume"] = min_relative
    max_relative = _coerce_float_from_keys(
        mapping,
        ["max_relative_volume", "maxRelativeVolume", "T", "t"],
        path,
    )
    if max_relative is not None:
        section["max_relative_volume"] = max_relative
    min_atr = _coerce_float_from_keys(
        mapping, ["min_atr_mult", "minAtrMult", "U", "u"], path
    )
    if min_atr is not None:
        section["min_atr_mult"] = min_atr
    min_pct = _coerce_float_from_keys(
        mapping, ["min_pct_move", "minPctMove", "V", "v"], path
    )
    if min_pct is not None:
        section["min_pct_move"] = min_pct
    max_pct = _coerce_float_from_keys(
        mapping, ["max_pct_move", "maxPctMove", "W", "w"], path
    )
    if max_pct is not None:
        section["max_pct_move"] = max_pct
    max_upper = _coerce_float_from_keys(
        mapping, ["max_upper_wick_pct", "maxUpperWickPct", "X", "x"], path
    )
    if max_upper is not None:
        section["max_upper_wick_pct"] = max_upper
    max_lower = _coerce_float_from_keys(
        mapping, ["max_lower_wick_pct", "maxLowerWickPct", "Y", "y"], path
    )
    if max_lower is not None:
        section["max_lower_wick_pct"] = max_lower

    allow_long = mapping.get("allow_long")
    if allow_long is not None:
        section["allow_long"] = _coerce_boolish(allow_long, f"{path}.allow_long")
    allow_short = mapping.get("allow_short")
    if allow_short is not None:
        section["allow_short"] = _coerce_boolish(allow_short, f"{path}.allow_short")

    metrics_raw = mapping.get("metrics")
    section["metrics"] = _coerce_threshold_metrics(metrics_raw, f"{path}.metrics")

    section["short_pct_move_ranges"] = _coerce_range_sequence(
        mapping.get("short_pct_move_ranges"), f"{path}.short_pct_move_ranges"
    )
    section["short_relative_volume_ranges"] = _coerce_range_sequence(
        mapping.get("short_relative_volume_ranges"),
        f"{path}.short_relative_volume_ranges",
    )

    metadata_raw = mapping.get("metadata")
    if metadata_raw is not None:
        section["metadata"] = _coerce_metadata(metadata_raw, f"{path}.metadata")

    created_at = mapping.get("created_at")
    if isinstance(created_at, (str, datetime)):
        section["created_at"] = created_at
    elif created_at is not None:
        section["created_at"] = str(created_at)
    updated_at = mapping.get("updated_at")
    if isinstance(updated_at, (str, datetime)):
        section["updated_at"] = updated_at
    elif updated_at is not None:
        section["updated_at"] = str(updated_at)

    return section


def _coerce_threshold_metrics(
    value: object, path: str
) -> Sequence[ThresholdMetricSection]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise ConfigStructureError(f"{path} must be an array of tables.")
    metrics: list[ThresholdMetricSection] = []
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise ConfigStructureError(f"{path}[{index}] must be a table.")
        metrics.append(_coerce_threshold_metric(item))
    return tuple(metrics)


def _coerce_threshold_metric(raw: Mapping[str, object]) -> ThresholdMetricSection:
    section: ThresholdMetricSection = {}
    name_value = raw.get("name")
    if name_value is not None:
        section["name"] = str(name_value)
    for key in ("min_value", "max_value", "min_abs_value"):
        if key in raw:
            section[key] = _coerce_number(raw.get(key), f"thresholds.metric.{key}")
    return section


def _coerce_range_sequence(value: object, path: str) -> Sequence[RangeEntry]:
    if value is None:
        return ()
    if not isinstance(value, Sequence) or isinstance(value, (bytes, bytearray, str)):
        raise ConfigStructureError(f"{path} must be an array.")
    ranges: list[RangeEntry] = []
    for index, entry in enumerate(value):
        ranges.append(
            _coerce_range_entry(entry, f"{path}[{index}]")
        )
    return tuple(ranges)


def _coerce_range_entry(value: object, path: str) -> RangeEntry:
    if value is None:
        return None
    if isinstance(value, Mapping):
        result: dict[str, NumberInput | None] = {}
        for key, raw_value in value.items():
            if not isinstance(key, str):
                raise ConfigStructureError(f"{path} keys must be strings.")
            result[key] = _coerce_number(raw_value, f"{path}.{key}")
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        return tuple(_coerce_number(item, f"{path}[{index}]") for index, item in enumerate(value))
    if isinstance(value, (str, int, float, bool)):
        return _coerce_number(value, path)
    raise ConfigStructureError(f"{path} must be a mapping, sequence, or number.")


def _coerce_metadata(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ConfigStructureError(f"{path} must be a table.")
    metadata: dict[str, object] = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            raise ConfigStructureError(f"{path} keys must be strings.")
        metadata[key] = raw_value
    return metadata


def _coerce_backtest_section(value: object) -> BacktestSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'backtest' must be a table.")
    section: BacktestSection = {}
    if "enabled" in value:
        enabled_raw = value.get("enabled")
        if enabled_raw is not None:
            section["enabled"] = _coerce_boolish(enabled_raw, "backtest.enabled")
    if "window" in value:
        section["window"] = _coerce_number(value.get("window"), "backtest.window")
    if "timeframes" in value:
        section["timeframes"] = _coerce_timeframes(
            value.get("timeframes"), "backtest.timeframes"
        )
    if "timeframe" in value:
        timeframe_raw = value.get("timeframe")
        if timeframe_raw is not None:
            section["timeframe"] = timeframe_raw
    if "limit" in value:
        section["limit"] = _coerce_number(value.get("limit"), "backtest.limit")
    if "history_batches" in value:
        section["history_batches"] = _coerce_number(
            value.get("history_batches"), "backtest.history_batches"
        )
    selection = _coerce_mode_selection_section(value, "backtest")
    if "allow" in selection:
        section["allow"] = selection["allow"]
    if "deny" in selection:
        section["deny"] = selection["deny"]
    if "symbols" in selection:
        section["symbols"] = selection["symbols"]
    return section


def _coerce_timeframes(value: object, path: str) -> Sequence[str | Timeframe]:
    if value is None:
        return ()
    if isinstance(value, (str, Timeframe)):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        items: list[str | Timeframe] = []
        for index, item in enumerate(value):
            if isinstance(item, (str, Timeframe)):
                items.append(item)
            else:
                raise ConfigStructureError(
                    f"{path}[{index}] must be a timeframe identifier."
                )
        return tuple(items)
    raise ConfigStructureError(f"{path} must be a string or sequence of strings.")


def _coerce_mode_selection_section(
    value: Mapping[str, object], path: str
) -> ModeSelectionSection:
    section: ModeSelectionSection = {}
    for key in ("allow", "deny", "symbols"):
        if key in value:
            section[key] = _coerce_symbol_list(value.get(key), f"{path}.{key}")
    return section


def _coerce_live_section(value: object) -> LiveSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'live' must be a table.")
    section: LiveSection = {}
    if "enabled" in value:
        enabled_raw = value.get("enabled")
        if enabled_raw is not None:
            section["enabled"] = _coerce_boolish(enabled_raw, "live.enabled")
    if "providers" in value:
        section["providers"] = _coerce_provider_sequence(
            value.get("providers"), "live.providers"
        )
    if "provider" in value:
        provider_raw = value.get("provider")
        if provider_raw is not None:
            section["provider"] = str(provider_raw)
    if "timeframe" in value:
        timeframe_raw = value.get("timeframe")
        if timeframe_raw is not None:
            section["timeframe"] = timeframe_raw
    if "window" in value:
        section["window"] = _coerce_number(value.get("window"), "live.window")
    selection = _coerce_mode_selection_section(value, "live")
    if "allow" in selection:
        section["allow"] = selection["allow"]
    if "deny" in selection:
        section["deny"] = selection["deny"]
    if "symbols" in selection:
        section["symbols"] = selection["symbols"]
    return section


def _coerce_provider_sequence(value: object, path: str) -> Sequence[str] | str:
    if value is None:
        return ()
    if isinstance(value, str):
        return value
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray, str)):
        providers: list[str] = []
        for item in value:
            if not isinstance(item, str):
                raise ConfigStructureError(f"{path} entries must be strings.")
            providers.append(item)
        return tuple(providers)
    raise ConfigStructureError(f"{path} must be a string or sequence of strings.")


def _coerce_providers_section(value: object) -> ProvidersSection | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ConfigStructureError("'providers' must be a table.")
    providers: ProvidersSection = {}
    for key, raw_value in value.items():
        if not isinstance(key, str):
            raise ConfigStructureError("Provider names must be strings.")
        providers[key] = _coerce_provider_section(raw_value, f"providers.{key}")
    return providers


def _coerce_provider_section(value: object, path: str) -> ProviderSection:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ConfigStructureError(f"{path} must be a table.")
    section: ProviderSection = {}
    api_base = value.get("api_base")
    if api_base is not None:
        section["api_base"] = str(api_base)
    ws_base = value.get("ws_base")
    if ws_base is not None:
        section["ws_base"] = str(ws_base)
    api_key = value.get("api_key")
    if api_key is not None:
        section["api_key"] = str(api_key)
    api_key_env = value.get("api_key_env")
    if api_key_env is not None:
        section["api_key_env"] = str(api_key_env)
    api_secret = value.get("api_secret")
    if api_secret is not None:
        section["api_secret"] = str(api_secret)
    api_secret_env = value.get("api_secret_env")
    if api_secret_env is not None:
        section["api_secret_env"] = str(api_secret_env)
    rate_limit = _coerce_number(value.get("rate_limit_per_minute"), f"{path}.rate_limit_per_minute")
    if rate_limit is not None:
        section["rate_limit_per_minute"] = rate_limit
    min_volume = _coerce_number(value.get("min_quote_volume"), f"{path}.min_quote_volume")
    if min_volume is not None:
        section["min_quote_volume"] = min_volume
    return section


def _coerce_boolish(value: object, path: str) -> bool | str | int | float:
    if isinstance(value, (bool, str, int, float)):
        return value
    if value is None:
        return False
    raise ConfigStructureError(f"{path} must be a boolean, number, or string.")


def _coerce_number(value: object, path: str) -> NumberInput:
    if value is None:
        return None
    if isinstance(value, (int, float, bool, str)):
        return value
    raise ConfigStructureError(f"{path} must be numeric or string.")


def _coerce_optional_float_value(value: NumberInput, path: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            return float(stripped)
        except ValueError as exc:  # pragma: no cover - defensive
            raise ConfigStructureError(f"{path} must be numeric.") from exc
    raise ConfigStructureError(f"{path} must be numeric.")


def _coerce_float_from_keys(
    mapping: Mapping[str, object], keys: Sequence[str], path: str
) -> float | None:
    for key in keys:
        if key in mapping:
            number = _coerce_optional_float_value(
                _coerce_number(mapping.get(key), f"{path}.{key}"),
                f"{path}.{key}",
            )
            if number is not None:
                return number
    return None


