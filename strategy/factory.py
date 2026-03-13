"""Фабрика построения стратегий."""

from __future__ import annotations

from config import AppConfig
from strategy.base_strategy import BaseStrategy
from strategy.bee_bite import BeeBiteStrategy


def build_strategy(config: AppConfig, _logger: object = None) -> BaseStrategy[object]:
    del _logger
    if config.strategy.strategy_id != "bee_bite":
        raise ValueError(f"Неподдерживаемый strategy_id: {config.strategy.strategy_id}")
    return BeeBiteStrategy(
        profile_id=config.strategy.bee_bite_profile,
        grid_mode=config.strategy.bee_bite_grid_mode,
        reclaim_mode=config.strategy.bee_bite_reclaim_mode,
        retest_mode=config.strategy.bee_bite_retest_mode,
        cooldown_hours=config.strategy.bee_bite_cooldown_hours,
        max_age_range_hours=config.strategy.bee_bite_max_age_range_hours,
        portfolio_top_n=config.strategy.bee_bite_portfolio_top_n,
    )
