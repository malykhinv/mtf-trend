from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, Iterable, Mapping

import ccxt

from config.config import OrderConfig, PositioningConfig
from domain.models import ActiveOrder, OrderParams, OrderRole
from utils.precision import (
    apply_min_notional,
    apply_min_qty,
    calculate_position_size,
    round_price_to_tick,
    round_quantity_to_step,
)


@dataclass(frozen=True)
class MarketPrecision:
    """Precision and trading limits extracted from the exchange metadata."""

    tick_size: float
    step_size: float
    min_qty: float
    min_notional: float


@dataclass
class OrderManager:
    """Place and manage bracket orders on the exchange."""

    exchange: ccxt.Exchange
    order_config: OrderConfig
    positioning_config: PositioningConfig
    quote_currency: str = "USDT"
    logger: logging.Logger = field(default_factory=lambda: logging.getLogger(__name__))

    def __post_init__(self) -> None:
        self._precision_cache: Dict[str, MarketPrecision] = {}
        self._ensure_markets_loaded()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def create_order_params(self, symbol: str, params: OrderParams) -> OrderParams:
        """Return order parameters rounded according to market precision."""

        precision = self._get_precision(symbol)
        entry_price = round_price_to_tick(params.entry_price, precision.tick_size)
        stop_loss = self._apply_stop_trigger(
            round_price_to_tick(params.stop_loss, precision.tick_size),
            precision,
        )
        tp1_price = round_price_to_tick(params.take_profit_1, precision.tick_size)
        tp2_price = round_price_to_tick(params.take_profit_2, precision.tick_size)

        balance = self._get_available_balance()
        position_value = calculate_position_size(
            balance,
            self.positioning_config.position_fraction,
            self.positioning_config.position_min_usdt,
        )
        if entry_price <= 0:
            raise ValueError("Цена входа должна быть положительной")
        raw_quantity = position_value / entry_price
        quantity = round_quantity_to_step(raw_quantity, precision.step_size)
        effective_min_qty = max(precision.min_qty, 2.0 * precision.step_size)
        quantity = apply_min_qty(quantity, effective_min_qty, precision.step_size)
        quantity = apply_min_notional(
            quantity,
            entry_price,
            precision.min_notional,
            precision.step_size,
        )
        quantity = apply_min_qty(quantity, effective_min_qty, precision.step_size)
        if quantity <= 0:
            raise ValueError("Размер позиции должен быть положительным после округления")

        return OrderParams(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1_price,
            take_profit_2=tp2_price,
            position_size=quantity,
        )

    def place_bracket_orders(self, symbol: str, params: OrderParams) -> Dict[OrderRole, ActiveOrder]:
        """Create entry, stop-loss and take-profit orders for a breakout setup."""

        precision = self._get_precision(symbol)
        tp1_quantity, tp2_quantity = self._split_take_profit_quantities(
            params.position_size,
            precision.step_size,
        )

        created_orders: list[tuple[OrderRole, str]] = []
        try:
            entry_id = self._create_limit_order(
                symbol=symbol,
                side="buy",
                quantity=params.position_size,
                price=params.entry_price,
                reduce_only=False,
            )
            created_orders.append((OrderRole.ENTRY, entry_id))

            stop_id = self._create_stop_order(
                symbol=symbol,
                quantity=params.position_size,
                price=params.stop_loss,
            )
            created_orders.append((OrderRole.STOP_LOSS, stop_id))

            tp1_id = self._create_limit_order(
                symbol=symbol,
                side="sell",
                quantity=tp1_quantity,
                price=params.take_profit_1,
                reduce_only=True,
            )
            created_orders.append((OrderRole.TAKE_PROFIT_1, tp1_id))

            tp2_id = self._create_limit_order(
                symbol=symbol,
                side="sell",
                quantity=tp2_quantity,
                price=params.take_profit_2,
                reduce_only=True,
            )
            created_orders.append((OrderRole.TAKE_PROFIT_2, tp2_id))
        except Exception:
            self.logger.error("Ошибка размещения ордеров по %s", symbol, exc_info=True)
            self.cancel_orders(symbol, [order_id for _, order_id in created_orders])
            raise

        self.logger.info(
            "Размещены ордера %s: entry=%s, stop=%s, tp1=%s, tp2=%s, qty=%s",
            symbol,
            entry_id,
            stop_id,
            tp1_id,
            tp2_id,
            params.position_size,
        )

        return {
            OrderRole.ENTRY: ActiveOrder(order_id=entry_id, quantity=params.position_size),
            OrderRole.STOP_LOSS: ActiveOrder(order_id=stop_id, quantity=params.position_size),
            OrderRole.TAKE_PROFIT_1: ActiveOrder(order_id=tp1_id, quantity=tp1_quantity),
            OrderRole.TAKE_PROFIT_2: ActiveOrder(order_id=tp2_id, quantity=tp2_quantity),
        }

    def replace_stop_loss(
        self,
        symbol: str,
        old_order_id: str,
        *,
        quantity: float,
        new_stop_price: float,
    ) -> str:
        """Cancel the existing stop-loss order and place a new one."""

        precision = self._get_precision(symbol)
        adjusted_price = self._apply_stop_trigger(
            round_price_to_tick(new_stop_price, precision.tick_size),
            precision,
        )
        self.cancel_orders(symbol, [old_order_id])
        new_order_id = self._create_stop_order(symbol, quantity=quantity, price=adjusted_price)
        self.logger.info(
            "Стоп-лосс для %s перенесён: старый=%s, новый=%s по цене %.8f",
            symbol,
            old_order_id,
            new_order_id,
            adjusted_price,
        )
        return new_order_id

    def cancel_orders(self, symbol: str, order_ids: Iterable[str]) -> None:
        """Cancel orders for ``symbol`` ignoring failures."""

        for order_id in order_ids:
            if not order_id:
                continue
            try:
                self.exchange.cancel_order(order_id, symbol)
            except ccxt.BaseError as exc:  # pragma: no cover - network failures
                self.logger.warning("Не удалось отменить ордер %s по %s: %s", order_id, symbol, exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    def _ensure_markets_loaded(self) -> None:
        try:
            self.exchange.load_markets()
        except ccxt.BaseError as exc:  # pragma: no cover - network failures
            self.logger.error("Не удалось загрузить рынки: %s", exc)
            raise

    def _get_available_balance(self) -> float:
        balance = self.exchange.fetch_balance()
        free = balance.get("free") if isinstance(balance, Mapping) else None
        if isinstance(free, Mapping) and self.quote_currency in free:
            return float(free[self.quote_currency])
        value = balance.get(self.quote_currency) if isinstance(balance, Mapping) else None
        return float(value or 0.0)

    def _get_market(self, symbol: str) -> Mapping[str, Any]:
        return self.exchange.market(symbol)

    def _get_precision(self, symbol: str) -> MarketPrecision:
        if symbol not in self._precision_cache:
            market = self._get_market(symbol)
            self._precision_cache[symbol] = self._extract_precision(market)
        return self._precision_cache[symbol]

    def _extract_precision(self, market: Mapping[str, Any]) -> MarketPrecision:
        info = market.get("info", {}) if isinstance(market, Mapping) else {}
        tick_size = self._extract_tick_size(market, info)
        step_size, min_qty = self._extract_step_size_and_min_qty(market, info)
        min_notional = self._extract_min_notional(market, info)
        if tick_size <= 0 or step_size <= 0:
            raise ValueError("Биржа вернула некорректные параметры точности")
        if min_qty <= 0 or min_notional <= 0:
            raise ValueError("Биржа вернула некорректные лимиты объёма")
        return MarketPrecision(
            tick_size=tick_size,
            step_size=step_size,
            min_qty=min_qty,
            min_notional=min_notional,
        )

    def _extract_tick_size(self, market: Mapping[str, Any], info: Mapping[str, Any]) -> float:
        filters = info.get("filters") if isinstance(info, Mapping) else None
        if isinstance(filters, list):
            for item in filters:
                if isinstance(item, Mapping) and item.get("filterType") == "PRICE_FILTER":
                    value = item.get("tickSize")
                    if value is not None:
                        return float(value)
        precision = market.get("precision") if isinstance(market, Mapping) else None
        if isinstance(precision, Mapping):
            price_precision = precision.get("price")
            if price_precision is not None:
                decimals = int(price_precision)
                return float(Decimal(1) / (Decimal(10) ** decimals))
        limits = market.get("limits") if isinstance(market, Mapping) else None
        if isinstance(limits, Mapping):
            price_limits = limits.get("price")
            if isinstance(price_limits, Mapping):
                min_price = price_limits.get("min")
                if min_price is not None:
                    return float(min_price)
        raise ValueError("tickSize не найден в описании маркета")

    def _extract_step_size_and_min_qty(
        self,
        market: Mapping[str, Any],
        info: Mapping[str, Any],
    ) -> tuple[float, float]:
        filters = info.get("filters") if isinstance(info, Mapping) else None
        step_size = None
        min_qty = None
        if isinstance(filters, list):
            for item in filters:
                if not isinstance(item, Mapping):
                    continue
                if item.get("filterType") == "LOT_SIZE":
                    step_raw = item.get("stepSize")
                    min_raw = item.get("minQty")
                    if step_raw is not None:
                        step_size = float(step_raw)
                    if min_raw is not None:
                        min_qty = float(min_raw)
                    break
        limits = market.get("limits") if isinstance(market, Mapping) else None
        if (step_size is None or min_qty is None) and isinstance(limits, Mapping):
            amount_limits = limits.get("amount")
            if isinstance(amount_limits, Mapping):
                if step_size is None and amount_limits.get("step") is not None:
                    step_size = float(amount_limits["step"])
                if min_qty is None and amount_limits.get("min") is not None:
                    min_qty = float(amount_limits["min"])
        if step_size is None:
            precision = market.get("precision") if isinstance(market, Mapping) else None
            if isinstance(precision, Mapping):
                amount_precision = precision.get("amount")
                if amount_precision is not None:
                    decimals = int(amount_precision)
                    step_size = float(Decimal(1) / (Decimal(10) ** decimals))
        if step_size is None or step_size <= 0:
            raise ValueError("stepSize не найден в описании маркета")
        if min_qty is None or min_qty <= 0:
            min_qty = step_size
        return step_size, min_qty

    def _extract_min_notional(self, market: Mapping[str, Any], info: Mapping[str, Any]) -> float:
        filters = info.get("filters") if isinstance(info, Mapping) else None
        if isinstance(filters, list):
            for item in filters:
                if isinstance(item, Mapping) and item.get("filterType") == "MIN_NOTIONAL":
                    value = item.get("notional") or item.get("minNotional")
                    if value is not None:
                        return float(value)
        limits = market.get("limits") if isinstance(market, Mapping) else None
        if isinstance(limits, Mapping):
            cost_limits = limits.get("cost")
            if isinstance(cost_limits, Mapping):
                min_cost = cost_limits.get("min")
                if min_cost is not None:
                    return float(min_cost)
        raise ValueError("minNotional не найден в описании маркета")

    def _split_take_profit_quantities(self, total: float, step_size: float) -> tuple[float, float]:
        if total <= 0:
            raise ValueError("Размер позиции должен быть положительным")
        step_dec = Decimal(str(step_size))
        units = (Decimal(str(total)) / step_dec).to_integral_value(rounding=ROUND_DOWN)
        units_int = int(units)
        if units_int < 2:
            raise ValueError("Недостаточный размер позиции для двух тейк-профитов")
        tp1_units = units_int // 2
        tp2_units = units_int - tp1_units
        if tp1_units <= 0 or tp2_units <= 0:
            raise ValueError("Недостаточный размер позиции для двух тейк-профитов")
        tp1 = float(Decimal(tp1_units) * step_dec)
        tp2 = float(Decimal(tp2_units) * step_dec)
        return tp1, tp2

    def _create_limit_order(
        self,
        *,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        reduce_only: bool,
    ) -> str:
        params: Dict[str, Any] = {}
        if reduce_only:
            params["reduceOnly"] = True
        order = self.exchange.create_order(
            symbol,
            "limit",
            side,
            quantity,
            price,
            params,
        )
        return self._extract_order_id(order)

    def _create_stop_order(self, symbol: str, *, quantity: float, price: float) -> str:
        params: Dict[str, Any] = {"stopPrice": price, "reduceOnly": True}
        order = self.exchange.create_order(
            symbol,
            "stop_market",
            "sell",
            quantity,
            None,
            params,
        )
        return self._extract_order_id(order)

    def _extract_order_id(self, order: Mapping[str, Any]) -> str:
        order_id = order.get("id") if isinstance(order, Mapping) else None
        if not order_id:
            raise ValueError("Биржа вернула ответ без идентификатора ордера")
        return str(order_id)

    def _apply_stop_trigger(self, stop_price: float, precision: MarketPrecision) -> float:
        adjusted = stop_price + self.order_config.stop_trigger
        if adjusted <= 0:
            raise ValueError("Стоп-лосс должен быть положительным")
        return round_price_to_tick(adjusted, precision.tick_size)


__all__ = ["OrderManager", "MarketPrecision"]
