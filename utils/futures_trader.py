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
from .risk import RiskManager


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
        risk_manager: RiskManager | None = None,
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
        self.risk_manager = risk_manager

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
        """Place a MARKET order and optional TP/SL exits.

        If ``tp`` is provided two take-profit targets are used: ``tp`` for
        half the position size and a trailing stop for the remaining half. The
        trailing stop is managed inside :meth:`manage_exit_orders`.
        """

        try:
            self.set_margin_and_leverage(symbol)
            order = self._retry(
                self.exchange.create_order, symbol, "market", side, amount
            )
            filled_order = self.monitor_fill(order["id"], symbol)
        except ccxt.BaseError:
            return False
        if filled_order:
            entry_price = float(
                filled_order.get("average") or filled_order.get("price") or 0.0
            )
            ts = filled_order.get("timestamp")
            entry_time = pd.to_datetime(ts, unit="ms") if ts is not None else pd.Timestamp.utcnow()
            exit_events = self.manage_exit_orders(symbol, side, amount, tp, sl)
            if exit_events:
                direction = "long" if side.lower() == "buy" else "short"
                risk = abs(entry_price - sl) if sl is not None else 0.0
                for i, (exit_price, exit_time) in enumerate(exit_events):
                    pnl = (
                        exit_price - entry_price
                        if direction == "long"
                        else entry_price - exit_price
                    )
                    rr = pnl / risk if risk else 0.0
                    if self.trade_logger:
                        self.trade_logger(
                            {
                                "symbol": symbol,
                                "direction": direction,
                                "entry_time": entry_time,
                                "entry": entry_price,
                                "stop": sl if sl is not None else 0.0,
                                "tp": tp if (tp is not None and i == 0) else 0.0,
                                "exit_time": exit_time,
                                "exit": exit_price,
                                "pnl": pnl,
                                "rr": rr,
                            }
                        )
                    if self.risk_manager:
                        self.risk_manager.close_trade(pnl)
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
        """Place a LIMIT-MAKER order with optional TP/SL.

        Behaviour is identical to :meth:`place_market_order` with respect to
        take-profit handling.
        """

        try:
            self.set_margin_and_leverage(symbol)
            params = {"timeInForce": "GTX"}
            order = self._retry(
                self.exchange.create_order,
                symbol,
                "limit",
                side,
                amount,
                price,
                params,
            )
            filled_order = self.monitor_fill(order["id"], symbol)
        except ccxt.BaseError:
            return False
        if filled_order:
            entry_price = float(
                filled_order.get("average") or filled_order.get("price") or price
            )
            ts = filled_order.get("timestamp")
            entry_time = pd.to_datetime(ts, unit="ms") if ts is not None else pd.Timestamp.utcnow()
            exit_events = self.manage_exit_orders(symbol, side, amount, tp, sl)
            if exit_events:
                direction = "long" if side.lower() == "buy" else "short"
                risk = abs(entry_price - sl) if sl is not None else 0.0
                for i, (exit_price, exit_time) in enumerate(exit_events):
                    pnl = (
                        exit_price - entry_price
                        if direction == "long"
                        else entry_price - exit_price
                    )
                    rr = pnl / risk if risk else 0.0
                    if self.trade_logger:
                        self.trade_logger(
                            {
                                "symbol": symbol,
                                "direction": direction,
                                "entry_time": entry_time,
                                "entry": entry_price,
                                "stop": sl if sl is not None else 0.0,
                                "tp": tp if (tp is not None and i == 0) else 0.0,
                                "exit_time": exit_time,
                                "exit": exit_price,
                                "pnl": pnl,
                                "rr": rr,
                            }
                        )
                    if self.risk_manager:
                        self.risk_manager.close_trade(pnl)
            return True
        return False

    # ------------------------------------------------------------------
    def monitor_fill(
        self, order_id: str, symbol: str, timeout: float = 30.0
    ) -> Optional[Dict[str, Any]]:
        """Poll the exchange until ``order_id`` is filled or timed out."""

        start = time.time()
        while time.time() - start < timeout:
            try:
                order = self._retry(self.exchange.fetch_order, order_id, symbol)
            except ccxt.BaseError:
                return None
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
    ) -> list[tuple[float, pd.Timestamp]]:
        """Submit TP/SL orders and manage a trailing stop.

        Two take-profit targets are supported: ``tp`` for 50% of the position
        size and a trailing stop for the remaining half. A fixed ``sl`` can be
        supplied which acts as the initial stop loss before the trailing stop
        kicks in.  The function returns a list of ``(exit_price, exit_time)``
        tuples for each filled exit order.
        """

        opposite = "sell" if side.lower() == "buy" else "buy"
        tp_id: str | None = None
        sl_id: str | None = None
        events: list[tuple[float, pd.Timestamp]] = []
        half_amount = amount / 2
        trailing_step = 0.002
        current_stop = sl if sl is not None else 0.0

        if tp is not None:
            try:
                tp_order = self._retry(
                    self.exchange.create_order,
                    symbol,
                    "limit",
                    opposite,
                    half_amount,
                    tp,
                    {"reduceOnly": True},
                )
                tp_id = tp_order.get("id")
            except ccxt.BaseError:
                return events

        if sl is not None:
            try:
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
            except ccxt.BaseError:
                return events

        if not tp_id and not sl_id:
            return events

        tp_filled = False
        highest: float | None = None

        while True:
            tp_status = None
            sl_status = None
            if tp_id and not tp_filled:
                try:
                    tp_status = self._retry(
                        self.exchange.fetch_order, tp_id, symbol
                    ).get("status")
                except ccxt.BaseError:
                    return events
            if sl_id:
                try:
                    sl_status = self._retry(
                        self.exchange.fetch_order, sl_id, symbol
                    ).get("status")
                except ccxt.BaseError:
                    return events

            if not tp_filled and tp_status == "closed":
                events.append((tp if tp is not None else 0.0, pd.Timestamp.utcnow()))
                tp_filled = True
                # replace stop for remaining half
                if sl_id:
                    try:
                        self._retry(self.exchange.cancel_order, sl_id, symbol)
                    except ccxt.BaseError:
                        pass
                if sl is not None:
                    try:
                        sl_order = self._retry(
                            self.exchange.create_order,
                            symbol,
                            "stop",
                            opposite,
                            half_amount,
                            None,
                            {"stopPrice": sl, "reduceOnly": True},
                        )
                        sl_id = sl_order.get("id")
                        current_stop = sl
                    except ccxt.BaseError:
                        return events
                try:
                    ticker = self._retry(self.exchange.fetch_ticker, symbol)
                    price = float(ticker.get("last") or 0.0)
                except ccxt.BaseError:
                    price = 0.0
                highest = price
            elif sl_status == "closed":
                if tp_id and not tp_filled:
                    try:
                        self._retry(self.exchange.cancel_order, tp_id, symbol)
                    except ccxt.BaseError:
                        pass
                events.append((current_stop, pd.Timestamp.utcnow()))
                return events

            if tp_filled and sl_id:
                try:
                    ticker = self._retry(self.exchange.fetch_ticker, symbol)
                    price = float(ticker.get("last") or 0.0)
                except ccxt.BaseError:
                    price = highest if highest is not None else 0.0

                if side.lower() == "buy":
                    if highest is None or price > highest:
                        highest = price
                    new_stop = (highest or price) * (1 - trailing_step)
                    if new_stop > current_stop:
                        try:
                            self._retry(self.exchange.cancel_order, sl_id, symbol)
                        except ccxt.BaseError:
                            pass
                        try:
                            sl_order = self._retry(
                                self.exchange.create_order,
                                symbol,
                                "stop",
                                opposite,
                                half_amount,
                                None,
                                {"stopPrice": new_stop, "reduceOnly": True},
                            )
                            sl_id = sl_order.get("id")
                            current_stop = new_stop
                        except ccxt.BaseError:
                            return events
                else:
                    if highest is None or price < highest:
                        highest = price
                    new_stop = (highest or price) * (1 + trailing_step)
                    if sl is not None and new_stop < current_stop:
                        try:
                            self._retry(self.exchange.cancel_order, sl_id, symbol)
                        except ccxt.BaseError:
                            pass
                        try:
                            sl_order = self._retry(
                                self.exchange.create_order,
                                symbol,
                                "stop",
                                opposite,
                                half_amount,
                                None,
                                {"stopPrice": new_stop, "reduceOnly": True},
                            )
                            sl_id = sl_order.get("id")
                            current_stop = new_stop
                        except ccxt.BaseError:
                            return events

                try:
                    sl_status = self._retry(
                        self.exchange.fetch_order, sl_id, symbol
                    ).get("status")
                except ccxt.BaseError:
                    return events
                if sl_status == "closed":
                    events.append((current_stop, pd.Timestamp.utcnow()))
                    return events

            time.sleep(0.1)

