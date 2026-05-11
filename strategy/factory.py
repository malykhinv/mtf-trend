"""Factory for building strategies."""

from __future__ import annotations

from config import AppConfig
from strategy.base_strategy import BaseStrategy


def build_strategy(config: AppConfig, _logger: object = None) -> BaseStrategy[object]:
    del _logger
    if config.strategy.strategy_id == "pno":
        from strategy.pno import PnoStrategy

        return PnoStrategy(
            deposit=config.strategy.pno_deposit,
            risk_pct=config.strategy.pno_risk_pct,
            entry_confirmation_mode_filter=config.strategy.pno_entry_confirmation_mode,
            category_mode_filter=config.strategy.pno_category_mode,
            cache_dir=config.backtest.cache_dir,
        )
    raise ValueError(f"Стратегия «{config.strategy.strategy_id}» не поддерживается.")
