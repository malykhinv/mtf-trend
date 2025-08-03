import sys
import pathlib
import types
from decimal import Decimal

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub heavy dependencies of funding_arbitrage
ai_module = types.ModuleType("ai")
parameter_optimizer = types.ModuleType("parameter_optimizer")


def load_thresholds(defaults):
    return defaults


async def periodic_optimization():
    return None


parameter_optimizer.load_thresholds = load_thresholds
parameter_optimizer.periodic_optimization = periodic_optimization
ai_module.parameter_optimizer = parameter_optimizer
sys.modules["ai"] = ai_module
sys.modules["ai.parameter_optimizer"] = parameter_optimizer

from strategies import funding_arbitrage as fa


def test_calculate_basis_positive() -> None:
    assert fa.calculate_basis(110.0, 100.0) == Decimal("10")


def test_calculate_basis_non_positive() -> None:
    assert fa.calculate_basis(100.0, 0.0) is None
    assert fa.calculate_basis(100.0, -1.0) is None
