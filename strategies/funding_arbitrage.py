"""Utilities for a funding-rate arbitrage strategy.

This module contains helpers for evaluating entry and exit conditions based on
funding rates and simple market microstructure metrics.  It also provides
helpers to open and monitor neutral long/short positions.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Dict

from exchanges import fetch_funding, get_orderbook, hedge, place_order
from risk import risk_control
from ai.parameter_optimizer import load_thresholds as _load_thresholds
from main import CONFIG


@dataclass
class MarketMetrics:
    """Container for market metrics relevant to the strategy."""

    funding_rate: float
    spread: float
    liquidity: float
    volatility: float
    spot_price: float
    futures_price: float
    volume: float
    open_interest: float
    slippage: float
    basis: float


async def get_market_metrics(symbol: str, depth: int = 5) -> MarketMetrics:
    """Fetch comprehensive market metrics for ``symbol``.

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
        futures_price = (asks[0][0] + bids[0][0]) / 2
        volatility = spread / futures_price if futures_price else float("inf")
        slippage = spread / futures_price if futures_price else float("inf")
    else:
        spread = float("inf")
        futures_price = float("nan")
        volatility = float("inf")
        slippage = float("inf")

    spot_price = float(orderbook.get("spot_price", futures_price))
    volume = float(orderbook.get("volume", 0.0))
    open_interest = float(orderbook.get("open_interest", 0.0))
    liquidity = sum(q for _, q in bids) + sum(q for _, q in asks)
    basis = (
        ((futures_price - spot_price) / spot_price) * 100
        if spot_price
        else float("inf")
    )

    return MarketMetrics(
        funding,
        spread,
        liquidity,
        volatility,
        spot_price,
        futures_price,
        volume,
        open_interest,
        slippage,
        basis,
    )


# ---------------------------------------------------------------------------
# Condition checks
# ---------------------------------------------------------------------------

def check_entry_conditions(
    symbol: str,
    quantity: float,
    metrics: MarketMetrics,
    thresholds: Dict[str, float],
) -> bool:
    """Return ``True`` if all entry thresholds are satisfied."""

    whitelist = CONFIG.get("bot", {}).get("whitelist", [])

    return (
        abs(metrics.funding_rate) >= thresholds.get("funding_rate", 0.0)
        and metrics.basis <= thresholds.get("basis", float("inf"))
        and metrics.liquidity >= thresholds.get("liquidity", 0.0)
        and metrics.volume >= thresholds.get("volume", 0.0)
        and metrics.volatility <= thresholds.get("volatility", float("inf"))
        and metrics.open_interest <= thresholds.get("open_interest", float("inf"))
        and thresholds.get("min_trade_size", 0.0)
        <= quantity
        <= thresholds.get("max_trade_size", float("inf"))
        and symbol in whitelist
        and not risk_control.is_symbol_open(symbol)
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
    bot_cfg = CONFIG.get("bot", {})
    if symbol not in bot_cfg.get("whitelist", []):
        raise RuntimeError("Symbol not whitelisted")
    deposit = bot_cfg.get("deposit_size", float("inf"))
    if quantity > deposit:
        raise RuntimeError("Trade size exceeds deposit")
    if not risk_control.can_open_position(quantity) or risk_control.is_symbol_open(symbol):
        raise RuntimeError("Risk limits exceeded, trading paused, or position exists")
    orders = await hedge(symbol, quantity)
    risk_control.update_position(quantity)
    risk_control.mark_symbol_open(symbol)
    return orders


async def close_neutral_position(
    symbol: str, quantity: float, pnl: float = 0.0
) -> Dict[str, Dict]:
    """Close an existing neutral position and record PnL."""
    close_long = await place_order(symbol, "SELL", quantity)
    try:
        close_short = await place_order(symbol, "BUY", quantity)
    except Exception as exc:
        # Rollback long close to restore neutrality
        await place_order(symbol, "BUY", quantity)
        raise RuntimeError("Failed to close hedge; rolled back long leg") from exc
    risk_control.update_position(-quantity)
    risk_control.record_pnl(pnl)
    risk_control.mark_symbol_closed(symbol)
    return {"long": close_long, "short": close_short}


async def monitor_neutral_position(
    symbol: str,
    quantity: float,
    exit_thresholds: Dict[str, float],
    poll_interval: float = 5.0,
) -> None:
    """Monitor a neutral position and close it when exit criteria are met."""
    while True:
        try:
            metrics = await get_market_metrics(symbol)
        except asyncio.TimeoutError:
            await close_neutral_position(symbol, quantity)
            break
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
