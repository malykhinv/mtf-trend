"""Basic risk management helpers.

This module tracks position sizes, accumulated losses, and consecutive
losing trades.  Trading can be paused when risk limits are violated.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict


@dataclass
class RiskLimits:
    """Configuration for risk checks."""

    max_position_size: float = float("inf")
    max_daily_loss: float = float("inf")
    max_consecutive_losses: int = float("inf")


@dataclass
class RiskState:
    """Runtime risk state tracked across trades."""

    current_position: float = 0.0
    daily_loss: float = 0.0
    consecutive_losses: int = 0
    paused: bool = False


_limits = RiskLimits()
_state = RiskState()


def configure(config: Dict[str, float]) -> None:
    """Configure risk limits from a dictionary."""

    global _limits
    _limits = RiskLimits(
        max_position_size=config.get("max_position_size", float("inf")),
        max_daily_loss=config.get("max_daily_loss", float("inf")),
        max_consecutive_losses=config.get("max_consecutive_losses", float("inf")),
    )


def can_open_position(quantity: float) -> bool:
    """Return ``True`` if a new position of ``quantity`` is allowed."""

    if _state.paused:
        return False
    if _state.current_position + quantity > _limits.max_position_size:
        return False
    if _state.daily_loss >= _limits.max_daily_loss:
        return False
    return True


def update_position(delta: float) -> None:
    """Update the tracked position size by ``delta`` units."""

    _state.current_position = max(_state.current_position + delta, 0.0)


def record_pnl(pnl: float) -> None:
    """Record the profit or loss from a completed trade."""

    if pnl < 0:
        _state.daily_loss += abs(pnl)
        _state.consecutive_losses += 1
        if _state.consecutive_losses >= _limits.max_consecutive_losses:
            _state.paused = True
    else:
        _state.consecutive_losses = 0


def is_paused() -> bool:
    """Return ``True`` if trading is currently paused."""

    return _state.paused
