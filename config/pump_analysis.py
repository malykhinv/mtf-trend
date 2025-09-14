from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, Dict

try:  # optional dependency
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml not installed
    yaml = None


@dataclass(frozen=True)
class PumpAnalysisConfig:
    """Configuration for pump candle analysis."""

    growth_pct: float = 3.0
    wick_pct: float = 0.3
    volume_mult: float = 3.0
    volume_window: int = 20
    rehigh_lookahead: int = 3
    atr_window: int = 14


def _from_mapping(data: Dict[str, Any]) -> PumpAnalysisConfig:
    return PumpAnalysisConfig(
        growth_pct=float(data.get("growth_pct", PumpAnalysisConfig.growth_pct)),
        wick_pct=float(data.get("wick_pct", PumpAnalysisConfig.wick_pct)),
        volume_mult=float(data.get("volume_mult", PumpAnalysisConfig.volume_mult)),
        volume_window=int(data.get("volume_window", PumpAnalysisConfig.volume_window)),
        rehigh_lookahead=int(data.get("rehigh_lookahead", PumpAnalysisConfig.rehigh_lookahead)),
        atr_window=int(data.get("atr_window", PumpAnalysisConfig.atr_window)),
    )


def load_config(path: str | Path | None = None) -> PumpAnalysisConfig:
    """Load configuration from ``path`` or environment variables.

    If ``path`` is not provided, ``config/pump_analysis.json`` is attempted. The
    file may be JSON or YAML. Environment variables are used as a fallback.
    """

    default_path = Path(__file__).with_suffix(".json")
    cfg_path = Path(path) if path else default_path
    if cfg_path.exists():
        if cfg_path.suffix.lower() in {".yaml", ".yml"}:
            if not yaml:
                raise RuntimeError("PyYAML is required to load YAML files")
            with cfg_path.open("r", encoding="utf-8") as fh:
                data = yaml.safe_load(fh) or {}
        else:
            with cfg_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
        return _from_mapping(data)

    env_map = {
        "growth_pct": os.getenv("PUMP_GROWTH_PCT"),
        "wick_pct": os.getenv("PUMP_WICK_PCT"),
        "volume_mult": os.getenv("PUMP_VOLUME_MULT"),
        "volume_window": os.getenv("PUMP_VOLUME_WINDOW"),
        "rehigh_lookahead": os.getenv("PUMP_REHIGH_LOOKAHEAD"),
        "atr_window": os.getenv("PUMP_ATR_WINDOW"),
    }
    data = {k: v for k, v in env_map.items() if v is not None}
    return _from_mapping(data)


__all__ = ["PumpAnalysisConfig", "load_config"]
