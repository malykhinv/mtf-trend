from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Mapping

from .config_models import AppConfig
from .config_types import ConfigStructureError, parse_app_config_payload


@dataclass(slots=True)
class ConfigLoader:
    settings_path: Path
    env_path: Path | None = None

    def _load_env(self) -> Dict[str, str]:
        env: Dict[str, str] = {}
        if self.env_path and self.env_path.exists():
            for line in self.env_path.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    env[key.strip()] = value.strip()
        return env

    def load(self) -> AppConfig:
        raw = tomllib.loads(self.settings_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ConfigStructureError("Top-level configuration must be a table.")
        env_overrides = self._load_env()
        payload = parse_app_config_payload(raw, env_overrides=env_overrides)
        for variable in payload.env.variables:
            os.environ.setdefault(variable.key, variable.value)
        return AppConfig(payload=payload)


def build_app_config(raw: Mapping[str, object]) -> AppConfig:
    payload = parse_app_config_payload(raw)
    return AppConfig(payload=payload)
