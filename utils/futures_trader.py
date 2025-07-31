from __future__ import annotations

"""Utilities for executing futures trades via ccxt.

This module provides a small wrapper around ccxt's futures endpoints to
place MARKET and LIMIT-MAKER orders using isolated margin with configurable
leverage.  It also monitors order fills, submits take-profit/stop-loss exit
orders and retries API calls on transient network errors.
"""

from typing import Callable, Optional
import time

import ccxt


class FuturesTrader:
    """Simple ccxt based futures trader.

    Parameters
    ----------
    api_key, api_secret:
        API credentials for the exchange.
    exchange_name:
        ccxt exchange id, defaults to ``"binanceusdm"``.
    exchange:
        Optional pre-initialised ccxt exchange instance.  Mainly useful for
        testing where network calls should be avoided.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        exchange_name: str = "binanceusdm",
        exchange: Optional[ccxt.Exchange] = None,
    ) -> None:
        if exchange is None:
            exchange_class = getattr(ccxt, exchange_name)
            exchange = exchange_class(
                {
                    "apiKey": api_key,
                    "secret": api_secret,
                    "enableRateLimit": True,
                    "options": {"defaultType": "future"},
                }
            )
        self.exchange = exchange

    # ------------------------------------------------------------------
    # internal helpers
    def _retry(
        self,
        func: Callable,
        *args,
        max_retries: int = 3,
        delay: float = 1.0,
        **kwargs,
    ):
        """Call ``func`` retrying on temporary network errors."""

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except (ccxt.NetworkError, ccxt.ExchangeError):
                if attempt == max_retries - 1:
                    raise
                time.sleep(delay)
                delay *= 2

    # ------------------------------------------------------------------
    def set_margin_and_leverage(
        self, symbol: str, leverage: int = 3, margin_mode: str = "ISOLATED"
    ) -> None:
        """Ensure isolated margin and leverage for ``symbol``."""

        self._retry(self.exchange.set_margin_mode, margin_mode, symbol)
        self._retry(self.exchange.set_leverage, leverage, symbol)

    # ------------------------------------------------------------------
    def place_market_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        tp: float | None = None,
        sl: float | None = None,
    ) -> bool:
        """Place a MARKET order and optional TP/SL exits."""

        self.set_margin_and_leverage(symbol)
        order = self._retry(self.exchange.create_order, symbol, "market", side, amount)
        filled = self.monitor_fill(order["id"], symbol)
        if filled:
            self.manage_exit_orders(symbol, side, amount, tp, sl)
        return filled

    # ------------------------------------------------------------------
    def place_limit_maker_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        tp: float | None = None,
        sl: float | None = None,
    ) -> bool:
        """Place a LIMIT-MAKER order with optional TP/SL."""

        self.set_margin_and_leverage(symbol)
        params = {"timeInForce": "GTX"}
        order = self._retry(
            self.exchange.create_order, symbol, "limit", side, amount, price, params
        )
        filled = self.monitor_fill(order["id"], symbol)
        if filled:
            self.manage_exit_orders(symbol, side, amount, tp, sl)
        return filled

    # ------------------------------------------------------------------
    def monitor_fill(self, order_id: str, symbol: str, timeout: float = 30.0) -> bool:
        """Poll the exchange until ``order_id`` is filled or timed out."""

        start = time.time()
        while time.time() - start < timeout:
            order = self._retry(self.exchange.fetch_order, order_id, symbol)
            if order.get("status") == "closed":
                return True
            time.sleep(1)
        return False

    # ------------------------------------------------------------------
    def manage_exit_orders(
        self,
        symbol: str,
        side: str,
        amount: float,
        tp: float | None,
        sl: float | None,
    ) -> None:
        """Submit TP/SL orders and cancel the remaining when one fills."""

        opposite = "sell" if side.lower() == "buy" else "buy"
        tp_id: str | None = None
        sl_id: str | None = None

        if tp is not None:
            tp_order = self._retry(
                self.exchange.create_order,
                symbol,
                "limit",
                opposite,
                amount,
                tp,
                {"reduceOnly": True},
            )
            tp_id = tp_order.get("id")

        if sl is not None:
            sl_order = self._retry(
                self.exchange.create_order,
                symbol,
                "stop",
                opposite,
                amount,
                None,
                {"stopPrice": sl, "reduceOnly": True},
            )
            sl_id = sl_order.get("id")

        if not tp_id and not sl_id:
            return

        while True:
            tp_status = None
            sl_status = None
            if tp_id:
                tp_status = self._retry(self.exchange.fetch_order, tp_id, symbol).get(
                    "status"
                )
            if sl_id:
                sl_status = self._retry(self.exchange.fetch_order, sl_id, symbol).get(
                    "status"
                )

            if tp_status == "closed":
                if sl_id:
                    self._retry(self.exchange.cancel_order, sl_id, symbol)
                break
            if sl_status == "closed":
                if tp_id:
                    self._retry(self.exchange.cancel_order, tp_id, symbol)
                break
            time.sleep(1)

