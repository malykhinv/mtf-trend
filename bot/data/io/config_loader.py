from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping

from .config_models import (
    BacktestConfig,
    LiveConfig,
    ModeSelectionOverrides,
    SymbolSelectionConfig,
    ThresholdsConfig,
    parse_symbol_provider_mapping,
)


@dataclass(slots=True)
class AppConfig:
    raw: Dict[str, Any]
    thresholds: ThresholdsConfig = field(init=False)
    symbol_selection: SymbolSelectionConfig = field(init=False)
    symbol_provider_mapping: dict[str, str] = field(init=False)
    backtest: BacktestConfig = field(init=False)
    live: LiveConfig = field(init=False)

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

    def selection_overrides_for(self, mode: str) -> ModeSelectionOverrides:
        if mode == "backtest":
            return self.backtest.selection
        if mode == "live":
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
