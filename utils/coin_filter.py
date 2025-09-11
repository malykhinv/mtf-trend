"""Utility functions for selecting coins based on daily volume."""
from typing import Iterable, Callable, Dict, List

MAX_COINS = 180

def select_top_coins(
    coins: Iterable[Dict],
    filters: Iterable[Callable[[Dict], bool]] = (),
) -> List[Dict]:
    """Return the top coins sorted by 24h volume.

    Parameters
    ----------
    coins: iterable of mapping
        Each element must contain at least ``symbol`` and ``volume`` keys.
    filters: iterable of callables
        Existing filter functions applied sequentially to ``coins``.

    Returns
    -------
    list of dict
        Coins with ``symbol`` and ``volume`` fields sorted by descending
        volume and truncated to ``MAX_COINS`` entries.
    """

    # Apply provided filters
    filtered = [c for c in coins if all(f(c) for f in filters)]

    # Collect volume information
    coins_with_volume = [
        {"symbol": c["symbol"], "volume": c.get("volume", 0)}
        for c in filtered
        if c.get("volume") is not None
    ]

    # Sort by 24h volume and limit to MAX_COINS
    coins_with_volume.sort(key=lambda c: c["volume"], reverse=True)
    return coins_with_volume[:MAX_COINS]
