from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List
import datetime as dt


@dataclass
class RiskManager:
    """Basic risk management helper.

    Parameters
    ----------
    balance_fetcher:
        Callable that returns the current account balance.
    risk_per_trade_pct:
        Fraction of account balance risked per trade (default 1%).
    max_open_risk_pct:
        Maximum fraction of balance allowed to be at risk across open trades
        (default 10%).
    daily_drawdown_pct:
        Maximum daily drawdown as fraction of starting balance before
        trading is halted (default 5%).
    max_consecutive_losses:
        Number of consecutive losing trades before trading is halted
        (default 3).
    """

    balance_fetcher: Callable[[], float]
    risk_per_trade_pct: float = 0.01
    max_open_risk_pct: float = 0.10
    daily_drawdown_pct: float = 0.05
    max_consecutive_losses: int = 3

    open_positions: List[float] = field(default_factory=list)
    consecutive_losses: int = 0
    daily_start_balance: float | None = None
    trading_halted: bool = False
    last_reset_day: dt.date | None = None

    def _ensure_daily_reset(self) -> None:
        today = dt.date.today()
        if self.last_reset_day != today:
            self.daily_start_balance = self.balance_fetcher()
            self.consecutive_losses = 0
            self.trading_halted = False
            self.last_reset_day = today

    def risk_per_trade(self) -> float:
        """Return the monetary risk allowed per trade."""
        balance = self.balance_fetcher()
        return balance * self.risk_per_trade_pct

    def position_size(self, entry: float, stop: float, risk: float | None = None) -> float:
        """Calculate position size based on entry and stop price."""
        if risk is None:
            risk = self.risk_per_trade()
        return risk / abs(entry - stop)

    @property
    def open_risk(self) -> float:
        return sum(self.open_positions)

    def can_open_trade(self) -> bool:
        """Return ``True`` if new trades are allowed."""
        self._ensure_daily_reset()
        if self.trading_halted:
            return False
        balance = self.balance_fetcher()
        return self.open_risk < balance * self.max_open_risk_pct

    def open_trade(self, entry: float, stop: float) -> float:
        """Register a new trade and return its position size."""
        self._ensure_daily_reset()
        if not self.can_open_trade():
            raise ValueError("Risk limits breached; cannot open trade")
        risk = self.risk_per_trade()
        balance = self.balance_fetcher()
        if self.open_risk + risk > balance * self.max_open_risk_pct:
            raise ValueError("Open risk would exceed limit")
        size = self.position_size(entry, stop, risk=risk)
        self.open_positions.append(risk)
        return size

    def close_trade(self, pnl: float) -> None:
        """Close an existing trade and update risk metrics."""
        self._ensure_daily_reset()
        if self.open_positions:
            # Remove risk for the oldest open position
            self.open_positions.pop(0)
        balance = self.balance_fetcher()
        # Update consecutive losses
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        # Check drawdown and loss limits
        if self.daily_start_balance is None:
            self.daily_start_balance = balance
        drawdown = (self.daily_start_balance - balance) / self.daily_start_balance
        if (
            drawdown >= self.daily_drawdown_pct
            or self.consecutive_losses >= self.max_consecutive_losses
        ):
            self.trading_halted = True


