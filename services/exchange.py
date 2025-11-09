from __future__ import annotations

from typing import Final


DEFAULT_MARGIN_MODE: Final[str] = "isolated"


def set_leverage(leverage: int, margin_mode: str) -> bool:
    """Set leverage on the exchange.

    This placeholder should be replaced with integration against the selected
    exchange. Returning ``True`` indicates that the leverage was accepted.
    """
    if leverage <= 0:
        raise ValueError("leverage должен быть положительным")
    if not margin_mode:
        raise ValueError("margin_mode не может быть пустым")
    return True


__all__ = ["DEFAULT_MARGIN_MODE", "set_leverage"]
