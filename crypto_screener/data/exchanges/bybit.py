from __future__ import annotations

from datetime import datetime, timezone
from collections.abc import Mapping
from typing import Optional

import ccxt  # type: ignore

from crypto_screener.data.mappers import map_ohlcv
from crypto_screener.domain.exchange import Exchange
from crypto_screener.domain.models.bar import Bar
from crypto_screener.domain.models.margin_mode import MarginMode
from crypto_screener.domain.models.order_info import OrderInfo
from crypto_screener.domain.models.order_side import OrderSide
from crypto_screener.domain.models.order_status import OrderStatus
from crypto_screener.domain.models.position import Position
from crypto_screener.domain.models.symbol import FuturesSymbol
from crypto_screener.domain.models.timeframe import Timeframe
from crypto_screener.utils.extractors import extract_float, extract_int
from crypto_screener.utils.logger import log


class Bybit(Exchange):
    def __init__(
            self,
            api_key: str,
            api_secret: str
    ) -> None:
        self._client = ccxt.bybit(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "options": {"defaultType": "future"},
                "enableRateLimit": True,
            }
        )

    @staticmethod
    def get_name() -> str:
        return "Bybit"

    def get_futures_symbols(self) -> list[FuturesSymbol]:
        try:
            markets = self._client.load_markets()
            tickers = self._client.fetch_tickers()
        except Exception as exception:
            log.e(f"Ошибка при загрузке фьючерсных инструментов Bybit: {exception}")
            return []
        symbols: list[FuturesSymbol] = []

        for market in markets.values():
            if not market.get("linear"):
                continue

            symbol = market["symbol"]
            ticker = tickers.get(symbol, {})
            info = ticker.get("info", {})
            market_info = market.get("info", {})

            launch_time = market_info.get("launchTime") or market_info.get("createdAt")
            onboard_ms = int(launch_time) if launch_time else 0
            if onboard_ms:
                listing_time = datetime.fromtimestamp(onboard_ms / 1000, tz=timezone.utc)
            else:
                listing_time = datetime.fromtimestamp(0, tz=timezone.utc)

            volume = extract_float(
                ticker.get("quoteVolume"),
                ticker.get("baseVolume"),
                info.get("turnover24h"),
                info.get("volume24h"),
            )
            trades = extract_int(
                info.get("tradeCount"),
                ticker.get("info", {}).get("tradeCount"),
            )

            symbols.append(
                FuturesSymbol(
                    symbol=symbol,
                    listing_time=listing_time,
                    volume_usdt_24h=volume,
                    trades_24h=trades,
                )
            )
        return symbols

    # noinspection DuplicatedCode
    def get_ohlcv(
            self,
            symbol: str,
            timeframe: Timeframe,
            limit: int,
            end: Optional[datetime] = None
    ) -> list[Bar]:
        params: dict[str, int] = {"limit": limit}
        if end:
            timeframe_ms = timeframe.minutes * 60 * 1000
            end_ms = int(end.astimezone(timezone.utc).timestamp() * 1000)
            params["since"] = end_ms - timeframe_ms * limit

        try:
            raw = self._client.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe.tf,
                since=params.get("since"),
                limit=params["limit"],
            )
        except Exception as exception:
            log.e(f"Ошибка при получении OHLCV Bybit для {symbol}: {exception}")
            return []
        return map_ohlcv(raw, end)

    def place_market_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        if not symbol or quantity <= 0:
            raise ValueError("Некорректные параметры ордера: symbol и quantity должны быть заданы")

        params: dict[str, object] = {"reduce_only": False}
        if margin_mode:
            params["marginMode"] = margin_mode.value
            try:
                self._client.set_margin_mode(margin_mode.value, symbol)
            except Exception as exception:
                log.e(f"Не удалось установить режим маржи {margin_mode.value} для {symbol}: {exception}")

        try:
            # noinspection PyTypeChecker
            order = self._client.create_order(
                symbol=symbol,
                type="market",
                side=side.value,
                amount=quantity,
                params=params,
            )
            return str(order.get("id"))
        except Exception as exception:
            log.e(f"Ошибка при выставлении рыночного ордера Bybit для {symbol}: {exception}")
            raise

    def cancel_order(self, symbol: str, order_id: str) -> None:
        if not symbol or not order_id:
            raise ValueError("symbol и order_id должны быть заданы для отмены ордера")

        try:
            self._client.cancel_order(order_id, symbol)
        except Exception as exception:
            log.e(f"Ошибка при отмене ордера {order_id} на Bybit для {symbol}: {exception}")
            raise

    def get_order_status(self, symbol: str, order_id: str) -> OrderInfo:
        if not symbol or not order_id:
            raise ValueError("symbol и order_id должны быть заданы для получения статуса")

        try:
            order = self._client.fetch_order(order_id, symbol)
        except Exception as exception:
            log.e(f"Ошибка при получении статуса ордера {order_id} на Bybit для {symbol}: {exception}")
            raise

        return self._map_order(order)

    def get_position(self, symbol: str) -> Optional[Position]:
        if not symbol:
            raise ValueError("symbol должен быть задан для получения позиции")

        try:
            positions = self._client.fetch_positions([symbol])
        except Exception as exception:
            log.e(f"Ошибка при получении позиции Bybit для {symbol}: {exception}")
            return None

        for position in positions:
            contracts = extract_float(position.get("contracts"), position.get("info", {}).get("size"))
            if not contracts:
                continue
            entry_price = extract_float(position.get("entryPrice")) or 0.0
            unrealized = extract_float(position.get("unrealizedPnl")) or 0.0
            leverage = extract_float(position.get("leverage"))
            return Position(
                symbol=position.get("symbol", symbol),
                quantity=contracts,
                entry_price=entry_price,
                pnl=unrealized,
                leverage=leverage,
            )
        return None

    def place_stop_loss_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            stop_price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        if not symbol or quantity <= 0 or stop_price <= 0:
            raise ValueError("Некорректные параметры стоп-ордера")

        params: dict[str, object] = {
            "triggerPrice": stop_price,
            "reduce_only": reduce_only,
        }
        if margin_mode:
            params["marginMode"] = margin_mode.value
            try:
                self._client.set_margin_mode(margin_mode.value, symbol)
            except Exception as exception:
                log.e(f"Не удалось установить режим маржи {margin_mode.value} для {symbol}: {exception}")

        try:
            # noinspection PyTypeChecker
            order = self._client.create_order(
                symbol=symbol,
                type="market",
                side=side.value,
                amount=quantity,
                params=params,
            )
            return str(order.get("id"))
        except Exception as exception:
            log.e(f"Ошибка при выставлении стоп-ордера Bybit для {symbol}: {exception}")
            raise

    def place_take_profit_order(
            self,
            symbol: str,
            side: OrderSide,
            quantity: float,
            price: float,
            reduce_only: bool = True,
            margin_mode: Optional[MarginMode] = None,
    ) -> str:
        if not symbol or quantity <= 0 or price <= 0:
            raise ValueError("Некорректные параметры тейк-профит ордера")

        params: dict[str, object] = {
            "triggerPrice": price,
            "reduce_only": reduce_only,
        }
        if margin_mode:
            params["marginMode"] = margin_mode.value
            try:
                self._client.set_margin_mode(margin_mode.value, symbol)
            except Exception as exception:
                log.e(f"Не удалось установить режим маржи {margin_mode.value} для {symbol}: {exception}")

        try:
            # noinspection PyTypeChecker
            order = self._client.create_order(
                symbol=symbol,
                type="market",
                side=side.value,
                amount=quantity,
                params=params,
            )
            return str(order.get("id"))
        except Exception as exception:
            log.e(f"Ошибка при выставлении тейк-профит ордера Bybit для {symbol}: {exception}")
            raise

    @staticmethod
    def _map_order_status(status: str) -> OrderStatus:
        status_normalized = (status or "").lower()
        if status_normalized in {"open", "new", "created"}:
            return OrderStatus.NEW
        if status_normalized in {"closed", "filled"}:
            return OrderStatus.FILLED
        if status_normalized in {"canceled", "cancelled"}:
            return OrderStatus.CANCELED
        if status_normalized in {"partial", "partially_filled", "partial_fill"}:
            return OrderStatus.PARTIALLY_FILLED
        return OrderStatus.NEW

    def _map_order(self, order: dict[str, object]) -> OrderInfo:
        order_data = order if isinstance(order, dict) else dict(order)
        info = order_data.get("info")
        info_data = info if isinstance(info, Mapping) else {}
        status = self._map_order_status(order_data.get("status"))
        amount = extract_float(order_data.get("amount"), info_data.get("qty")) or 0.0
        filled = extract_float(order_data.get("filled"), info_data.get("cumExecQty")) or 0.0
        average = extract_float(order_data.get("average"), order_data.get("price"))
        side_value = (order_data.get("side") or "").lower()
        side = OrderSide.BUY if side_value == OrderSide.BUY.value else OrderSide.SELL

        return OrderInfo(
            id=str(order_data.get("id")),
            symbol=order_data.get("symbol", ""),
            side=side,
            quantity=amount,
            filled=filled,
            status=status,
            average_price=average,
        )
