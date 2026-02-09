"""Aggregate application configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass

from .backtest_config import BacktestConfig
from .fetch_config import FetchConfig
from .simulation_config import SimulationConfig
from .strategy_config import StrategyConfig


@dataclass(slots=True)
class AppConfig:
    fetch: FetchConfig
    strategy: StrategyConfig
    simulation: SimulationConfig
    backtest: BacktestConfig
