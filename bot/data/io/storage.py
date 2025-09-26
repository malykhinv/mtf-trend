from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from ...domain.enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from ...domain.models.entities import (
    Candle,
    Signal,
    SignalMetric,
    ThresholdMetric,
    Thresholds,
    Trade,
)
from ...domain.models.metadata import (
    SignalMetadata,
    SignalMetadataPayload,
    ThresholdsMetadata,
    TradeMetadata,
    TradeMetadataPayload,
)
from ...domain.services.metrics_service import SelectionMetricsSnapshot
from .excel_rows import (
    JSONDict,
    JSONValue,
    SignalMetricPayload,
    SignalPayload,
    SignalRow,
    StateRow,
    ThresholdMetricPayload,
    ThresholdRow,
    TradePayload,
    TradeRow,
)
from .excel_writer import ExcelWriter


class Storage:
    def __init__(self, path: Path) -> None:
        self._writer = ExcelWriter(path)

    # ------------------------------------------------------------------
    # Signal API
    # ------------------------------------------------------------------
    def save_signal(self, signal: Signal) -> None:
        row = self._serialize_signal(signal)
        self._writer.write_signal(row)

    def load_signals(self) -> List[Signal]:
        rows = self._writer.read_signals()
        signals: List[Signal] = []
        for payload in rows:
            try:
                signals.append(self.deserialize_signal(payload))
            except Exception:
                continue
        return signals

    # ------------------------------------------------------------------
    # Trade API
    # ------------------------------------------------------------------
    def save_trade(self, trade: Trade) -> None:
        row = self._serialize_trade(trade)
        self._writer.write_trade(row)

    def load_trades(self) -> List[Trade]:
        rows = self._writer.read_trades()
        trades: List[Trade] = []
        for payload in rows:
            try:
                trades.append(self.deserialize_trade(payload))
            except Exception:
                continue
        return trades

    # ------------------------------------------------------------------
    # State API
    # ------------------------------------------------------------------
    def save_state(
        self,
        asset: str,
        deposit_amount: float,
        updated_at: Optional[datetime],
        used: float,
        *,
        key: Optional[str] = None,
    ) -> None:
        stored_row = StateRow(
            key=key or "default",
            asset=asset,
            deposit_amount=deposit_amount,
            deposit_updated_at=updated_at.isoformat() if updated_at else None,
            used_amount=used,
        )
        self._writer.write_state(stored_row)

    def load_state(self, key: Optional[str] = None) -> tuple[str, float, Optional[str], float]:
        key_value = key if key is not None else "default"
        row = self._writer.read_state(key_value)
        if row is None and key_value == "default":
            row = self._writer.read_state(None)
        if row is None:
            return ("USDT", 0.0, None, 0.0)
        asset = row.asset or "USDT"
        amount = row.deposit_amount
        used = row.used_amount
        updated_at = row.deposit_updated_at
        return (asset, amount, updated_at, used)

    @staticmethod
    def _to_bool(value: Any, default: bool = True) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes"}:
                return True
            if lowered in {"false", "0", "no"}:
                return False
        return default

    def _deserialize_threshold_metric(self, payload: ThresholdMetricPayload) -> ThresholdMetric:
        return ThresholdMetric(
            name=payload.name,
            min_value=payload.min_value,
            max_value=payload.max_value,
            min_abs_value=payload.min_abs_value,
        )

    def _deserialize_thresholds(self, payload: ThresholdRow) -> Thresholds:
        metrics = [self._deserialize_threshold_metric(metric) for metric in payload.metrics]
        metadata = ThresholdsMetadata.from_mapping(payload.metadata)
        short_pct_move_ranges: List[tuple[float | None, float | None]] = list(
            payload.short_pct_move_ranges
        )
        short_relative_volume_ranges: List[tuple[float | None, float | None]] = list(
            payload.short_relative_volume_ranges
        )
        if not short_pct_move_ranges and metadata:
            for range_value in metadata.short_pct_move_ranges:
                if range_value.is_empty():
                    continue
                short_pct_move_ranges.append(
                    (range_value.minimum, range_value.maximum)
                )
        if not short_relative_volume_ranges and metadata:
            for range_value in metadata.short_relative_volume_ranges:
                if range_value.is_empty():
                    continue
                short_relative_volume_ranges.append(
                    (range_value.minimum, range_value.maximum)
                )

        def _float_or_default(value: float | None, default: float = 0.0) -> float:
            return float(value) if value is not None else default

        created_at = (
            datetime.fromisoformat(payload.created_at)
            if isinstance(payload.created_at, str)
            else None
        )
        updated_at = (
            datetime.fromisoformat(payload.updated_at)
            if isinstance(payload.updated_at, str)
            else None
        )

        return Thresholds(
            id=payload.id,
            min_relative_volume=_float_or_default(payload.min_relative_volume),
            max_relative_volume=_float_or_default(payload.max_relative_volume),
            min_atr_mult=_float_or_default(payload.min_atr_mult),
            min_pct_move=_float_or_default(payload.min_pct_move),
            max_pct_move=_float_or_default(payload.max_pct_move),
            max_upper_wick_pct=_float_or_default(payload.max_upper_wick_pct),
            max_lower_wick_pct=_float_or_default(payload.max_lower_wick_pct),
            short_pct_move_ranges=short_pct_move_ranges,
            short_relative_volume_ranges=short_relative_volume_ranges,
            allow_long=self._to_bool(payload.allow_long, True),
            allow_short=self._to_bool(payload.allow_short, True),
            metrics=metrics,
            metadata=metadata,
            created_at=created_at,
            updated_at=updated_at,
        )

    def _deserialize_signal_metric(self, payload: SignalMetricPayload) -> SignalMetric:
        threshold = (
            self._deserialize_threshold_metric(payload.threshold)
            if payload.threshold is not None
            else None
        )
        return SignalMetric(
            name=payload.name,
            value=payload.value,
            passed=payload.passed,
            threshold=threshold,
        )

    def deserialize_signal(self, payload: SignalPayload) -> Signal:
        candle_payload = payload.candle
        candle_id = candle_payload.id
        if candle_id is None:
            try:
                candle_timestamp = datetime.fromisoformat(candle_payload.started_at)
            except ValueError:
                candle_timestamp = None
            if candle_timestamp is not None:
                timeframe_value = candle_payload.timeframe or payload.timeframe or ""
                candle_id = (
                    f"{candle_payload.exchange}:{candle_payload.symbol}:{timeframe_value}:"
                    f"{int(candle_timestamp.timestamp())}"
                )

        candle_timeframe_str = candle_payload.timeframe or payload.timeframe
        if candle_timeframe_str is None:
            raise ValueError("Signal payload missing candle timeframe")
        candle_timeframe = Timeframe(candle_timeframe_str)

        candle = Candle(
            symbol=candle_payload.symbol,
            exchange=Exchange(candle_payload.exchange),
            timeframe=candle_timeframe,
            open=candle_payload.open,
            high=candle_payload.high,
            low=candle_payload.low,
            close=candle_payload.close,
            volume=candle_payload.volume,
            started_at=datetime.fromisoformat(candle_payload.started_at),
            closed_at=datetime.fromisoformat(candle_payload.closed_at),
            id=candle_id,
            quote_volume=candle_payload.quote_volume,
        )
        thresholds = self._deserialize_thresholds(payload.thresholds)
        metrics = [self._deserialize_signal_metric(metric) for metric in payload.metrics]
        metadata_mapping = payload.metadata
        metrics_snapshot = self._deserialize_metrics_snapshot(payload.metrics_snapshot)
        if metrics_snapshot is None:
            legacy_snapshot = metadata_mapping.get("metrics")
            if isinstance(legacy_snapshot, Mapping):
                metrics_snapshot = self._deserialize_metrics_snapshot(legacy_snapshot)
        metadata_payload = SignalMetadataPayload.from_mapping(metadata_mapping)
        signal_metadata = SignalMetadata.from_mapping(metadata_payload)
        created_at_raw = payload.created_at
        updated_at_raw = payload.updated_at
        timeframe_raw = payload.timeframe
        timeframe = Timeframe(timeframe_raw) if isinstance(timeframe_raw, str) else candle.timeframe
        candle_ref = payload.candle_id or candle.id
        if candle_ref is None and isinstance(candle.started_at, datetime):
            candle_ref = (
                f"{candle.exchange.value}:{candle.symbol}:{candle.timeframe.value}:"
                f"{int(candle.started_at.timestamp())}"
            )
        return Signal(
            id=payload.id,
            candle_id=candle_ref,
            candle=candle,
            timeframe=timeframe,
            side=Side(payload.side),
            direction=BreakDirection(payload.direction),
            score=payload.score,
            triggered_at=datetime.fromisoformat(payload.triggered_at),
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            thresholds=thresholds,
            metrics=metrics,
            allow_long=self._to_bool(payload.allow_long, thresholds.allow_long),
            allow_short=self._to_bool(payload.allow_short, thresholds.allow_short),
            metrics_snapshot=metrics_snapshot,
            metadata=signal_metadata,
        )

    def deserialize_trade(self, payload: TradePayload) -> Trade:
        thresholds_snapshot = (
            self._deserialize_thresholds(payload.thresholds_snapshot)
            if payload.thresholds_snapshot is not None
            else None
        )
        metadata_payload = TradeMetadataPayload.from_mapping(payload.metadata)
        trade_metadata = TradeMetadata.from_mapping(metadata_payload)
        created_at_raw = payload.created_at
        updated_at_raw = payload.updated_at
        timeframe_raw = payload.timeframe
        timeframe = Timeframe(timeframe_raw) if isinstance(timeframe_raw, str) else None
        if timeframe is None and thresholds_snapshot and thresholds_snapshot.metadata:
            meta_tf = thresholds_snapshot.metadata.timeframe
            if meta_tf is not None:
                timeframe = meta_tf
            else:
                raw_tf = thresholds_snapshot.metadata.timeframe_raw
                if isinstance(raw_tf, str):
                    try:
                        timeframe = Timeframe(raw_tf)
                    except ValueError:
                        timeframe = None
        if timeframe is None and trade_metadata and trade_metadata.timeframe:
            try:
                timeframe = Timeframe(trade_metadata.timeframe)
            except ValueError:
                timeframe = None
        source_signal_id = payload.source_signal_id or payload.signal_id
        trade = Trade(
            id=payload.id,
            signal_id=payload.signal_id,
            source_signal_id=source_signal_id,
            exchange=Exchange(payload.exchange),
            symbol=payload.symbol,
            timeframe=timeframe,
            side=Side(payload.side),
            status=TradeStatus(payload.status),
            entry_price=payload.entry_price,
            size=payload.size,
            used_margin=payload.used_margin if payload.used_margin else payload.size,
            exit_price=payload.exit_price,
            tp_price=payload.tp_price,
            sl_price=payload.sl_price,
            tp_pct=payload.tp_pct,
            sl_pct=payload.sl_pct,
            opened_at=datetime.fromisoformat(payload.opened_at) if payload.opened_at else None,
            closed_at=datetime.fromisoformat(payload.closed_at) if payload.closed_at else None,
            pnl=payload.pnl,
            pnl_pct=payload.pnl_pct,
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            allow_long=self._to_bool(payload.allow_long),
            allow_short=self._to_bool(payload.allow_short),
            thresholds_snapshot=thresholds_snapshot,
            metadata=trade_metadata,
        )
        return trade

    # ------------------------------------------------------------------
    # Serialization helpers
    # ------------------------------------------------------------------
    def _serialize_signal(self, signal: Signal) -> SignalRow:
        return SignalRow.from_signal(signal)

    def _serialize_trade(self, trade: Trade) -> TradeRow:
        return TradeRow.from_trade(trade)

    def _deserialize_metrics_snapshot(
        self, raw: Mapping[str, Any] | JSONDict | None
    ) -> SelectionMetricsSnapshot | None:
        if raw is None:
            return None
        if not isinstance(raw, Mapping):
            return None
        try:
            return SelectionMetricsSnapshot.from_mapping(raw)
        except (KeyError, TypeError, ValueError):
            return None
