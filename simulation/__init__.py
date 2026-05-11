"""Shared simulation package exports for strategy and anomaly research."""

from simulation.models.fill import Fill
from simulation.order_processor import OrderProcessor
from simulation.position_simulator import StatefulPositionSimulator
from simulation.risk_manager import RiskConfig, RiskManager
from simulation.exit_manager import ExitManager, ExitManagerConfig
from simulation.position_result_classifier import PositionResultClassifier

__all__ = [
    "Fill",
    "OrderProcessor",
    "StatefulPositionSimulator",
    "PositionResultClassifier",
    "RiskConfig",
    "RiskManager",
    "ExitManager",
    "ExitManagerConfig",
]
