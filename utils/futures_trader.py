from __future__ import annotations

"""Utilities for executing futures trades via ccxt.

This module provides a small wrapper around ccxt's futures endpoints to
place MARKET and LIMIT-MAKER orders using isolated margin with configurable
leverage.  It also monitors order fills, submits take-profit/stop-loss exit
orders and retries API calls on transient network errors.
"""

from typing import Callable, Optional, Dict, Any
import time

import ccxt
import pandas as pd

from .trade_logger import append_trade


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
        trade_logger: Callable[[Dict[str, Any]], None] | None = append_trade,
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
        self.trade_logger = trade_logger

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
        filled_order = self.monitor_fill(order["id"], symbol)
        if filled_order:
            entry_price = float(
                filled_order.get("average") or filled_order.get("price") or 0.0
            )
            ts = filled_order.get("timestamp")
            entry_time = pd.to_datetime(ts, unit="ms") if ts is not None else pd.Timestamp.utcnow()
            exit_price = self.manage_exit_orders(symbol, side, amount, tp, sl)
            if exit_price is not None and self.trade_logger:
                direction = "long" if side.lower() == "buy" else "short"
                pnl = (
                    exit_price - entry_price
                    if direction == "long"
                    else entry_price - exit_price
                )
                risk = abs(entry_price - sl) if sl is not None else 0.0
                rr = pnl / risk if risk else 0.0
                self.trade_logger(
                    {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_time": entry_time,
                        "entry": entry_price,
                        "stop": sl if sl is not None else 0.0,
                        "tp": tp if tp is not None else 0.0,
                        "exit_time": pd.Timestamp.utcnow(),
                        "exit": exit_price,
                        "pnl": pnl,
                        "rr": rr,
                    }
                )
            return True
        return False

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
        filled_order = self.monitor_fill(order["id"], symbol)
        if filled_order:
            entry_price = float(
                filled_order.get("average") or filled_order.get("price") or price
            )
            ts = filled_order.get("timestamp")
            entry_time = pd.to_datetime(ts, unit="ms") if ts is not None else pd.Timestamp.utcnow()
            exit_price = self.manage_exit_orders(symbol, side, amount, tp, sl)
            if exit_price is not None and self.trade_logger:
                direction = "long" if side.lower() == "buy" else "short"
                pnl = (
                    exit_price - entry_price
                    if direction == "long"
                    else entry_price - exit_price
                )
                risk = abs(entry_price - sl) if sl is not None else 0.0
                rr = pnl / risk if risk else 0.0
                self.trade_logger(
                    {
                        "symbol": symbol,
                        "direction": direction,
                        "entry_time": entry_time,
                        "entry": entry_price,
                        "stop": sl if sl is not None else 0.0,
                        "tp": tp if tp is not None else 0.0,
                        "exit_time": pd.Timestamp.utcnow(),
                        "exit": exit_price,
                        "pnl": pnl,
                        "rr": rr,
                    }
                )
            return True
        return False

    # ------------------------------------------------------------------
    def monitor_fill(
        self, order_id: str, symbol: str, timeout: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """Poll the exchange until ``order_id`` is filled or timed out."""

        start = time.time()
        while time.time() - start < timeout:
            order = self._retry(self.exchange.fetch_order, order_id, symbol)
            if order.get("status") == "closed":
                return order
            time.sleep(1)
        return None

    # ------------------------------------------------------------------
    def manage_exit_orders(
        self,
        symbol: str,
        side: str,
        amount: float,
        tp: float | None,
        sl: float | None,
    ) -> float | None:
        """Submit TP/SL orders and cancel the remaining when one fills.

        Returns the exit price when either TP or SL is hit, otherwise ``None``.
        """

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
            return None

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
                return tp if tp is not None else None
            if sl_status == "closed":
                if tp_id:
                    self._retry(self.exchange.cancel_order, tp_id, symbol)
                return sl if sl is not None else None
            time.sleep(1)

