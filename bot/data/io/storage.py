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
        return Thresholds(
            s=float(raw.get("s", 0.0)),
            t=float(raw.get("t", 0.0)),
            u=float(raw.get("u", 0.0)),
            v=float(raw.get("v", 0.0)),
            w=float(raw.get("w", 0.0)),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            allow_long=self._to_bool(raw.get("allow_long", True)),
            allow_short=self._to_bool(raw.get("allow_short", True)),
            metrics=metrics,
            metadata=metadata,
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
        candle = Candle(
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
        return Signal(
            id=raw["id"],
            candle=candle,
            side=Side(raw["side"]),
            direction=BreakDirection(raw["direction"]),
            score=float(raw["score"]),
            triggered_at=datetime.fromisoformat(raw["triggered_at"]),
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
        trade = Trade(
            id=raw["id"],
            signal_id=raw["signal_id"],
            exchange=Exchange(raw["exchange"]),
            symbol=raw["symbol"],
            side=Side(raw["side"]),
            status=TradeStatus(raw["status"]),
            entry_price=float(raw["entry_price"]),
            size=float(raw["size"]),
            tp_price=float(raw["tp_price"]) if raw.get("tp_price") is not None else None,
            sl_price=float(raw["sl_price"]) if raw.get("sl_price") is not None else None,
            opened_at=datetime.fromisoformat(raw["opened_at"]) if raw.get("opened_at") else None,
            closed_at=datetime.fromisoformat(raw["closed_at"]) if raw.get("closed_at") else None,
            pnl=float(raw["pnl"]) if raw.get("pnl") is not None else None,
            allow_long=self._to_bool(raw.get("allow_long", True)),
            allow_short=self._to_bool(raw.get("allow_short", True)),
            thresholds_snapshot=thresholds_snapshot,
            metadata=metadata,
        )
        return trade
