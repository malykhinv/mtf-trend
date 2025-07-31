"""Utilities for a funding-rate arbitrage strategy.

This module contains helpers for evaluating entry and exit conditions based on
funding rates and simple market microstructure metrics.  It also provides
helpers to open and monitor neutral long/short positions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict

from exchanges import fetch_funding, get_orderbook, place_order
from risk import risk_control
from ai.parameter_optimizer import load_thresholds as _load_thresholds


@dataclass
class MarketMetrics:
    """Container for market metrics relevant to the strategy."""

    funding_rate: float
    spread: float
    liquidity: float
    volatility: float


async def get_market_metrics(symbol: str, depth: int = 5) -> MarketMetrics:
    """Fetch funding, spread, liquidity and a naive volatility estimate.

    Parameters
    ----------
    symbol:
        Trading pair symbol understood by the configured exchange.
    depth:
        Order book depth to request for liquidity calculations.  Defaults to 5.
    """
    funding = await fetch_funding(symbol)
    orderbook = await get_orderbook(symbol, depth=depth)
    bids = [(float(p), float(q)) for p, q in orderbook.get("bids", [])]
    asks = [(float(p), float(q)) for p, q in orderbook.get("asks", [])]

    if bids and asks:
        spread = asks[0][0] - bids[0][0]
        mid = (asks[0][0] + bids[0][0]) / 2
        volatility = spread / mid if mid else float("inf")
    else:
        spread = float("inf")
        volatility = float("inf")

    liquidity = sum(q for _, q in bids) + sum(q for _, q in asks)
    return MarketMetrics(funding, spread, liquidity, volatility)


# ---------------------------------------------------------------------------
# Condition checks
# ---------------------------------------------------------------------------

def check_entry_conditions(metrics: MarketMetrics, thresholds: Dict[str, float]) -> bool:
    """Return ``True`` if all entry thresholds are satisfied."""
    return (
        abs(metrics.funding_rate) >= thresholds.get("funding_rate", 0.0)
        and metrics.spread <= thresholds.get("spread", float("inf"))
        and metrics.liquidity >= thresholds.get("liquidity", 0.0)
        and metrics.volatility <= thresholds.get("volatility", float("inf"))
    )


def check_exit_conditions(metrics: MarketMetrics, thresholds: Dict[str, float]) -> bool:
    """Return ``True`` if any exit condition is met."""
    return (
        abs(metrics.funding_rate) <= thresholds.get("funding_rate", float("inf"))
        or metrics.spread >= thresholds.get("spread", float("-inf"))
        or metrics.liquidity <= thresholds.get("liquidity", float("inf"))
        or metrics.volatility >= thresholds.get("volatility", float("-inf"))
    )


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------

async def open_neutral_position(symbol: str, quantity: float) -> Dict[str, Dict]:
    """Open offsetting long and short positions.

    This naive implementation simply places a buy and a sell market order for
    ``quantity`` units of ``symbol``.  Real-world usage should handle errors and
    slippage appropriately.
    """
    if not risk_control.can_open_position(quantity):
        raise RuntimeError("Risk limits exceeded or trading paused")
    long_order = await place_order(symbol, "BUY", quantity)
    short_order = await place_order(symbol, "SELL", quantity)
    risk_control.update_position(quantity)
    return {"long": long_order, "short": short_order}


async def close_neutral_position(
    symbol: str, quantity: float, pnl: float = 0.0
) -> Dict[str, Dict]:
    """Close an existing neutral position and record PnL."""
    close_long = await place_order(symbol, "SELL", quantity)
    close_short = await place_order(symbol, "BUY", quantity)
    risk_control.update_position(-quantity)
    risk_control.record_pnl(pnl)
    return {"long": close_long, "short": close_short}


async def monitor_neutral_position(
    symbol: str,
    quantity: float,
    exit_thresholds: Dict[str, float],
    poll_interval: float = 5.0,
) -> None:
    """Monitor a neutral position and close it when exit criteria are met."""
    while True:
        metrics = await get_market_metrics(symbol)
        if check_exit_conditions(metrics, exit_thresholds):
            await close_neutral_position(symbol, quantity)
            break
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Dynamic parameter loading
# ---------------------------------------------------------------------------

def get_thresholds(config_thresholds: Dict[str, float]) -> Dict[str, float]:
    """Return strategy thresholds merged with any optimized values.

    Parameters
    ----------
    config_thresholds:
        Thresholds configured in ``config.yaml``.  Values produced by the
        optimizer take precedence over these defaults.
    """

    return _load_thresholds(config_thresholds)
