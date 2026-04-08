"""Factory for building strategies."""

from __future__ import annotations

from config import AppConfig
from strategy.base_strategy import BaseStrategy
from strategy.bee_bite import BeeBiteStrategy
from strategy.pno import PnoStrategy
from strategy.post_pump_absorption import PostPumpAbsorptionStrategy


def build_strategy(config: AppConfig, _logger: object = None) -> BaseStrategy[object]:
    del _logger
    if config.strategy.strategy_id == "bee_bite":
        return BeeBiteStrategy(
            profile_id=config.strategy.bee_bite_profile,
            grid_mode=config.strategy.bee_bite_grid_mode,
            reclaim_mode=config.strategy.bee_bite_reclaim_mode,
            retest_mode=config.strategy.bee_bite_retest_mode,
            cooldown_hours=config.strategy.bee_bite_cooldown_hours,
            max_age_range_hours=config.strategy.bee_bite_max_age_range_hours,
            portfolio_top_n=config.strategy.bee_bite_portfolio_top_n,
            deposit=config.strategy.bee_bite_deposit,
            risk_pct=config.strategy.bee_bite_risk_pct,
        )
    if config.strategy.strategy_id == "post_pump_absorption":
        return PostPumpAbsorptionStrategy(
            profile_id=config.strategy.post_pump_absorption_profile,
            deposit=config.strategy.post_pump_absorption_deposit,
            risk_pct=config.strategy.post_pump_absorption_risk_pct,
        )
    if config.strategy.strategy_id == "pno":
        return PnoStrategy(
            deposit=config.strategy.pno_deposit,
            risk_pct=config.strategy.pno_risk_pct,
        )
    raise ValueError(f"Unsupported strategy_id: {config.strategy.strategy_id}")
