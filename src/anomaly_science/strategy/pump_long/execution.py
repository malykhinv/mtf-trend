"""Compatibility facade for Core-owned path simulation mechanics."""

from anomaly_science.simulation.numpy_path import *  # noqa: F403
from anomaly_science.simulation.numpy_path import __all__

PumpLongTradeResult = LongPathResult
simulate_pump_long_trade = simulate_long_path
__all__ = (*__all__, "PumpLongTradeResult", "simulate_pump_long_trade")
