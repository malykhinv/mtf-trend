from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping


@dataclass(slots=True)
class AppConfig:
    raw: Dict[str, Any]

    def get(self, path: str, default: Any = None) -> Any:
        cursor: Any = self.raw
        for part in path.split('.'):
            if not isinstance(cursor, Mapping) or part not in cursor:
                return default
            cursor = cursor[part]
        return cursor


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
