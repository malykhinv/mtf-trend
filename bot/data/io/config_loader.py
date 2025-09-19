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
    MetricsConfig,
    ModeSelectionOverrides,
    SymbolSelectionConfig,
    ThresholdsConfig,
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

    def __post_init__(self) -> None:
        self.thresholds = ThresholdsConfig.from_raw(self.get("thresholds", {}))
        self.symbol_selection = SymbolSelectionConfig.from_raw(
            self.get("symbols.selection", {})
        )
        self.symbol_provider_mapping = parse_symbol_provider_mapping(
            self.get("symbols.providers", {})
        )
        self.backtest = BacktestConfig.from_raw(self.get("backtest", {}))
        self.live = LiveConfig.from_raw(self.get("live", {}))
        self.metrics = MetricsConfig.from_raw(self.get("metrics", {}))
        self.dedup = DedupConfig.from_raw(self.get("dedup", {}))
        env_values: dict[str, str] = {}
        env_raw = self.get("env", {})
        if isinstance(env_raw, Mapping):
            for key, value in env_raw.items():
                if not isinstance(key, str):
                    continue
                if value is None:
                    continue
                env_values[key] = str(value)
        self.env = env_values
        self.providers = parse_exchange_provider_configs(self.get("providers", {}))

    def get(self, path: str, default: Any = None) -> Any:
        cursor: Any = self.raw
        for part in path.split('.'):
            if not isinstance(cursor, Mapping) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor

    @property
    def timezone_name(self) -> str:
        value = self.get("time.zone")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "UTC"

    def selection_overrides_for(self, mode: AppMode | str) -> ModeSelectionOverrides:
        if isinstance(mode, AppMode):
            key = mode.value
        else:
            key = mode
        if key == "backtest":
            return self.backtest.selection
        if key == "live":
            return self.live.selection
        return ModeSelectionOverrides()


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
