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
    if config.strategy.strategy_id == "retest":
        return build_breakout_strategy(config, logger)
    if config.strategy.strategy_id == "bee_bite":
        return BeeBiteStrategy(
            profile_id=config.strategy.bee_bite_profile,
            grid_mode=config.strategy.bee_bite_grid_mode,
            reclaim_mode=config.strategy.bee_bite_reclaim_mode,
            retest_mode=config.strategy.bee_bite_retest_mode,
            cooldown_hours=config.strategy.bee_bite_cooldown_bars,
            max_age_range_hours=config.strategy.bee_bite_max_age_range,
        )
    raise ValueError(f"Неподдерживаемый strategy_id: {config.strategy.strategy_id}")
