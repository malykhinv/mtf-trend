"""Factory for building executable strategies."""

from __future__ import annotations

from config import AppConfig
from strategy.base_strategy import BaseStrategy


def build_strategy(config: AppConfig, _logger: object = None) -> BaseStrategy[object]:
    del _logger
    raise ValueError(
        "No executable strategy factory is configured. "
        f"strategy_id={config.strategy.strategy_id!r} is research/anomaly-only."
    )
