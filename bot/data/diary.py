"""Workbook writers for signals and trades diaries."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Callable, Iterable, Optional, Protocol

from openpyxl import Workbook, load_workbook
from openpyxl.workbook.defined_name import DefinedName
from openpyxl.utils.cell import get_column_letter

from bot.domain.models.anomaly import Anomaly, AnomalyThresholdSnapshot
from bot.domain.models.bar import BarMetrics
from bot.domain.models.close_reason import CloseReason
from bot.domain.models.exchange import Exchange
from bot.domain.models.signal import Signal, ThresholdSnapshot
from bot.domain.models.signal_direction import SignalDirection
from bot.domain.models.timeframe import Timeframe
from bot.domain.models.trade import Trade
from bot.domain.models.trade_status import TradeStatus
from bot import config


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
    requested_qty: float
    executed_qty: float
    status: TradeStatus
    avg_fill_price: Optional[float]
    reason_close: Optional[CloseReason]
    sl_be_at: Optional[datetime]
    order_id: Optional[str]
    stop_order_id: Optional[str]
    take_order_id: Optional[str]
    close_order_id: Optional[str]


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

    _ANOMALY_THRESHOLD_LAYOUT = [
        ("Minimum anomaly growth (%)", "anomaly_min_growth_pct", config.ANOMALY_MIN_GROWTH_PCT),
        ("Minimum anomaly ATR multiple", "anomaly_min_atr_mult", config.ANOMALY_MIN_ATR_MULT),
        (
            "Minimum anomaly relative volume",
            "anomaly_min_relative_volume",
            config.ANOMALY_MIN_RELATIVE_VOLUME,
        ),
        (
            "Minimum anomaly volume spike",
            "anomaly_min_volume_spike",
            config.ANOMALY_MIN_VOLUME_SPIKE,
        ),
        ("Minimum relative volume", "min_relative_volume", config.MIN_REL_VOL),
        ("Maximum relative volume", "max_relative_volume", config.MAX_REL_VOL),
        ("Minimum ATR multiple", "min_atr_mult", config.MIN_ATR_MULT),
        ("Minimum percent move", "min_pct_move", config.MIN_PCT_MOVE),
        ("Maximum percent move", "max_pct_move", config.MAX_PCT_MOVE),
        ("Initial deposit (USDT)", "initial_deposit", 100.0),
        ("Position fraction", "position_fraction", 0.1),
        ("Maximum upper wick (%)", "max_upper_wick_pct", config.MAX_UPPER_WICK_PCT),
        ("Maximum lower wick (%)", "max_lower_wick_pct", config.MAX_LOWER_WICK_PCT),
        ("Minimum risk/reward", "min_rr", config.MIN_RR),
        ("Minimum order size (USDT)", "min_order_usdt", config.MIN_ORDER_USDT),
        (
            "Order fraction of deposit",
            "order_pct_of_deposit",
            config.ORDER_PCT_OF_DEPOSIT,
        ),
    ]

    # Mapping is used by formulas in the ``Anomalies`` sheet.
    _ANOMALY_THRESHOLD_CELL_MAP = {
        name: f"Thresholds!$B${row_index}"
        for row_index, (_, name, _) in enumerate(_ANOMALY_THRESHOLD_LAYOUT, start=2)
    }

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
        "requested_qty",
        "executed_qty",
        "status",
        "avg_fill_price",
        "reason_close",
        "sl_be_at",
        "order_id",
        "stop_order_id",
        "take_order_id",
        "close_order_id",
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
        "long_rr",
        "short_rr",
        "long_filters_pass",
        "short_filters_pass",
        "long_pnl_pct",
        "short_pnl_pct",
        "long_equity_pct",
        "short_equity_pct",
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
        workbook = load_workbook(self._anomalies_path)
        try:
            self._ensure_anomaly_threshold_sheet(workbook)
            workbook.save(self._anomalies_path)
        finally:
            workbook.close()

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

    @staticmethod
    def _append(
        path: Path, rows: Iterable[Row], mapper: Callable[[Row, int], list[object]]
    ) -> None:
        workbook = load_workbook(path)
        try:
            sheet = workbook.active
            for row in rows:
                next_row = sheet.max_row + 1
                sheet.append(mapper(row, next_row))
            workbook.save(path)
        finally:
            workbook.close()

    @staticmethod
    def _format_dt(value: Optional[datetime]) -> Optional[str]:
        if value is None:
            return None
        return value.isoformat()

    def _signal_values(self, row: SignalRow, row_index: int) -> list[object]:
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
            metrics.break_direction.name,
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

    def _trade_values(self, row: TradeRow, row_index: int) -> list[object]:
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
            row.requested_qty,
            row.executed_qty,
            row.status.value,
            row.avg_fill_price,
            row.reason_close.value if row.reason_close else None,
            self._format_dt(row.sl_be_at),
            row.order_id,
            row.stop_order_id,
            row.take_order_id,
            row.close_order_id,
        ]

    def _anomaly_values(self, row: AnomalyRow, row_index: int) -> list[object]:
        metrics = row.metrics
        thresholds = row.thresholds
        threshold_cells = self._ANOMALY_THRESHOLD_CELL_MAP
        long_rr_formula = (
            f"=IFERROR((G{row_index}-I{row_index})/(I{row_index}-H{row_index}), \"\")"
        )
        short_rr_formula = (
            f"=IFERROR((I{row_index}-H{row_index})/(G{row_index}-I{row_index}), \"\")"
        )
        long_filter_formula = (
            f"=AND("
            f"L{row_index}>={threshold_cells['min_relative_volume']},"
            f"L{row_index}<={threshold_cells['max_relative_volume']},"
            f"M{row_index}>{threshold_cells['min_atr_mult']},"
            f"K{row_index}>={threshold_cells['min_pct_move']},"
            f"K{row_index}<={threshold_cells['max_pct_move']},"
            f"N{row_index}<{threshold_cells['max_upper_wick_pct']},"
            f"P{row_index}<{threshold_cells['max_lower_wick_pct']},"
            f"X{row_index}>={threshold_cells['min_rr']}"
            f")"
        )
        short_filter_formula = (
            f"=AND("
            f"OR(L{row_index}<{threshold_cells['min_relative_volume']},"
            f"L{row_index}>{threshold_cells['max_relative_volume']}),"
            f"M{row_index}<{threshold_cells['min_atr_mult']},"
            f"OR(K{row_index}>{threshold_cells['max_pct_move']},"
            f"K{row_index}<{threshold_cells['min_pct_move']}),"
            f"N{row_index}<{threshold_cells['max_upper_wick_pct']},"
            f"P{row_index}<{threshold_cells['max_lower_wick_pct']},"
            f"Y{row_index}>={threshold_cells['min_rr']}"
            f")"
        )
        long_pnl_formula = (
            f"=IF($I{row_index}=0,\"\",SWITCH($S{row_index},"
            f"\"HIGH_FIRST\",(G{row_index}-I{row_index})/I{row_index}*100,"
            f"\"LOW_FIRST\",(H{row_index}-I{row_index})/I{row_index}*100,"
            f"\"BOTH\",(H{row_index}-I{row_index})/I{row_index}*100,"
            f"\"NONE\",0,0))"
        )
        short_pnl_formula = (
            f"=IF($I{row_index}=0,\"\",SWITCH($S{row_index},"
            f"\"LOW_FIRST\",(I{row_index}-H{row_index})/I{row_index}*100,"
            f"\"HIGH_FIRST\",(I{row_index}-G{row_index})/I{row_index}*100,"
            f"\"BOTH\",(I{row_index}-G{row_index})/I{row_index}*100,"
            f"\"NONE\",0,0))"
        )
        long_equity_formula = (
            f"=IF(ISNUMBER(AD{row_index-1}),"
            f"IF($Z{row_index},AD{row_index-1}*(1+{threshold_cells['position_fraction']}*AB{row_index}/100),AD{row_index-1}),"
            f"IF($Z{row_index},{threshold_cells['initial_deposit']}*(1+{threshold_cells['position_fraction']}*AB{row_index}/100),{threshold_cells['initial_deposit']}))"
        )
        short_equity_formula = (
            f"=IF(ISNUMBER(AE{row_index-1}),"
            f"IF($AA{row_index},AE{row_index-1}*(1+{threshold_cells['position_fraction']}*AC{row_index}/100),AE{row_index-1}),"
            f"IF($AA{row_index},{threshold_cells['initial_deposit']}*(1+{threshold_cells['position_fraction']}*AC{row_index}/100),{threshold_cells['initial_deposit']}))"
        )
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
            metrics.break_direction.name,
            thresholds.min_green_move_pct,
            thresholds.min_volume_spike,
            thresholds.min_relative_volume,
            thresholds.min_atr_mult,
            long_rr_formula,
            short_rr_formula,
            long_filter_formula,
            short_filter_formula,
            long_pnl_formula,
            short_pnl_formula,
            long_equity_formula,
            short_equity_formula,
        ]

    @staticmethod
    def _set_named_range(workbook: Workbook, *, name: str, sheet_title: str, column_letter: str, row: int) -> None:
        """Ensure a workbook defined name points at the requested cell."""

        attr_text = f"'{sheet_title}'!${column_letter}${row}"
        if name in workbook.defined_names:
            workbook.defined_names.delete(name)
        workbook.defined_names.append(DefinedName(name=name, attr_text=attr_text))

    def _ensure_anomaly_threshold_sheet(self, workbook: Workbook) -> None:
        """Create the ``Thresholds`` sheet with default values when missing."""

        if "Thresholds" in workbook.sheetnames:
            sheet = workbook["Thresholds"]
        else:
            sheet = workbook.create_sheet("Thresholds")

        # Header row for readability.
        if sheet["A1"].value is None:
            sheet["A1"].value = "Parameter"
        if sheet["B1"].value is None:
            sheet["B1"].value = "Value"

        for row_index, (label, name, default_value) in enumerate(
            self._ANOMALY_THRESHOLD_LAYOUT, start=2
        ):
            label_cell = sheet.cell(row=row_index, column=1)
            value_cell = sheet.cell(row=row_index, column=2)

            if label_cell.value is None:
                label_cell.value = label
            if value_cell.value is None:
                value_cell.value = default_value

            self._set_named_range(
                workbook,
                name=name,
                sheet_title=sheet.title,
                column_letter=value_cell.column_letter,
                row=value_cell.row,
            )

        summary_row_start = len(self._ANOMALY_THRESHOLD_LAYOUT) + 2
        long_equity_column_letter = get_column_letter(
            self._ANOMALIES_HEADERS.index("long_equity_pct") + 1
        )
        short_equity_column_letter = get_column_letter(
            self._ANOMALIES_HEADERS.index("short_equity_pct") + 1
        )

        # The summary formulas below depend on the columns used in the ``Anomalies``
        # sheet. We specifically read ``long_equity_pct`` and ``short_equity_pct``
        # columns, so changes to ``_ANOMALIES_HEADERS`` that move these fields must
        # also update the calculated column letters above.
        for offset, (label, column_letter) in enumerate(
            (
                ("Итог лонг (сложный процент)", long_equity_column_letter),
                ("Итог шорт (сложный процент)", short_equity_column_letter),
            ),
            start=0,
        ):
            row_index = summary_row_start + offset
            label_cell = sheet.cell(row=row_index, column=1)
            value_cell = sheet.cell(row=row_index, column=2)

            if label_cell.value is None:
                label_cell.value = label
            if value_cell.value is None:
                value_cell.value = (
                    f"=IFERROR(LOOKUP(2,1/(Anomalies!${column_letter}:${column_letter}<>""),"
                    f"Anomalies!${column_letter}:${column_letter}),Thresholds!$B$11)"
                )


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
            requested_qty=trade.requested_qty,
            executed_qty=trade.executed_qty,
            status=trade.status,
            avg_fill_price=trade.avg_fill_price,
            reason_close=trade.reason_close,
            sl_be_at=trade.sl_be_at,
            order_id=trade.order_id,
            stop_order_id=trade.stop_order_id,
            take_order_id=trade.take_order_id,
            close_order_id=trade.close_order_id,
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
