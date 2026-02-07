"""Aggregate application configuration dataclass."""

from __future__ import annotations

from dataclasses import dataclass

from config.backtest_config import BacktestConfig
from config.fetch_config import FetchConfig
from config.simulation_config import SimulationConfig
from config.strategy_config import StrategyConfig


@dataclass(slots=True)
class AppConfig:
    fetch: FetchConfig
    strategy: StrategyConfig
    simulation: SimulationConfig
    backtest: BacktestConfig
