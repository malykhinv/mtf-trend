from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union, cast

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
    ThresholdsMetadataPayload,
    TradeMetadata,
    TradeMetadataPayload,
)
from ...domain.services.metrics_service import SelectionMetricsSnapshot
from .excel_rows import (
    JSONDict,
    JSONValue,
    SignalMetricPayload,
    SignalPayload,
    StoredSignalRow,
    StoredStateRow,
    StoredTradeRow,
    ThresholdMetricPayload,
    ThresholdPayload,
    TradePayload,
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
        stored_row = StoredStateRow(
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

    def _deserialize_thresholds(self, payload: ThresholdPayload) -> Thresholds:
        metrics = [self._deserialize_threshold_metric(metric) for metric in payload.metrics]
        metadata_payload = ThresholdsMetadataPayload.from_mapping(payload.metadata)
        metadata = ThresholdsMetadata.from_mapping(metadata_payload)
        created_at_raw = payload.created_at
        updated_at_raw = payload.updated_at

        short_pct_move_ranges: List[tuple[float | None, float | None]] = []
        short_relative_volume_ranges: List[tuple[float | None, float | None]] = []
        ranges_raw = payload.raw.get("short_pct_move_ranges")
        if isinstance(ranges_raw, list):
            for entry in ranges_raw:
                min_value: float | None
                max_value: float | None
                if isinstance(entry, dict):
                    min_raw = entry.get("min")
                    if min_raw is None:
                        min_raw = entry.get("min_value")
                    max_raw = entry.get("max")
                    if max_raw is None:
                        max_raw = entry.get("max_value")
                    if min_raw is None:
                        continue
                    try:
                        min_value = float(cast(Union[int, float, str], min_raw))
                    except (TypeError, ValueError):
                        continue
                    max_value = None
                    if max_raw is not None:
                        try:
                            max_value = float(cast(Union[int, float, str], max_raw))
                        except (TypeError, ValueError):
                            max_value = None
                elif isinstance(entry, (list, tuple)) and entry:
                    sequence_entry = cast(Sequence[JSONValue], entry)
                    try:
                        min_value = float(cast(Union[int, float, str], sequence_entry[0]))
                    except (TypeError, ValueError):
                        continue
                    max_value = None
                    if len(sequence_entry) > 1 and sequence_entry[1] is not None:
                        try:
                            max_value = float(
                                cast(Union[int, float, str], sequence_entry[1])
                            )
                        except (TypeError, ValueError):
                            max_value = None
                else:
                    continue
                short_pct_move_ranges.append((min_value, max_value))
        if not short_pct_move_ranges and metadata:
            for range_value in metadata.short_pct_move_ranges:
                if range_value.is_empty():
                    continue
                short_pct_move_ranges.append(
                    (range_value.minimum, range_value.maximum)
                )

        if metadata:
            for range_value in metadata.short_relative_volume_ranges:
                if range_value.is_empty():
                    continue
                short_relative_volume_ranges.append(
                    (range_value.minimum, range_value.maximum)
                )

        def _float_from_value(value: JSONValue | None) -> float:
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                try:
                    return float(value)
                except ValueError:
                    return 0.0
            return 0.0

        def _get_float_from_keys(keys: list[str]) -> float:
            value = payload.get_first(keys)
            if value is not None:
                return _float_from_value(value)
            return 0.0

        default_allow_long = payload.allow_long if isinstance(payload.allow_long, bool) else True
        default_allow_short = payload.allow_short if isinstance(payload.allow_short, bool) else True

        id_raw = payload.raw.get("id")
        return Thresholds(
            id=str(id_raw) if isinstance(id_raw, str) else None,
            min_relative_volume=_float_from_value(
                payload.raw.get("min_relative_volume")
                or payload.raw.get("minRelativeVolume")
                or payload.raw.get("S")
                or payload.raw.get("s")
                or 0
            ),
            max_relative_volume=_float_from_value(
                payload.raw.get("max_relative_volume")
                or payload.raw.get("maxRelativeVolume")
                or payload.raw.get("T")
                or payload.raw.get("t")
                or 0
            ),
            min_atr_mult=_get_float_from_keys(["min_atr_mult", "minAtrMult", "U", "u"]),
            min_pct_move=_get_float_from_keys(["min_pct_move", "minPctMove", "V", "v"]),
            max_pct_move=_get_float_from_keys(["max_pct_move", "maxPctMove", "W", "w"]),
            max_upper_wick_pct=_get_float_from_keys(
                ["max_upper_wick_pct", "maxUpperWickPct", "X", "x"]
            ),
            max_lower_wick_pct=_get_float_from_keys(
                ["max_lower_wick_pct", "maxLowerWickPct", "Y", "y"]
            ),
            short_pct_move_ranges=short_pct_move_ranges,
            short_relative_volume_ranges=short_relative_volume_ranges,
            allow_long=self._to_bool(
                payload.raw.get("allow_long", payload.allow_long), default_allow_long
            ),
            allow_short=self._to_bool(
                payload.raw.get("allow_short", payload.allow_short), default_allow_short
            ),
            metrics=metrics,
            metadata=metadata,
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
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
    def _serialize_signal(self, signal: Signal) -> StoredSignalRow:
        thresholds = asdict(signal.thresholds)
        if signal.thresholds.created_at:
            thresholds["created_at"] = signal.thresholds.created_at.isoformat()
        if signal.thresholds.updated_at:
            thresholds["updated_at"] = signal.thresholds.updated_at.isoformat()
        thresholds["metadata"] = (
            signal.thresholds.metadata.to_dict()
            if signal.thresholds.metadata
            else {}
        )
        metrics: List[Dict[str, Any]] = []
        for metric in signal.metrics:
            metric_dict = asdict(metric)
            threshold = metric_dict.get("threshold")
            if isinstance(threshold, dict):
                metric_dict["threshold"] = threshold
            metrics.append(metric_dict)
        metadata = json.dumps(self._signal_metadata_to_mapping(signal.metadata), sort_keys=True)
        snapshot_json = None
        if signal.metrics_snapshot is not None:
            snapshot_json = json.dumps(
                signal.metrics_snapshot.to_mapping(), sort_keys=True
            )
        candle = signal.candle
        return StoredSignalRow(
            id=signal.id,
            candle_id=signal.candle_id or candle.id,
            symbol=candle.symbol,
            exchange=candle.exchange.value,
            timeframe=signal.timeframe.value if signal.timeframe else None,
            candle_timeframe=candle.timeframe.value,
            side=signal.side.value,
            direction=signal.direction.value,
            score=signal.score,
            triggered_at=signal.triggered_at.isoformat(),
            created_at=signal.created_at.isoformat() if signal.created_at else None,
            updated_at=signal.updated_at.isoformat() if signal.updated_at else None,
            allow_long=bool(signal.allow_long),
            allow_short=bool(signal.allow_short),
            candle_open=candle.open,
            candle_high=candle.high,
            candle_low=candle.low,
            candle_close=candle.close,
            candle_volume=candle.volume,
            candle_quote_volume=candle.quote_volume,
            candle_started_at=candle.started_at.isoformat(),
            candle_closed_at=candle.closed_at.isoformat(),
            thresholds_json=json.dumps(thresholds, sort_keys=True),
            metrics_json=json.dumps(metrics, sort_keys=True),
            metrics_snapshot_json=snapshot_json,
            metadata_json=metadata,
        )

    def _serialize_trade(self, trade: Trade) -> StoredTradeRow:
        thresholds_snapshot = None
        if trade.thresholds_snapshot:
            snapshot = asdict(trade.thresholds_snapshot)
            if trade.thresholds_snapshot.created_at:
                snapshot["created_at"] = trade.thresholds_snapshot.created_at.isoformat()
            if trade.thresholds_snapshot.updated_at:
                snapshot["updated_at"] = trade.thresholds_snapshot.updated_at.isoformat()
            snapshot["metadata"] = (
                trade.thresholds_snapshot.metadata.to_dict()
                if trade.thresholds_snapshot.metadata
                else {}
            )
            thresholds_snapshot = json.dumps(snapshot, sort_keys=True)
        metadata_json = json.dumps(
            self._trade_metadata_to_mapping(trade.metadata), sort_keys=True
        )
        return StoredTradeRow(
            id=trade.id,
            signal_id=trade.signal_id,
            source_signal_id=trade.source_signal_id,
            exchange=trade.exchange.value,
            symbol=trade.symbol,
            timeframe=trade.timeframe.value if trade.timeframe else None,
            side=trade.side.value,
            status=trade.status.value,
            entry_price=trade.entry_price,
            size=trade.size,
            used_margin=trade.used_margin,
            exit_price=trade.exit_price,
            tp_price=trade.tp_price,
            sl_price=trade.sl_price,
            tp_pct=trade.tp_pct,
            sl_pct=trade.sl_pct,
            opened_at=trade.opened_at.isoformat() if trade.opened_at else None,
            closed_at=trade.closed_at.isoformat() if trade.closed_at else None,
            pnl=trade.pnl,
            pnl_pct=trade.pnl_pct,
            created_at=trade.created_at.isoformat() if trade.created_at else None,
            updated_at=trade.updated_at.isoformat() if trade.updated_at else None,
            allow_long=bool(trade.allow_long),
            allow_short=bool(trade.allow_short),
            thresholds_snapshot_json=thresholds_snapshot,
            metadata_json=metadata_json,
        )

    @staticmethod
    def _signal_metadata_to_mapping(metadata: SignalMetadata | None) -> Dict[str, Any]:
        if metadata is None:
            return {}
        return metadata.to_dict()

    @staticmethod
    def _trade_metadata_to_mapping(metadata: TradeMetadata | None) -> Dict[str, Any]:
        if metadata is None:
            return {}
        return metadata.to_dict()

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
