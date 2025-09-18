from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Optional

from ...domain.enums import BreakDirection, Exchange, Side, Timeframe, TradeStatus
from ...domain.models.entities import Candle, Signal, Thresholds, Trade


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
        thresholds_raw = raw["thresholds"]
        thresholds = Thresholds(**thresholds_raw)
        return Signal(
            id=raw["id"],
            candle=candle,
            side=Side(raw["side"]),
            direction=BreakDirection(raw["direction"]),
            score=float(raw["score"]),
            triggered_at=datetime.fromisoformat(raw["triggered_at"]),
            thresholds=thresholds,
            metadata=raw.get("metadata", {}),
        )

    def deserialize_trade(self, raw: Dict[str, Any]) -> Trade:
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
            metadata=raw.get("metadata", {}),
        )
        return trade
