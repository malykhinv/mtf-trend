"""Фабрика построения стратегий."""

from __future__ import annotations

from logging import Logger

from config import AppConfig
from strategy.base_strategy import BaseStrategy
from strategy.bee_bite import BeeBiteStrategy
from strategy.breakout.breakout_strategy import BreakoutStrategy


def build_breakout_strategy(config: AppConfig, logger: Logger) -> BreakoutStrategy:
    return BreakoutStrategy(
        commission_rate=config.simulation.commission_rate,
        slippage=config.simulation.slippage,
        logger=logger,
    )


def build_strategy(config: AppConfig, logger: Logger) -> BaseStrategy[object]:
    breakout = build_breakout_strategy(config, logger)
    if config.strategy.strategy_id == "breakout":
        return breakout
    if config.strategy.strategy_id == "bee_bite":
        return BeeBiteStrategy(
            breakout,
            profile_id=config.strategy.bee_bite_profile,
            grid_mode=config.strategy.bee_bite_grid_mode,
        )
    raise ValueError(f"Неподдерживаемый strategy_id: {config.strategy.strategy_id}")
