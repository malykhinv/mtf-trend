"""Core trading bot package scaffolding.

This namespace exposes the foundational building blocks required for
upcoming trading bot orchestration features. The modules currently provide
strictly-typed stubs so that future work can focus on the business logic
without restructuring the package layout again.
"""

from trading_bot import datamodels
from trading_bot.entry_logic import EntryEvaluator
from trading_bot.persistence import AbstractPlanRepository
from trading_bot.planning import TradePlanAssembler

__all__ = [
    "datamodels",
    "EntryEvaluator",
    "AbstractPlanRepository",
    "TradePlanAssembler",
]
