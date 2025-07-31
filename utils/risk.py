from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List
import datetime as dt
import json
import sqlite3
from pathlib import Path


@dataclass
class RiskManager:
    """Basic risk management helper with optional state persistence.

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
        (default 5).
    """

    balance_fetcher: Callable[[], float]
    risk_per_trade_pct: float = 0.01
    max_open_risk_pct: float = 0.10
    daily_drawdown_pct: float = 0.05
    max_consecutive_losses: int = 5
    db_path: str | Path = "risk_state.db"

    open_positions: List[float] = field(default_factory=list)
    consecutive_losses: int = 0
    daily_start_balance: float | None = None
    trading_halted: bool = False
    last_reset_day: dt.date | None = None

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self._init_db()
        self._load_state()

    # ------------------------------------------------------------------
    # database helpers
    def _init_db(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS state (
                    id INTEGER PRIMARY KEY,
                    open_positions TEXT,
                    consecutive_losses INTEGER,
                    daily_start_balance REAL,
                    trading_halted INTEGER,
                    last_reset_day TEXT
                )
                """
            )
            conn.commit()
        finally:
            conn.close()

    def _load_state(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.execute("SELECT open_positions, consecutive_losses, daily_start_balance, trading_halted, last_reset_day FROM state WHERE id=1")
            row = cur.fetchone()
            if row:
                positions, losses, start_balance, halted, last_day = row
                if positions:
                    self.open_positions = json.loads(positions)
                self.consecutive_losses = losses or 0
                self.daily_start_balance = start_balance
                self.trading_halted = bool(halted)
                self.last_reset_day = dt.date.fromisoformat(last_day) if last_day else None
            else:
                conn.execute(
                    "INSERT INTO state (id, open_positions, consecutive_losses, daily_start_balance, trading_halted, last_reset_day) VALUES (1, ?, ?, ?, ?, ?)",
                    (json.dumps(self.open_positions), self.consecutive_losses, self.daily_start_balance, int(self.trading_halted), self.last_reset_day.isoformat() if self.last_reset_day else None),
                )
                conn.commit()
        finally:
            conn.close()

    def _save_state(self) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute(
                "UPDATE state SET open_positions=?, consecutive_losses=?, daily_start_balance=?, trading_halted=?, last_reset_day=? WHERE id=1",
                (
                    json.dumps(self.open_positions),
                    self.consecutive_losses,
                    self.daily_start_balance,
                    int(self.trading_halted),
                    self.last_reset_day.isoformat() if self.last_reset_day else None,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    def _ensure_daily_reset(self) -> None:
        today = dt.date.today()
        if self.last_reset_day != today:
            self.daily_start_balance = self._get_balance()
            self.consecutive_losses = 0
            self.trading_halted = False
            self.last_reset_day = today
            self._save_state()

    def risk_per_trade(self) -> float:
        """Return the monetary risk allowed per trade."""
        balance = self._get_balance()
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
        balance = self._get_balance()
        return self.open_risk < balance * self.max_open_risk_pct

    def open_trade(self, entry: float, stop: float) -> float:
        """Register a new trade and return its position size."""
        self._ensure_daily_reset()
        if not self.can_open_trade():
            raise ValueError("Risk limits breached; cannot open trade")
        risk = self.risk_per_trade()
        balance = self._get_balance()
        if self.open_risk + risk > balance * self.max_open_risk_pct:
            raise ValueError("Open risk would exceed limit")
        size = self.position_size(entry, stop, risk=risk)
        self.open_positions.append(risk)
        self._save_state()
        return size

    def close_trade(self, pnl: float) -> None:
        """Close an existing trade and update risk metrics."""
        self._ensure_daily_reset()
        if self.open_positions:
            # Remove risk for the oldest open position
            self.open_positions.pop(0)
        balance = self._get_balance()
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
        self._save_state()

    def _get_balance(self) -> float:
        try:
            return self.balance_fetcher()
        except Exception:
            # Halt trading on network issues when balance cannot be fetched
            self.trading_halted = True
            self._save_state()
            return 0.0


