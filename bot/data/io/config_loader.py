from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping

from bot.app_modes import AppMode

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


@dataclass(slots=True)
class AppConfig:
    raw: Dict[str, Any]
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
    _default_mode_raw: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        raw = self.raw

        self.storage = StorageConfig.from_raw(raw.get("storage"))
        self.logging = LoggingConfig.from_raw(raw.get("logging"))
        self.time = TimeConfig.from_raw(raw.get("time"))
        self._default_mode_raw = raw.get("mode")

        thresholds_raw = raw.get("thresholds", {})
        self.thresholds = ThresholdsConfig.from_raw(thresholds_raw)

        symbols_raw = raw.get("symbols")
        selection_raw: Any = {}
        providers_raw: Any = {}
        if isinstance(symbols_raw, Mapping):
            selection_raw = symbols_raw.get("selection", {})
            providers_raw = symbols_raw.get("providers", {})
        self.symbol_selection = SymbolSelectionConfig.from_raw(selection_raw)
        self.symbol_provider_mapping = parse_symbol_provider_mapping(providers_raw)

        self.backtest = BacktestConfig.from_raw(raw.get("backtest"))
        self.live = LiveConfig.from_raw(raw.get("live"))
        self.metrics = MetricsConfig.from_raw(raw.get("metrics"))
        self.dedup = DedupConfig.from_raw(raw.get("dedup"))
        env_values: dict[str, str] = {}
        env_raw = raw.get("env", {})
        if isinstance(env_raw, Mapping):
            for key, value in env_raw.items():
                if not isinstance(key, str):
                    continue
                if value is None:
                    continue
                env_values[key] = str(value)
        self.env = env_values
        providers_raw = raw.get("providers", {})
        self.providers = parse_exchange_provider_configs(providers_raw)

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


def _parse_optional_mode(value: Any) -> AppMode | None:
    if value is None:
        return None
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
        for key, value in env_overrides.items():
            data.setdefault("env", {})[key] = value
            os.environ.setdefault(key, value)
        return AppConfig(raw=data)
