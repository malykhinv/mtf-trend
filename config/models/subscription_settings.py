"""Subscription activation throttling settings."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SubscriptionSettings:
    """Limits controlling how aggressively the bot subscribes to symbols.

    Recommended values for monitoring several hundred instruments:
        * ``max_total`` around 80 keeps concurrent streams manageable.
        * ``max_new_per_cycle`` around 4 avoids bursts when recovering feeds.
    """

    max_total: int
    max_new_per_cycle: int


__all__ = ["SubscriptionSettings"]
