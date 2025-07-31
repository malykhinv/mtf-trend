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
    return await _current.place_order(symbol, side, quantity, price)


async def fetch_funding(symbol: str) -> float:
    """Fetch the funding rate for ``symbol`` from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _current.fetch_funding(symbol)


async def get_orderbook(symbol: str, depth: int = 5) -> dict:
    """Retrieve the latest order book from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _current.get_orderbook(symbol, depth)


async def get_balance() -> dict:
    """Return the account balance from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _current.get_balance()

# Import built-in exchanges so they register themselves with the factory.
from . import binance as _binance  # noqa: F401
from . import bybit as _bybit  # noqa: F401
