"""Unified exchange interface for strategy modules.

This package exposes helper functions :func:`configure`, order placement
utilities and various helpers which delegate to the configured exchange
implementation.

Example
-------
>>> import exchanges
>>> exchanges.configure("binance", api_key="key", api_secret="secret")
>>> await exchanges.fetch_funding("BTCUSDT")
"""
from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
import logging
import time
from typing import Any, Coroutine, Dict, Optional, Set, Tuple, Type

from risk import risk_control

logger = logging.getLogger(__name__)

_OUTSTANDING: Set[Tuple[str, str]] = set()

# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------


class BaseExchange(ABC):
    """Abstract base class for exchange implementations.

    Only a very small subset of functionality is required by the test suite
    and thus the concrete implementations found in this repository intentionally
    keep many operations as stubs.  The additional order management methods
    defined here provide sensible defaults so that unit tests can exercise the
    high level logic without performing real network requests.
    """

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

    # ------------------------------------------------------------------
    # Optional order management helpers
    # ------------------------------------------------------------------

    async def get_order_status(self, order_id: str, market: str) -> dict:
        """Return the status for ``order_id``.

        Exchange implementations can override this with real API calls.  The
        default implementation assumes the order is immediately filled which is
        sufficient for unit tests.
        """

        return {"status": "FILLED", "order_id": order_id}

    async def cancel_order(self, order_id: str, market: str) -> dict:
        """Cancel ``order_id`` in ``market``.

        The default implementation simply reports a cancelled status.
        """

        return {"status": "CANCELED", "order_id": order_id}


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


async def _handle_timeout() -> None:
    """Cancel all outstanding orders and pause trading."""

    logger.error("API call exceeded %s seconds; cancelling outstanding orders", API_TIMEOUT)
    if _current is not None:
        for market, oid in list(_OUTSTANDING):
            try:
                await asyncio.wait_for(
                    _current.cancel_order(oid, market), API_TIMEOUT
                )
            except Exception as exc:  # pragma: no cover - best effort
                logger.error("Failed to cancel %s %s: %s", market, oid, exc)
        _OUTSTANDING.clear()
    risk_control.pause()


async def _await_with_timeout(coro: Coroutine[Any, Any, Any]) -> Any:
    try:
        return await asyncio.wait_for(coro, API_TIMEOUT)
    except asyncio.TimeoutError:
        await _handle_timeout()
        raise


def _track_order(market: str, order_id: str) -> None:
    _OUTSTANDING.add((market, order_id))


def _untrack_order(market: str, order_id: str) -> None:
    _OUTSTANDING.discard((market, order_id))


async def place_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Place an order using the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _await_with_timeout(
        _current.place_order(symbol, side, quantity, price)
    )


async def _poll_fill(order_id: str, market: str) -> None:
    """Poll ``order_id`` until the exchange reports it as filled.

    The function relies on :meth:`BaseExchange.get_order_status` and sleeps for
    short intervals between requests.  ``BaseExchange`` provides a stub
    implementation so that tests which do not interact with live exchanges can
    still run deterministically.
    """

    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")

    start = time.monotonic()
    while True:
        status = await _await_with_timeout(
            _current.get_order_status(order_id, market)
        )
        if status.get("status") == "FILLED":
            _untrack_order(market, order_id)
            return
        if time.monotonic() - start > API_TIMEOUT:
            await _handle_timeout()
            raise RuntimeError(f"Order {order_id} not filled in time")
        await asyncio.sleep(0.5)


async def place_spot_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Place a spot order and wait for full execution."""

    order = await place_order(symbol, side, quantity, price)
    order_id = str(order.get("orderId") or order.get("id") or "")
    _track_order("spot", order_id)
    await _poll_fill(order_id, "spot")
    return order


async def place_perp_order(
    symbol: str, side: str, quantity: float, price: float | None = None
) -> dict:
    """Place a futures/perpetual order and wait for full execution."""

    order = await place_order(symbol, side, quantity, price)
    order_id = str(order.get("orderId") or order.get("id") or "")
    _track_order("perp", order_id)
    await _poll_fill(order_id, "perp")
    return order


async def fetch_funding(symbol: str) -> float:
    """Fetch the funding rate for ``symbol`` from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _await_with_timeout(_current.fetch_funding(symbol))


async def get_orderbook(symbol: str, depth: int = 5) -> dict:
    """Retrieve the latest order book from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _await_with_timeout(_current.get_orderbook(symbol, depth))


async def get_balance() -> dict:
    """Return the account balance from the configured exchange."""
    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")
    return await _await_with_timeout(_current.get_balance())


async def hedge(symbol: str, quantity: float) -> Dict[str, Dict]:
    """Place offsetting spot and futures orders with rollback on failure."""

    if _current is None:  # pragma: no cover - defensive programming
        raise RuntimeError("Exchange not configured")

    # Spot leg -------------------------------------------------------------
    spot_order: Dict = await place_spot_order(symbol, "BUY", quantity)
    spot_id = str(spot_order.get("orderId") or spot_order.get("id") or "")

    # Futures leg ----------------------------------------------------------
    try:
        perp_order: Dict = await place_perp_order(symbol, "SELL", quantity)
        return {"spot": spot_order, "perp": perp_order}
    except Exception as exc:
        # Rollback the spot leg if the futures leg fails in any way.
        try:
            cancel_resp = await _await_with_timeout(
                _current.cancel_order(spot_id, "spot")
            )
            logger.warning("Rolled back spot order %s: %s", spot_id, cancel_resp)
        except Exception as cancel_exc:  # pragma: no cover - best effort
            logger.error(
                "Failed to rollback spot order %s: %s", spot_id, cancel_exc
            )
        raise RuntimeError("Hedge placement failed; spot leg rolled back") from exc

# Import built-in exchanges so they register themselves with the factory.
from . import binance as _binance  # noqa: F401
from . import bybit as _bybit  # noqa: F401
