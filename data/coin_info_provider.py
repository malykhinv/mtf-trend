from __future__ import annotations

from typing import Iterable


def filter_by_market_cap(coins: dict[str, float], min_cap: int) -> list[str]:
    """Вернуть монеты с капитализацией выше `min_cap`."""
    return [symbol for symbol, cap in coins.items() if cap >= min_cap]

