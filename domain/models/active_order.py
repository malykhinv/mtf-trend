from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ActiveOrder:
    """Order tracked by the execution layer."""

    order_id: str
    quantity: float
    filled: float = 0.0

    def register_fill(self, filled: float) -> float:
        """Update filled quantity and return the delta executed since last update."""

        if filled < 0:
            raise ValueError("filled не может быть отрицательным")
        delta = max(filled - self.filled, 0.0)
        self.filled += delta
        return delta


__all__ = ["ActiveOrder"]
