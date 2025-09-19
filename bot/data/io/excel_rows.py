from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional


def _require_str(value: Any, field: str) -> str:
    if value is None:
        raise ValueError(f"Missing required value for '{field}'")
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _optional_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _require_float(value: Any, field: str) -> float:
    if value is None:
        raise ValueError(f"Missing required value for '{field}'")
    return float(value)


def _optional_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    return float(value)


def _require_int(value: Any, field: str) -> int:
    if value is None:
        raise ValueError(f"Missing required value for '{field}'")
    return int(value)


def _ensure_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    if value is None:
        return default
    return bool(value)


@dataclass(frozen=True)
class StoredSignalRow:
    id: str
    candle_id: str | None
    symbol: str
    exchange: str
    timeframe: str | None
    candle_timeframe: str | None
    side: str
    direction: int
    score: float
    triggered_at: str
    created_at: str | None
    updated_at: str | None
    allow_long: bool
    allow_short: bool
    candle_open: float
    candle_high: float
    candle_low: float
    candle_close: float
    candle_volume: float
    candle_quote_volume: float | None
    candle_started_at: str
    candle_closed_at: str
    thresholds_json: str
    metrics_json: str
    metadata_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_str(self.id, "id"))
        object.__setattr__(self, "candle_id", _optional_str(self.candle_id))
        object.__setattr__(self, "symbol", _require_str(self.symbol, "symbol"))
        object.__setattr__(self, "exchange", _require_str(self.exchange, "exchange"))
        object.__setattr__(self, "timeframe", _optional_str(self.timeframe))
        object.__setattr__(self, "candle_timeframe", _optional_str(self.candle_timeframe))
        object.__setattr__(self, "side", _require_str(self.side, "side"))
        object.__setattr__(self, "direction", _require_int(self.direction, "direction"))
        object.__setattr__(self, "score", _require_float(self.score, "score"))
        object.__setattr__(self, "triggered_at", _require_str(self.triggered_at, "triggered_at"))
        object.__setattr__(self, "created_at", _optional_str(self.created_at))
        object.__setattr__(self, "updated_at", _optional_str(self.updated_at))
        object.__setattr__(self, "allow_long", _ensure_bool(self.allow_long, default=True))
        object.__setattr__(self, "allow_short", _ensure_bool(self.allow_short, default=True))
        object.__setattr__(self, "candle_open", _require_float(self.candle_open, "candle_open"))
        object.__setattr__(self, "candle_high", _require_float(self.candle_high, "candle_high"))
        object.__setattr__(self, "candle_low", _require_float(self.candle_low, "candle_low"))
        object.__setattr__(self, "candle_close", _require_float(self.candle_close, "candle_close"))
        object.__setattr__(self, "candle_volume", _require_float(self.candle_volume, "candle_volume"))
        object.__setattr__(self, "candle_quote_volume", _optional_float(self.candle_quote_volume))
        object.__setattr__(self, "candle_started_at", _require_str(self.candle_started_at, "candle_started_at"))
        object.__setattr__(self, "candle_closed_at", _require_str(self.candle_closed_at, "candle_closed_at"))
        object.__setattr__(self, "thresholds_json", _require_str(self.thresholds_json, "thresholds_json"))
        object.__setattr__(self, "metrics_json", _require_str(self.metrics_json, "metrics_json"))
        object.__setattr__(self, "metadata_json", _require_str(self.metadata_json, "metadata_json"))

    def to_excel_row(self) -> dict[str, object]:
        return {
            "id": self.id,
            "candle_id": self.candle_id,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "timeframe": self.timeframe,
            "candle_timeframe": self.candle_timeframe,
            "side": self.side,
            "direction": self.direction,
            "score": self.score,
            "triggered_at": self.triggered_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "allow_long": self.allow_long,
            "allow_short": self.allow_short,
            "candle_open": self.candle_open,
            "candle_high": self.candle_high,
            "candle_low": self.candle_low,
            "candle_close": self.candle_close,
            "candle_volume": self.candle_volume,
            "candle_quote_volume": self.candle_quote_volume,
            "candle_started_at": self.candle_started_at,
            "candle_closed_at": self.candle_closed_at,
            "thresholds_json": self.thresholds_json,
            "metrics_json": self.metrics_json,
            "metadata_json": self.metadata_json,
        }

    @classmethod
    def from_excel_row(cls, row: Mapping[str, object]) -> "StoredSignalRow":
        return cls(
            id=row.get("id"),
            candle_id=row.get("candle_id"),
            symbol=row.get("symbol"),
            exchange=row.get("exchange"),
            timeframe=row.get("timeframe"),
            candle_timeframe=row.get("candle_timeframe"),
            side=row.get("side"),
            direction=row.get("direction"),
            score=row.get("score"),
            triggered_at=row.get("triggered_at"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            allow_long=row.get("allow_long", True),
            allow_short=row.get("allow_short", True),
            candle_open=row.get("candle_open"),
            candle_high=row.get("candle_high"),
            candle_low=row.get("candle_low"),
            candle_close=row.get("candle_close"),
            candle_volume=row.get("candle_volume"),
            candle_quote_volume=row.get("candle_quote_volume"),
            candle_started_at=row.get("candle_started_at"),
            candle_closed_at=row.get("candle_closed_at"),
            thresholds_json=row.get("thresholds_json"),
            metrics_json=row.get("metrics_json"),
            metadata_json=row.get("metadata_json"),
        )


@dataclass(frozen=True)
class StoredTradeRow:
    id: str
    signal_id: str
    source_signal_id: str | None
    exchange: str
    symbol: str
    timeframe: str | None
    side: str
    status: str
    entry_price: float
    size: float
    used_margin: float
    exit_price: float | None
    tp_price: float | None
    sl_price: float | None
    tp_pct: float | None
    sl_pct: float | None
    opened_at: str | None
    closed_at: str | None
    pnl: float | None
    pnl_pct: float | None
    created_at: str | None
    updated_at: str | None
    allow_long: bool
    allow_short: bool
    thresholds_snapshot_json: str | None
    metadata_json: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _require_str(self.id, "id"))
        object.__setattr__(self, "signal_id", _require_str(self.signal_id, "signal_id"))
        object.__setattr__(self, "source_signal_id", _optional_str(self.source_signal_id))
        object.__setattr__(self, "exchange", _require_str(self.exchange, "exchange"))
        object.__setattr__(self, "symbol", _require_str(self.symbol, "symbol"))
        object.__setattr__(self, "timeframe", _optional_str(self.timeframe))
        object.__setattr__(self, "side", _require_str(self.side, "side"))
        object.__setattr__(self, "status", _require_str(self.status, "status"))
        object.__setattr__(self, "entry_price", _require_float(self.entry_price, "entry_price"))
        object.__setattr__(self, "size", _require_float(self.size, "size"))
        object.__setattr__(self, "used_margin", _require_float(self.used_margin, "used_margin"))
        object.__setattr__(self, "exit_price", _optional_float(self.exit_price))
        object.__setattr__(self, "tp_price", _optional_float(self.tp_price))
        object.__setattr__(self, "sl_price", _optional_float(self.sl_price))
        object.__setattr__(self, "tp_pct", _optional_float(self.tp_pct))
        object.__setattr__(self, "sl_pct", _optional_float(self.sl_pct))
        object.__setattr__(self, "opened_at", _optional_str(self.opened_at))
        object.__setattr__(self, "closed_at", _optional_str(self.closed_at))
        object.__setattr__(self, "pnl", _optional_float(self.pnl))
        object.__setattr__(self, "pnl_pct", _optional_float(self.pnl_pct))
        object.__setattr__(self, "created_at", _optional_str(self.created_at))
        object.__setattr__(self, "updated_at", _optional_str(self.updated_at))
        object.__setattr__(self, "allow_long", _ensure_bool(self.allow_long, default=True))
        object.__setattr__(self, "allow_short", _ensure_bool(self.allow_short, default=True))
        object.__setattr__(self, "thresholds_snapshot_json", _optional_str(self.thresholds_snapshot_json))
        object.__setattr__(self, "metadata_json", _require_str(self.metadata_json, "metadata_json"))

    def to_excel_row(self) -> dict[str, object]:
        return {
            "id": self.id,
            "signal_id": self.signal_id,
            "source_signal_id": self.source_signal_id,
            "exchange": self.exchange,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "side": self.side,
            "status": self.status,
            "entry_price": self.entry_price,
            "size": self.size,
            "used_margin": self.used_margin,
            "exit_price": self.exit_price,
            "tp_price": self.tp_price,
            "sl_price": self.sl_price,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "opened_at": self.opened_at,
            "closed_at": self.closed_at,
            "pnl": self.pnl,
            "pnl_pct": self.pnl_pct,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "allow_long": self.allow_long,
            "allow_short": self.allow_short,
            "thresholds_snapshot_json": self.thresholds_snapshot_json,
            "metadata_json": self.metadata_json,
        }

    @classmethod
    def from_excel_row(cls, row: Mapping[str, object]) -> "StoredTradeRow":
        return cls(
            id=row.get("id"),
            signal_id=row.get("signal_id"),
            source_signal_id=row.get("source_signal_id"),
            exchange=row.get("exchange"),
            symbol=row.get("symbol"),
            timeframe=row.get("timeframe"),
            side=row.get("side"),
            status=row.get("status"),
            entry_price=row.get("entry_price"),
            size=row.get("size"),
            used_margin=row.get("used_margin"),
            exit_price=row.get("exit_price"),
            tp_price=row.get("tp_price"),
            sl_price=row.get("sl_price"),
            tp_pct=row.get("tp_pct"),
            sl_pct=row.get("sl_pct"),
            opened_at=row.get("opened_at"),
            closed_at=row.get("closed_at"),
            pnl=row.get("pnl"),
            pnl_pct=row.get("pnl_pct"),
            created_at=row.get("created_at"),
            updated_at=row.get("updated_at"),
            allow_long=row.get("allow_long", True),
            allow_short=row.get("allow_short", True),
            thresholds_snapshot_json=row.get("thresholds_snapshot_json"),
            metadata_json=row.get("metadata_json"),
        )


@dataclass(frozen=True)
class StoredStateRow:
    key: str
    asset: str
    deposit_amount: float
    deposit_updated_at: str | None
    used_amount: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "key", _require_str(self.key, "key"))
        object.__setattr__(self, "asset", _require_str(self.asset, "asset"))
        object.__setattr__(self, "deposit_amount", _require_float(self.deposit_amount, "deposit_amount"))
        object.__setattr__(self, "deposit_updated_at", _optional_str(self.deposit_updated_at))
        object.__setattr__(self, "used_amount", _require_float(self.used_amount, "used_amount"))

    def to_excel_row(self) -> dict[str, object]:
        return {
            "key": self.key,
            "asset": self.asset,
            "deposit_amount": self.deposit_amount,
            "deposit_updated_at": self.deposit_updated_at,
            "used_amount": self.used_amount,
        }

    @classmethod
    def from_excel_row(cls, row: Mapping[str, object]) -> "StoredStateRow":
        return cls(
            key=row.get("key", "default"),
            asset=row.get("asset", "USDT"),
            deposit_amount=row.get("deposit_amount", 0.0),
            deposit_updated_at=row.get("deposit_updated_at"),
            used_amount=row.get("used_amount", 0.0),
        )
