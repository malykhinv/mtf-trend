"""Scientific Binance Spot Donchian ensemble research implementation."""

from .contracts import (
    AdmissionConfig,
    CatBoostConfig,
    PortfolioConfig,
    ResearchConfig,
    SimulationConfig,
    UniverseConfig,
)
from .trend import build_trend_state
from .universe import build_point_in_time_universe
from .research import run_spot_trend_research, write_research_artifacts

__all__ = [
    "AdmissionConfig",
    "CatBoostConfig",
    "PortfolioConfig",
    "ResearchConfig",
    "SimulationConfig",
    "UniverseConfig",
    "build_point_in_time_universe",
    "build_trend_state",
    "run_spot_trend_research",
    "write_research_artifacts",
]
