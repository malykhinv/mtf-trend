import sys
import pathlib
import types
from decimal import Decimal

import pytest

sys.path.append(str(pathlib.Path(__file__).resolve().parents[1]))

# Stub sklearn to avoid heavy dependency
linear_model_stub = types.SimpleNamespace(LinearRegression=object)
sklearn_stub = types.SimpleNamespace(linear_model=linear_model_stub)
sys.modules.setdefault("sklearn", sklearn_stub)
sys.modules.setdefault("sklearn.linear_model", linear_model_stub)

from strategies.funding_arbitrage import MarketMetrics, check_exit_conditions


def _sample_metrics() -> MarketMetrics:
    return MarketMetrics(
        funding_rate=Decimal("0.01"),
        spread=Decimal("10"),
        liquidity=Decimal("1000"),
        volatility=0.02,
        spot_price=Decimal("100"),
        futures_price=Decimal("100"),
        volume=Decimal("10000"),
        open_interest=Decimal("5000"),
        spot_slippage=Decimal("0.0001"),
        futures_slippage=Decimal("0.0001"),
        slippage=Decimal("0.0002"),
        basis=0.1,
    )


def test_no_spread_threshold_does_not_trigger_exit():
    metrics = _sample_metrics()
    thresholds = {
        "funding_rate": 0.0,
        "liquidity": 0.0,
        "volatility": 1.0,
    }
    assert not check_exit_conditions(metrics, thresholds)


def test_no_volatility_threshold_does_not_trigger_exit():
    metrics = _sample_metrics()
    thresholds = {
        "funding_rate": 0.0,
        "liquidity": 0.0,
        "spread": 1000.0,
    }
    assert not check_exit_conditions(metrics, thresholds)
