"""Small market-data helpers for anomaly live2."""

from __future__ import annotations

from math import isfinite


def optional_float(value: object) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not isfinite(parsed):
        return None
    return parsed


def optional_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def symbol_to_market_id(symbol: str) -> str:
    """Convert common CCXT futures symbols to Binance stream market ids."""

    normalized = symbol.strip().upper()
    if not normalized:
        return ""
    base_quote = normalized.split(":", 1)[0]
    return base_quote.replace("/", "").replace("-", "").replace("_", "")


def market_id_to_default_symbol(market_id: str) -> str:
    """Return a stable symbol for WS-only markets when no CCXT symbol was configured."""

    normalized = market_id.strip().upper()
    if normalized.endswith("USDT") and len(normalized) > 4:
        return f"{normalized[:-4]}/USDT:USDT"
    return normalized
