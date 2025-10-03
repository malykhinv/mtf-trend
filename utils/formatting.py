from __future__ import annotations

import math
from decimal import Decimal, ROUND_HALF_UP


def format_number(value: float, decimals: int = 4) -> str:
    if decimals < 0:
        raise ValueError("decimals must be non-negative")
    if math.isnan(value) or math.isinf(value):
        raise ValueError("value must be a finite number")
    quantize_pattern: str = "1" if decimals == 0 else f"1.{'0' * decimals}"
    decimal_value: Decimal = Decimal(str(value)).quantize(
        Decimal(quantize_pattern), rounding=ROUND_HALF_UP
    )
    if decimal_value == 0:
        return "0"
    formatted: str = format(decimal_value, "f")
    if decimals > 0:
        formatted = formatted.rstrip("0").rstrip(".")
    return formatted


def format_money(value: float, currency: str = "USDT", decimals: int = 2) -> str:
    formatted_amount: str = format_number(value, decimals)
    return f"{formatted_amount} {currency}"


__all__ = ["format_number", "format_money"]

