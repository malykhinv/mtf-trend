"""Unified exchange interface for strategy modules.

This package exposes helper functions :func:`configure`, :func:`place_order`,
:func:`fetch_funding`, and :func:`get_orderbook` which delegate to the
configured exchange implementation.

Example
-------
>>> import exchanges
>>> exchanges.configure("binance", api_key="key", api_secret="secret")
>>> await exchanges.fetch_funding("BTCUSDT")
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Optional, Type

# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class BaseExchange(ABC):
    """Abstract base class for exchange implementations."""

    @abstractmethod
    async def fetch_funding(self, symbol: str) -> float:
        """Return the current funding rate for ``symbol``."""

    @abstractmethod
    async def place_order(
        self, symbol: str, side: str, quantity: float, price: float | None = None
    ) -> dict:
        """Place an order and return exchange response."""

    @abstractmethod
    async def get_orderbook(self, symbol: str, depth: int = 5) -> dict:
        """Return the latest order book for ``symbol``."""

    @abstractmethod
    async def get_balance(self) -> dict:
        """Return account balance information."""


# ---------------------------------------------------------------------------
# Exchange factory and unified functions
# ---------------------------------------------------------------------------

_EXCHANGES: Dict[str, Type[BaseExchange]] = {}
_current: Optional[BaseExchange] = None
API_TIMEOUT = 30


def register(name: str, cls: Type[BaseExchange]) -> None:
    """Register an exchange implementation."""
    _EXCHANGES[name.lower()] = cls


def configure(name: str, **kwargs) -> None:
    """Configure the active exchange by name."""
    global _current
    try:
        cls = _EXCHANGES[name.lower()]
    except KeyError as exc:  # pragma: no cover - defensive programming
        raise ValueError(f"Unknown exchange: {name}") from exc
    _current = cls(**kwargs)


async def place_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Place an order using the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await asyncio.wait_for(
        _current.place_order(symbol, side, quantity, price), API_TIMEOUT
    )


async def fetch_funding(symbol: str) -> float:
    """Fetch the funding rate for ``symbol`` from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await asyncio.wait_for(_current.fetch_funding(symbol), API_TIMEOUT)


async def get_orderbook(symbol: str, depth: int = 5) -> dict:
    """Retrieve the latest order book from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await asyncio.wait_for(
        _current.get_orderbook(symbol, depth), API_TIMEOUT
    )


async def get_balance() -> dict:
    """Return the account balance from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await asyncio.wait_for(_current.get_balance(), API_TIMEOUT)


async def hedge(symbol: str, quantity: float) -> Dict[str, Dict]:
    """Place offsetting buy and sell orders with rollback on failure."""

    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")

    long_order: Dict = await place_order(symbol, "BUY", quantity)
    try:
        short_order: Dict = await place_order(symbol, "SELL", quantity)
    except Exception as exc:
        # Attempt to rollback the long leg if the short leg fails
        try:
            await place_order(symbol, "SELL", quantity)
        finally:
            pass
        raise RuntimeError("Hedge placement failed; long leg rolled back") from exc
    return {"long": long_order, "short": short_order}

# Import built-in exchanges so they register themselves with the factory.
from . import binance as _binance  # noqa: F401
from . import bybit as _bybit  # noqa: F401
