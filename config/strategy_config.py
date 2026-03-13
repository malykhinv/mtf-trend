"""Модуль проекта."""

from __future__ import annotations

from dataclasses import dataclass

from domain.enums.timeframe import Timeframe
from strategy.bee_bite.config import (
    BeeBiteGridMode,
    BeeBiteProfileId,
    BeeBiteReclaimMode,
    BeeBiteRetestMode,
)


@dataclass(slots=True)
class StrategyConfig:
    strategy_id: str = "bee_bite"
    levels_timeframe: Timeframe = Timeframe.D1
    entry_timeframe: Timeframe = Timeframe.M15
    bee_bite_profile: BeeBiteProfileId = "A"
    bee_bite_grid_mode: BeeBiteGridMode = "baseline"
    bee_bite_reclaim_mode: BeeBiteReclaimMode = "strict"
    bee_bite_retest_mode: BeeBiteRetestMode = "confirmation"
    bee_bite_cooldown_hours: int = 8
    bee_bite_max_age_range_hours: int = 24
    bee_bite_portfolio_top_n: int | None = None
