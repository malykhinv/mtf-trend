"""Workbook writers for signals and trades diaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Iterable, Optional, Protocol

from openpyxl import Workbook, load_workbook

from bot.domain.models.anomaly import Anomaly, AnomalyThresholdSnapshot
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


@dataclass(frozen=True, slots=True)
class AnomalyRow(Row):
    timestamp: datetime
    exchange: Exchange
    symbol: str
    timeframe: Timeframe
    bar_id: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    metrics: BarMetrics
    thresholds: AnomalyThresholdSnapshot


class DiaryBackend(Protocol):
    def append_signals(self, rows: Iterable[SignalRow]) -> None:  # pragma: no cover - interface definition
        ...

    def append_trades(self, rows: Iterable[TradeRow]) -> None:  # pragma: no cover - interface definition
        ...

    def append_anomalies(self, rows: Iterable[AnomalyRow]) -> None:  # pragma: no cover - interface definition
        ...


class WorkbookDiaryBackend(DiaryBackend):
    """Persist diary rows into Excel workbooks under a target directory."""

    _SIGNALS_HEADERS = [
        "signal_id",
        "timestamp",
        "exchange",
        "symbol",
        "timeframe",
        "direction",
        "entry_price",
        "take_profit_price",
        "stop_loss_price",
        "bar_id",
        "metrics_pct_move",
        "metrics_relative_volume",
        "metrics_atr_mult",
        "metrics_upper_wick_pct",
        "metrics_body_pct",
        "metrics_lower_wick_pct",
        "metrics_pct_to_low_break",
        "metrics_pct_to_high_break",
        "metrics_break_direction",
        "thresholds_min_green_move_pct",
        "thresholds_min_volume_spike",
        "thresholds_min_relative_volume",
        "thresholds_max_relative_volume",
        "thresholds_min_atr_mult",
        "thresholds_min_pct_move",
        "thresholds_max_pct_move",
        "thresholds_max_upper_wick_pct",
        "thresholds_max_lower_wick_pct",
    ]

    _TRADES_HEADERS = [
        "trade_id",
        "source_signal_id",
        "timestamp_open",
        "timestamp_close",
        "exchange",
        "symbol",
        "timeframe",
        "side",
        "entry_price",
        "take_profit_price",
        "stop_loss_price",
        "executed_qty",
        "status",
        "avg_fill_price",
        "reason_close",
        "sl_be_at",
    ]

    _ANOMALIES_HEADERS = [
        "timestamp",
        "exchange",
        "symbol",
        "timeframe",
        "bar_id",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "metrics_pct_move",
        "metrics_relative_volume",
        "metrics_atr_mult",
        "metrics_upper_wick_pct",
        "metrics_body_pct",
        "metrics_lower_wick_pct",
        "metrics_pct_to_low_break",
        "metrics_pct_to_high_break",
        "metrics_break_direction",
        "thresholds_min_green_move_pct",
        "thresholds_min_volume_spike",
        "thresholds_min_relative_volume",
        "thresholds_min_atr_mult",
    ]

    def __init__(self, base_path: Path) -> None:
        self._base_path = base_path
        self._base_path.mkdir(parents=True, exist_ok=True)

        self._signals_path = self._base_path / "signals.xlsx"
        self._trades_path = self._base_path / "trades.xlsx"
        self._anomalies_path = self._base_path / "anomalies.xlsx"

        self._ensure_workbook(self._signals_path, self._SIGNALS_HEADERS, sheet_name="Signals")
        self._ensure_workbook(self._trades_path, self._TRADES_HEADERS, sheet_name="Trades")
        self._ensure_workbook(self._anomalies_path, self._ANOMALIES_HEADERS, sheet_name="Anomalies")

    def append_signals(self, rows: Iterable[SignalRow]) -> None:
        materialized = list(rows)
        if not materialized:
            return
        self._append(self._signals_path, materialized, self._signal_values)

    def append_trades(self, rows: Iterable[TradeRow]) -> None:
        materialized = list(rows)
        if not materialized:
            return
        self._append(self._trades_path, materialized, self._trade_values)

    def append_anomalies(self, rows: Iterable[AnomalyRow]) -> None:
        materialized = list(rows)
        if not materialized:
            return
        self._append(self._anomalies_path, materialized, self._anomaly_values)

    @staticmethod
    def _ensure_workbook(path: Path, headers: list[str], *, sheet_name: str) -> None:
        if path.exists():
            workbook = load_workbook(path)
            try:
                sheet = workbook.active
                if WorkbookDiaryBackend._needs_header(sheet):
                    sheet.append(headers)
                    workbook.save(path)
            finally:
                workbook.close()
            return

        workbook = Workbook()
        try:
            sheet = workbook.active
            sheet.title = sheet_name
            sheet.append(headers)
            workbook.save(path)
        finally:
            workbook.close()

    @staticmethod
    def _needs_header(sheet) -> bool:  # type: ignore[no-any-unimported]
        """Return True if the first row has no values and headers should be added."""

        if sheet.max_row == 0:  # pragma: no cover - openpyxl always has at least 1 row
            return True
        first_row = sheet[1]
        return all(cell.value is None for cell in first_row)

    def _append(self, path: Path, rows: Iterable[Row], mapper) -> None:
        workbook = load_workbook(path)
        try:
            sheet = workbook.active
            for row in rows:
                sheet.append(mapper(row))
            workbook.save(path)
        finally:
            workbook.close()

    @staticmethod
    def _format_dt(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat()

    def _signal_values(self, row: SignalRow) -> list[object]:
        metrics = row.metrics
        thresholds = row.thresholds
        return [
            row.signal_id,
            self._format_dt(row.timestamp),
            row.exchange.value,
            row.symbol,
            row.timeframe.value,
            row.direction.value,
            row.entry_price,
            row.take_profit_price,
            row.stop_loss_price,
            row.bar_id,
            metrics.pct_move,
            metrics.relative_volume,
            metrics.atr_mult,
            metrics.upper_wick_pct,
            metrics.body_pct,
            metrics.lower_wick_pct,
            metrics.pct_to_low_break,
            metrics.pct_to_high_break,
            metrics.break_direction.value,
            thresholds.min_green_move_pct,
            thresholds.min_volume_spike,
            thresholds.min_relative_volume,
            thresholds.max_relative_volume,
            thresholds.min_atr_mult,
            thresholds.min_pct_move,
            thresholds.max_pct_move,
            thresholds.max_upper_wick_pct,
            thresholds.max_lower_wick_pct,
        ]

    def _trade_values(self, row: TradeRow) -> list[object]:
        return [
            row.trade_id,
            row.source_signal_id,
            self._format_dt(row.timestamp_open),
            self._format_dt(row.timestamp_close),
            row.exchange.value,
            row.symbol,
            row.timeframe.value,
            row.side.value,
            row.entry_price,
            row.take_profit_price,
            row.stop_loss_price,
            row.executed_qty,
            row.status.value,
            row.avg_fill_price,
            row.reason_close.value if row.reason_close else None,
            self._format_dt(row.sl_be_at),
        ]

    def _anomaly_values(self, row: AnomalyRow) -> list[object]:
        metrics = row.metrics
        thresholds = row.thresholds
        return [
            self._format_dt(row.timestamp),
            row.exchange.value,
            row.symbol,
            row.timeframe.value,
            row.bar_id,
            row.open,
            row.high,
            row.low,
            row.close,
            row.volume,
            metrics.pct_move,
            metrics.relative_volume,
            metrics.atr_mult,
            metrics.upper_wick_pct,
            metrics.body_pct,
            metrics.lower_wick_pct,
            metrics.pct_to_low_break,
            metrics.pct_to_high_break,
            metrics.break_direction.value,
            thresholds.min_green_move_pct,
            thresholds.min_volume_spike,
            thresholds.min_relative_volume,
            thresholds.min_atr_mult,
        ]


@dataclass(slots=True)
class WorkbookDiary:
    path: Path
    backend: Optional[DiaryBackend] = None
    _lock: Lock = field(default_factory=Lock)

    def __post_init__(self) -> None:
        if self.backend is None:
            self.backend = WorkbookDiaryBackend(self.path)

    def append_signals(self, signals: Iterable[Signal]) -> None:
        with self._lock:
            assert self.backend is not None
            rows = [self._signal_to_row(signal) for signal in signals]
            self.backend.append_signals(rows)

    def append_trades(self, trades: Iterable[Trade]) -> None:
        with self._lock:
            assert self.backend is not None
            rows = [self._trade_to_row(trade) for trade in trades]
            self.backend.append_trades(rows)

    def append_anomalies(self, anomalies: Iterable[Anomaly]) -> None:
        with self._lock:
            assert self.backend is not None
            rows = [self._anomaly_to_row(anomaly) for anomaly in anomalies]
            self.backend.append_anomalies(rows)

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

    @staticmethod
    def _anomaly_to_row(anomaly: Anomaly) -> AnomalyRow:
        return AnomalyRow(
            timestamp=anomaly.timestamp,
            exchange=anomaly.exchange,
            symbol=anomaly.symbol,
            timeframe=anomaly.timeframe,
            bar_id=anomaly.bar_id,
            open=anomaly.open,
            high=anomaly.high,
            low=anomaly.low,
            close=anomaly.close,
            volume=anomaly.volume,
            metrics=anomaly.metrics,
            thresholds=anomaly.thresholds,
        )
