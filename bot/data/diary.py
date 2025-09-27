"""Workbook writers for signals and trades diaries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional, Protocol

from bot.domain.models.bar import BarMetrics
from bot.domain.models.close_reason import CloseReason
from bot.domain.models.exchange import Exchange
from bot.domain.models.signal import Signal, ThresholdSnapshot
from bot.domain.models.signal_direction import SignalDirection
from bot.domain.models.timeframe import Timeframe
from bot.domain.models.trade import Trade
from bot.domain.models.trade_status import TradeStatus


@dataclass(frozen=True, slots=True)
class Row:
    """Base diary row."""


@dataclass(frozen=True, slots=True)
class SignalRow(Row):
    signal_id: str
    timestamp: datetime
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    direction: SignalDirection
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    bar_id: str
    metrics: BarMetrics
    thresholds: ThresholdSnapshot


@dataclass(frozen=True, slots=True)
class TradeRow(Row):
    trade_id: str
    source_signal_id: str
    timestamp_open: datetime
    timestamp_close: Optional[datetime]
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    side: SignalDirection
    entry_price: float
    take_profit_price: float
    stop_loss_price: float
    executed_qty: float
    status: TradeStatus
    avg_fill_price: Optional[float]
    reason_close: Optional[CloseReason]
    sl_be_at: Optional[datetime]


class DiaryBackend(Protocol):
    def append_rows(self, rows: Iterable[Row]) -> None:  # pragma: no cover - interface definition
        # TODO: implement concrete persistence for diary rows.
        ...


@dataclass(slots=True)
class WorkbookDiary:
    path: Path
    backend: DiaryBackend
    _lock: Lock = Lock()

    def append_signals(self, signals: Iterable[Signal]) -> None:
        with self._lock:
            rows = [self._signal_to_row(signal) for signal in signals]
            self.backend.append_rows(rows)

    def append_trades(self, trades: Iterable[Trade]) -> None:
        with self._lock:
            rows = [self._trade_to_row(trade) for trade in trades]
            self.backend.append_rows(rows)

    @staticmethod
    def _signal_to_row(signal: Signal) -> SignalRow:
        return SignalRow(
            signal_id=signal.signal_id,
            timestamp=signal.timestamp,
            exchange=signal.exchange,
            symbol=signal.symbol,
            timeframe=signal.timeframe,
            direction=signal.direction,
            entry_price=signal.levels.entry_price,
            take_profit_price=signal.levels.take_profit_price,
            stop_loss_price=signal.levels.stop_loss_price,
            bar_id=signal.bar.bar_id,
            metrics=signal.bar.metrics,
            thresholds=signal.thresholds,
        )

    @staticmethod
    def _trade_to_row(trade: Trade) -> TradeRow:
        return TradeRow(
            trade_id=trade.trade_id,
            source_signal_id=trade.source_signal_id,
            timestamp_open=trade.timestamp_open,
            timestamp_close=trade.timestamp_close,
            exchange=trade.exchange,
            symbol=trade.symbol,
            timeframe=trade.timeframe,
            side=trade.side,
            entry_price=trade.entry_price,
            take_profit_price=trade.take_profit_price,
            stop_loss_price=trade.stop_loss_price,
            executed_qty=trade.executed_qty,
            status=trade.status,
            avg_fill_price=trade.avg_fill_price,
            reason_close=trade.reason_close,
            sl_be_at=trade.sl_be_at,
        )
