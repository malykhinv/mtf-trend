from __future__ import annotations

from decimal import Decimal, ROUND_DOWN
from typing import Optional


def _to_decimal(value: float) -> Decimal:
    return Decimal(str(value))


def round_price_to_tick(price: float, tick_size: float) -> float:
    if tick_size <= 0:
        raise ValueError("tick_size должен быть положительным")
    decimal_price = _to_decimal(price)
    decimal_tick = _to_decimal(tick_size)
    return float((decimal_price / decimal_tick).to_integral_value(rounding=ROUND_DOWN) * decimal_tick)


def round_quantity_to_step(quantity: float, step_size: float) -> float:
    if step_size <= 0:
        raise ValueError("step_size должен быть положительным")
    decimal_quantity = _to_decimal(quantity)
    decimal_step = _to_decimal(step_size)
    return float((decimal_quantity / decimal_step).to_integral_value(rounding=ROUND_DOWN) * decimal_step)


def apply_min_qty(quantity: float, min_qty: float, step_size: Optional[float] = None) -> float:
    if min_qty <= 0:
        raise ValueError("min_qty должен быть положительным")
    adjusted = quantity if quantity >= min_qty else min_qty
    if step_size is not None:
        adjusted = round_quantity_to_step(adjusted, step_size)
    return adjusted


def apply_min_notional(
    quantity: float,
    price: float,
    min_notional: float,
    step_size: Optional[float] = None,
) -> float:
    if min_notional <= 0:
        raise ValueError("min_notional должен быть положительным")
    notional = quantity * price
    if notional >= min_notional:
        return quantity if step_size is None else round_quantity_to_step(quantity, step_size)
    required_quantity = min_notional / price
    if step_size is not None:
        return round_quantity_to_step(required_quantity, step_size)
    return required_quantity


def calculate_position_size(balance: float, position_fraction: float, position_min_usdt: float) -> float:
    if position_fraction < 0:
        raise ValueError("position_fraction не может быть отрицательным")
    if position_min_usdt < 0:
        raise ValueError("position_min_usdt не может быть отрицательным")
    fractional = balance * position_fraction
    return max(fractional, position_min_usdt)


__all__ = [
    "apply_min_notional",
    "apply_min_qty",
    "calculate_position_size",
    "round_price_to_tick",
    "round_quantity_to_step",
]
