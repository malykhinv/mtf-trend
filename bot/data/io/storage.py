from __future__ import annotations

import json
from dataclasses import asdict
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
from ...domain.models.metadata import SignalMetadata, TradeMetadata
from ...domain.services.metrics_service import SelectionMetricsSnapshot
from .excel_rows import StoredSignalRow, StoredStateRow, StoredTradeRow
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
        for row in rows:
            raw = self._row_to_signal_payload(row)
            try:
                signals.append(self.deserialize_signal(raw))
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
        for row in rows:
            raw = self._row_to_trade_payload(row)
            try:
                trades.append(self.deserialize_trade(raw))
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

    def _deserialize_threshold_metric(self, raw: Dict[str, Any]) -> ThresholdMetric:
        return ThresholdMetric(
            name=raw["name"],
            min_value=float(raw["min_value"]) if raw.get("min_value") is not None else None,
            max_value=float(raw["max_value"]) if raw.get("max_value") is not None else None,
            min_abs_value=float(raw["min_abs_value"]) if raw.get("min_abs_value") is not None else None,
        )

    def _deserialize_thresholds(self, raw: Dict[str, Any]) -> Thresholds:
        metrics_raw = raw.get("metrics", [])
        metrics: List[ThresholdMetric] = []
        if isinstance(metrics_raw, list):
            for metric_raw in metrics_raw:
                if isinstance(metric_raw, dict) and "name" in metric_raw:
                    metrics.append(self._deserialize_threshold_metric(metric_raw))
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        created_at_raw = raw.get("created_at")
        updated_at_raw = raw.get("updated_at")
        short_pct_move_ranges: List[tuple[float, Optional[float]]] = []
        ranges_raw = raw.get("short_pct_move_ranges")
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
                    min_value = float(min_raw)
                    max_value = float(max_raw) if max_raw is not None else None
                elif isinstance(entry, (list, tuple)) and entry:
                    min_value = float(entry[0])
                    max_value = float(entry[1]) if len(entry) > 1 and entry[1] is not None else None
                else:
                    continue
                short_pct_move_ranges.append((min_value, max_value))
        def _get_float_from_keys(keys: list[str]) -> float:
            for key in keys:
                if raw.get(key) is not None:
                    return float(raw[key])
            return 0.0

        return Thresholds(
            id=str(raw["id"]) if raw.get("id") is not None else None,
            min_relative_volume=_get_float_from_keys(
                [
                    "min_relative_volume",
                    "minRelativeVolume",
                    "S",
                    "s",
                ]
            ),
            max_relative_volume=_get_float_from_keys(
                [
                    "max_relative_volume",
                    "maxRelativeVolume",
                    "T",
                    "t",
                ]
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
            allow_long=self._to_bool(raw.get("allow_long", True)),
            allow_short=self._to_bool(raw.get("allow_short", True)),
            metrics=metrics,
            metadata=metadata,
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
        )

    def _deserialize_signal_metric(self, raw: Dict[str, Any]) -> SignalMetric:
        threshold_raw = raw.get("threshold")
        threshold = (
            self._deserialize_threshold_metric(threshold_raw)
            if isinstance(threshold_raw, dict) and "name" in threshold_raw
            else None
        )
        return SignalMetric(
            name=raw["name"],
            value=float(raw["value"]),
            passed=bool(raw.get("passed", False)),
            threshold=threshold,
        )

    def deserialize_signal(self, raw: Dict[str, Any]) -> Signal:
        candle_raw = raw["candle"]
        candle_id = candle_raw.get("id")
        if candle_id is None:
            try:
                candle_timestamp = datetime.fromisoformat(candle_raw["started_at"])
            except (KeyError, ValueError):
                candle_timestamp = None
            if candle_timestamp is not None:
                candle_id = (
                    f"{candle_raw['exchange']}:{candle_raw['symbol']}:{candle_raw['timeframe']}:"
                    f"{int(candle_timestamp.timestamp())}"
                )
        candle = Candle(
            id=candle_id,
            symbol=candle_raw["symbol"],
            exchange=Exchange(candle_raw["exchange"]),
            timeframe=Timeframe(candle_raw["timeframe"]),
            open=candle_raw["open"],
            high=candle_raw["high"],
            low=candle_raw["low"],
            close=candle_raw["close"],
            volume=candle_raw["volume"],
            started_at=datetime.fromisoformat(candle_raw["started_at"]),
            closed_at=datetime.fromisoformat(candle_raw["closed_at"]),
        )
        thresholds = self._deserialize_thresholds(raw["thresholds"])
        metrics_raw = raw.get("metrics", [])
        metrics: List[SignalMetric] = []
        if isinstance(metrics_raw, list):
            for metric_raw in metrics_raw:
                if isinstance(metric_raw, dict) and "name" in metric_raw:
                    metrics.append(self._deserialize_signal_metric(metric_raw))
        metadata_raw = raw.get("metadata")
        metadata_dict = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        snapshot_raw = raw.get("metrics_snapshot")
        metrics_snapshot = self._deserialize_metrics_snapshot(snapshot_raw)
        if metrics_snapshot is None:
            legacy_snapshot = metadata_dict.get("metrics")
            if isinstance(legacy_snapshot, Mapping):
                metrics_snapshot = self._deserialize_metrics_snapshot(legacy_snapshot)
                if metrics_snapshot is not None:
                    metadata_dict.pop("metrics", None)
        created_at_raw = raw.get("created_at")
        updated_at_raw = raw.get("updated_at")
        timeframe_raw = raw.get("timeframe")
        timeframe = Timeframe(timeframe_raw) if isinstance(timeframe_raw, str) else candle.timeframe
        candle_ref = raw.get("candle_id") or candle.id
        if candle_ref is None and isinstance(candle.started_at, datetime):
            candle_ref = (
                f"{candle.exchange.value}:{candle.symbol}:{candle.timeframe.value}:"
                f"{int(candle.started_at.timestamp())}"
            )
        return Signal(
            id=raw["id"],
            candle_id=candle_ref,
            candle=candle,
            timeframe=timeframe,
            side=Side(raw["side"]),
            direction=BreakDirection(raw["direction"]),
            score=raw["score"],
            triggered_at=datetime.fromisoformat(raw["triggered_at"]),
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            thresholds=thresholds,
            metrics=metrics,
            allow_long=self._to_bool(raw.get("allow_long", thresholds.allow_long), thresholds.allow_long),
            allow_short=self._to_bool(raw.get("allow_short", thresholds.allow_short), thresholds.allow_short),
            metrics_snapshot=metrics_snapshot,
            metadata=SignalMetadata.from_mapping(metadata_dict),
        )

    def deserialize_trade(self, raw: Dict[str, Any]) -> Trade:
        thresholds_snapshot = (
            self._deserialize_thresholds(raw["thresholds_snapshot"])
            if raw.get("thresholds_snapshot")
            else None
        )
        metadata_raw = raw.get("metadata")
        metadata_dict = dict(metadata_raw) if isinstance(metadata_raw, Mapping) else {}
        created_at_raw = raw.get("created_at")
        updated_at_raw = raw.get("updated_at")
        timeframe_raw = raw.get("timeframe")
        timeframe = Timeframe(timeframe_raw) if isinstance(timeframe_raw, str) else None
        if timeframe is None and thresholds_snapshot and isinstance(thresholds_snapshot.metadata, dict):
            meta_tf = thresholds_snapshot.metadata.get("timeframe")
            if isinstance(meta_tf, str):
                try:
                    timeframe = Timeframe(meta_tf)
                except ValueError:
                    timeframe = None
        if timeframe is None:
            meta_tf = metadata_dict.get("timeframe")
            if isinstance(meta_tf, str):
                try:
                    timeframe = Timeframe(meta_tf)
                except ValueError:
                    timeframe = None
        source_signal_id = raw.get("source_signal_id") or raw.get("signal_id")
        trade = Trade(
            id=raw["id"],
            signal_id=raw["signal_id"],
            source_signal_id=source_signal_id,
            exchange=Exchange(raw["exchange"]),
            symbol=raw["symbol"],
            timeframe=timeframe,
            side=Side(raw["side"]),
            status=TradeStatus(raw["status"]),
            entry_price=raw["entry_price"],
            size=raw.get("size", 0.0),
            used_margin=raw.get("used_margin", raw.get("used_amount", raw.get("size", 0.0))),
            exit_price=raw.get("exit_price"),
            tp_price=raw.get("tp_price"),
            sl_price=raw.get("sl_price"),
            tp_pct=raw.get("tp_pct"),
            sl_pct=raw.get("sl_pct"),
            opened_at=datetime.fromisoformat(raw["opened_at"]) if raw.get("opened_at") else None,
            closed_at=datetime.fromisoformat(raw["closed_at"]) if raw.get("closed_at") else None,
            pnl=raw.get("pnl"),
            pnl_pct=raw.get("pnl_pct"),
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            allow_long=self._to_bool(raw.get("allow_long", True)),
            allow_short=self._to_bool(raw.get("allow_short", True)),
            thresholds_snapshot=thresholds_snapshot,
            metadata=TradeMetadata.from_mapping(metadata_dict),
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
        thresholds["metadata"] = dict(signal.thresholds.metadata or {})
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
            snapshot["metadata"] = dict(trade.thresholds_snapshot.metadata or {})
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

    def _row_to_signal_payload(self, row: StoredSignalRow) -> Dict[str, Any]:
        thresholds_json = row.thresholds_json
        metrics_json = row.metrics_json
        metadata_json = row.metadata_json
        snapshot_json = row.metrics_snapshot_json
        thresholds = self._safe_json_load(thresholds_json, {})
        metrics = self._safe_json_load(metrics_json, [])
        metadata = self._safe_json_load(metadata_json, {})
        snapshot = self._safe_json_load(snapshot_json, None)
        candle: Dict[str, Any] = {
            "id": row.candle_id,
            "symbol": row.symbol,
            "exchange": row.exchange,
            "timeframe": row.candle_timeframe or row.timeframe,
            "open": row.candle_open,
            "high": row.candle_high,
            "low": row.candle_low,
            "close": row.candle_close,
            "volume": row.candle_volume,
            "started_at": row.candle_started_at,
            "closed_at": row.candle_closed_at,
        }
        quote_volume = row.candle_quote_volume
        if quote_volume is not None:
            candle["quote_volume"] = quote_volume
        payload: Dict[str, Any] = {
            "id": row.id,
            "candle_id": row.candle_id,
            "candle": candle,
            "side": row.side,
            "direction": row.direction,
            "score": row.score,
            "triggered_at": row.triggered_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "timeframe": row.timeframe,
            "allow_long": row.allow_long,
            "allow_short": row.allow_short,
            "thresholds": thresholds,
            "metrics": metrics,
            "metrics_snapshot": snapshot,
            "metadata": metadata,
        }
        return payload

    def _deserialize_metrics_snapshot(
        self, raw: Any
    ) -> SelectionMetricsSnapshot | None:
        if not isinstance(raw, Mapping):
            return None
        try:
            return SelectionMetricsSnapshot.from_mapping(raw)
        except (KeyError, TypeError, ValueError):
            return None

    def _row_to_trade_payload(self, row: StoredTradeRow) -> Dict[str, Any]:
        thresholds_json = row.thresholds_snapshot_json
        metadata_json = row.metadata_json
        thresholds = self._safe_json_load(thresholds_json, None)
        metadata = self._safe_json_load(metadata_json, {})
        payload: Dict[str, Any] = {
            "id": row.id,
            "signal_id": row.signal_id,
            "source_signal_id": row.source_signal_id,
            "exchange": row.exchange,
            "symbol": row.symbol,
            "timeframe": row.timeframe,
            "side": row.side,
            "status": row.status,
            "entry_price": row.entry_price,
            "size": row.size,
            "used_margin": row.used_margin,
            "exit_price": row.exit_price,
            "tp_price": row.tp_price,
            "sl_price": row.sl_price,
            "tp_pct": row.tp_pct,
            "sl_pct": row.sl_pct,
            "opened_at": row.opened_at,
            "closed_at": row.closed_at,
            "pnl": row.pnl,
            "pnl_pct": row.pnl_pct,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
            "allow_long": row.allow_long,
            "allow_short": row.allow_short,
            "thresholds_snapshot": thresholds,
            "metadata": metadata,
        }
        return payload

    def _safe_json_load(self, raw: Any, default: Any) -> Any:
        if raw in (None, ""):
            return default
        if isinstance(raw, (dict, list)):
            return raw
        try:
            return json.loads(str(raw))
        except (TypeError, json.JSONDecodeError):
            return default
