from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional

from ...domain.enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from ...domain.models.entities import (
    Candle,
    Signal,
    SignalMetric,
    ThresholdMetric,
    Thresholds,
    Trade,
)


class Storage:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = RLock()
        if not self._path.exists():
            self._path.write_text("{}", encoding="utf-8")

    def _load(self) -> Dict[str, Any]:
        with self._lock:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        return data

    def _dump(self, data: Dict[str, Any]) -> None:
        with self._lock:
            self._path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    def read(self, key: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return data.get(key)

    def write(self, key: str, value: Dict[str, Any]) -> None:
        data = self._load()
        data[key] = value
        self._dump(data)

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
            open=float(candle_raw["open"]),
            high=float(candle_raw["high"]),
            low=float(candle_raw["low"]),
            close=float(candle_raw["close"]),
            volume=float(candle_raw["volume"]),
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
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
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
            score=float(raw["score"]),
            triggered_at=datetime.fromisoformat(raw["triggered_at"]),
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            thresholds=thresholds,
            metrics=metrics,
            allow_long=self._to_bool(raw.get("allow_long", thresholds.allow_long), thresholds.allow_long),
            allow_short=self._to_bool(raw.get("allow_short", thresholds.allow_short), thresholds.allow_short),
            metadata=metadata,
        )

    def deserialize_trade(self, raw: Dict[str, Any]) -> Trade:
        thresholds_snapshot = (
            self._deserialize_thresholds(raw["thresholds_snapshot"])
            if raw.get("thresholds_snapshot")
            else None
        )
        metadata = raw.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
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
            meta = raw.get("metadata")
            if isinstance(meta, dict):
                meta_tf = meta.get("timeframe")
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
            entry_price=float(raw["entry_price"]),
            size=float(raw.get("size", 0.0)),
            used_margin=float(
                raw.get("used_margin", raw.get("used_amount", raw.get("size", 0.0)))
            ),
            tp_price=float(raw["tp_price"]) if raw.get("tp_price") is not None else None,
            sl_price=float(raw["sl_price"]) if raw.get("sl_price") is not None else None,
            opened_at=datetime.fromisoformat(raw["opened_at"]) if raw.get("opened_at") else None,
            closed_at=datetime.fromisoformat(raw["closed_at"]) if raw.get("closed_at") else None,
            pnl=float(raw["pnl"]) if raw.get("pnl") is not None else None,
            pnl_pct=float(raw["pnl_pct"]) if raw.get("pnl_pct") is not None else None,
            created_at=datetime.fromisoformat(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=datetime.fromisoformat(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            allow_long=self._to_bool(raw.get("allow_long", True)),
            allow_short=self._to_bool(raw.get("allow_short", True)),
            thresholds_snapshot=thresholds_snapshot,
            metadata=metadata,
        )
        return trade
