"""Execution service responsible for translating signals into trades."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from bot import config
from .models.close_reason import CloseReason
from .models.signal import Signal
from .models.signal_direction import SignalDirection
from .models.trade import Trade
from .models.trade_status import TradeStatus


@dataclass(frozen=True)
class ExecutionSettings:
    min_order_usdt: float = config.MIN_ORDER_USDT
    order_pct_of_deposit: float = config.ORDER_PCT_OF_DEPOSIT
    breakeven_trigger_pct: float = config.BREAKEVEN_TRIGGER_PCT
    breakeven_offset_pct: float = config.BREAKEVEN_OFFSET_PCT


class ExecutionService:
    """Simple in-memory execution abstraction used by orchestrator and backtests."""

    def __init__(self) -> None:
        self._settings = ExecutionSettings()
        self._latest_imbalance: dict[str, float] = {}

    def calc_order_size_usdt(self, deposit_usdt: float) -> float:
        base_size = deposit_usdt * self._settings.order_pct_of_deposit
        return max(self._settings.min_order_usdt, base_size)

    def open_trade(
        self,
        signal: Signal,
        quantity: float,
        timestamp: Optional[datetime] = None,
    ) -> Trade:
        levels = signal.levels
        trade = Trade(
            trade_id=f"trade-{signal.signal_id}",
            source_signal_id=signal.signal_id,
            exchange=signal.exchange,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            side=signal.direction,
            timestamp_open=timestamp or signal.timestamp,
            entry_price=levels.entry_price,
            take_profit_price=levels.take_profit_price,
            stop_loss_price=levels.stop_loss_price,
            executed_qty=quantity,
            status=TradeStatus.OPENED,
        )
        return trade

    def close_trade(
        self,
        trade: Trade,
        reason: CloseReason,
        price: float,
        timestamp: datetime,
    ) -> Trade:
        return Trade(
            trade_id=trade.trade_id,
            source_signal_id=trade.source_signal_id,
            exchange=trade.exchange,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            side=trade.side,
            timestamp_open=trade.timestamp_open,
            entry_price=trade.entry_price,
            take_profit_price=trade.take_profit_price,
            stop_loss_price=trade.stop_loss_price,
            executed_qty=trade.executed_qty,
            status=self._map_reason_to_status(reason),
            timestamp_close=timestamp,
            avg_fill_price=price,
            reason_close=reason,
            sl_be_at=trade.sl_be_at,
        )

    def should_move_to_breakeven(self, entry_price: float, last_price: float, side: SignalDirection) -> bool:
        """Return True when price progress warrants moving the stop to break-even."""

        move_pct = self._settings.breakeven_trigger_pct / 100.0
        if side.is_long:
            return last_price >= entry_price * (1 + move_pct)
        return last_price <= entry_price * (1 - move_pct)

    def breakeven_stop(self, entry_price: float, side: SignalDirection) -> float:
        offset_pct = self._settings.breakeven_offset_pct / 100.0
        if side.is_long:
            return entry_price * (1 + offset_pct)
        return entry_price * (1 - offset_pct)

    def record_imbalance(self, symbol_key: str, imbalance: float) -> None:
        """Store the latest computed imbalance for a symbol."""

        self._latest_imbalance[symbol_key] = imbalance

    def last_recorded_imbalance(self, symbol_key: str) -> float | None:
        """Return last recorded imbalance if available."""

        return self._latest_imbalance.get(symbol_key)

    @staticmethod
    def _map_reason_to_status(reason: CloseReason) -> TradeStatus:
        if reason is CloseReason.TAKE_PROFIT:
            return TradeStatus.CLOSED_TP
        if reason is CloseReason.STOP_LOSS:
            return TradeStatus.CLOSED_SL
        if reason in (CloseReason.AGGRESSION, CloseReason.MANUAL):
            return TradeStatus.CLOSED_MANUAL
        return TradeStatus.CANCELLED
