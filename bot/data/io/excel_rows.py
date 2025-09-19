from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence, Union

JSONPrimitive = Union[str, int, float, bool, None]
JSONValue = Union[JSONPrimitive, "JSONList", "JSONDict"]
JSONList = list[JSONValue]
JSONDict = dict[str, JSONValue]
JSONMapping = Mapping[str, JSONValue]


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


def _ensure_json_value(value: Any) -> JSONValue:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(key): _ensure_json_value(val) for key, val in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_ensure_json_value(item) for item in value]
    return str(value)


def _load_json_value(raw: Any) -> JSONValue | None:
    if raw in (None, ""):
        return None
    if isinstance(raw, (dict, list)):
        return _ensure_json_value(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        return _ensure_json_value(parsed)
    return _ensure_json_value(raw)


def _load_json_dict(raw: Any) -> JSONDict:
    loaded = _load_json_value(raw)
    if isinstance(loaded, dict):
        return loaded
    return {}


def _load_json_list(raw: Any) -> JSONList:
    loaded = _load_json_value(raw)
    if isinstance(loaded, list):
        return loaded
    return []


def _load_json_dict_list(raw: Any) -> list[JSONDict]:
    result: list[JSONDict] = []
    for entry in _load_json_list(raw):
        if isinstance(entry, dict):
            result.append(entry)
    return result


def _ensure_json_dict(mapping: JSONMapping | None) -> JSONDict:
    if mapping is None:
        return {}
    return {str(key): _ensure_json_value(value) for key, value in mapping.items()}


@dataclass(frozen=True)
class ThresholdMetricPayload:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_mapping(cls, raw: JSONMapping) -> "ThresholdMetricPayload" | None:
        name_value = raw.get("name")
        if not isinstance(name_value, str):
            return None
        min_value = raw.get("min_value")
        max_value = raw.get("max_value")
        min_abs_value = raw.get("min_abs_value")
        return cls(
            name=name_value,
            min_value=float(min_value) if isinstance(min_value, (int, float)) else None,
            max_value=float(max_value) if isinstance(max_value, (int, float)) else None,
            min_abs_value=float(min_abs_value)
            if isinstance(min_abs_value, (int, float))
            else None,
        )


@dataclass(frozen=True)
class SignalMetricPayload:
    name: str
    value: float
    passed: bool
    threshold: ThresholdMetricPayload | None

    @classmethod
    def from_mapping(cls, raw: JSONMapping) -> "SignalMetricPayload" | None:
        name_value = raw.get("name")
        value_value = raw.get("value")
        if not isinstance(name_value, str):
            return None
        if not isinstance(value_value, (int, float)):
            return None
        passed_value = raw.get("passed", False)
        threshold_raw = raw.get("threshold")
        threshold: ThresholdMetricPayload | None = None
        if isinstance(threshold_raw, Mapping):
            threshold = ThresholdMetricPayload.from_mapping(threshold_raw)
        return cls(
            name=name_value,
            value=float(value_value),
            passed=bool(passed_value),
            threshold=threshold,
        )


@dataclass(frozen=True)
class ThresholdPayload:
    raw: JSONDict
    metadata: JSONDict
    metrics: list[ThresholdMetricPayload]
    created_at: str | None
    updated_at: str | None
    allow_long: bool | None
    allow_short: bool | None

    @classmethod
    def from_mapping(cls, raw: JSONMapping) -> "ThresholdPayload":
        metadata_raw = raw.get("metadata")
        metadata = _ensure_json_dict(metadata_raw if isinstance(metadata_raw, Mapping) else None)
        metrics_entries: list[ThresholdMetricPayload] = []
        metrics_raw = raw.get("metrics")
        if isinstance(metrics_raw, Sequence) and not isinstance(metrics_raw, (str, bytes, bytearray)):
            for entry in metrics_raw:
                if isinstance(entry, Mapping):
                    metric = ThresholdMetricPayload.from_mapping(entry)
                    if metric is not None:
                        metrics_entries.append(metric)
        created_at_raw = raw.get("created_at")
        updated_at_raw = raw.get("updated_at")
        allow_long_raw = raw.get("allow_long")
        allow_short_raw = raw.get("allow_short")
        return cls(
            raw=_ensure_json_dict(raw),
            metadata=metadata,
            metrics=metrics_entries,
            created_at=str(created_at_raw) if isinstance(created_at_raw, str) else None,
            updated_at=str(updated_at_raw) if isinstance(updated_at_raw, str) else None,
            allow_long=_ensure_bool(allow_long_raw) if allow_long_raw is not None else None,
            allow_short=_ensure_bool(allow_short_raw) if allow_short_raw is not None else None,
        )

    def get_first(self, keys: Iterable[str]) -> JSONValue | None:
        for key in keys:
            value = self.raw.get(key)
            if value is not None:
                return value
        return None


@dataclass(frozen=True)
class CandlePayload:
    id: str | None
    symbol: str
    exchange: str
    timeframe: str | None
    open: float
    high: float
    low: float
    close: float
    volume: float
    quote_volume: float | None
    started_at: str
    closed_at: str


@dataclass(frozen=True)
class SignalPayload:
    id: str
    candle_id: str | None
    candle: CandlePayload
    side: str
    direction: int
    score: float
    triggered_at: str
    created_at: str | None
    updated_at: str | None
    timeframe: str | None
    allow_long: bool
    allow_short: bool
    thresholds: ThresholdPayload
    metrics: list[SignalMetricPayload]
    metrics_snapshot: JSONDict | None
    metadata: JSONDict


@dataclass(frozen=True)
class TradePayload:
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
    thresholds_snapshot: ThresholdPayload | None
    metadata: JSONDict


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
    metrics_snapshot_json: str | None
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
        object.__setattr__(self, "metrics_snapshot_json", _optional_str(self.metrics_snapshot_json))
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
            "metrics_snapshot_json": self.metrics_snapshot_json,
            "metadata_json": self.metadata_json,
        }

    @classmethod
    def from_excel_row(cls, row: Mapping[str, object]) -> SignalPayload:
        identifier = _require_str(row.get("id"), "id")
        candle_id = _optional_str(row.get("candle_id"))
        symbol = _require_str(row.get("symbol"), "symbol")
        exchange = _require_str(row.get("exchange"), "exchange")
        timeframe = _optional_str(row.get("timeframe"))
        candle_timeframe = _optional_str(row.get("candle_timeframe"))
        side = _require_str(row.get("side"), "side")
        direction = _require_int(row.get("direction"), "direction")
        score = _require_float(row.get("score"), "score")
        triggered_at = _require_str(row.get("triggered_at"), "triggered_at")
        created_at = _optional_str(row.get("created_at"))
        updated_at = _optional_str(row.get("updated_at"))
        allow_long = _ensure_bool(row.get("allow_long", True), default=True)
        allow_short = _ensure_bool(row.get("allow_short", True), default=True)
        candle_open = _require_float(row.get("candle_open"), "candle_open")
        candle_high = _require_float(row.get("candle_high"), "candle_high")
        candle_low = _require_float(row.get("candle_low"), "candle_low")
        candle_close = _require_float(row.get("candle_close"), "candle_close")
        candle_volume = _require_float(row.get("candle_volume"), "candle_volume")
        candle_quote_volume = _optional_float(row.get("candle_quote_volume"))
        candle_started_at = _require_str(row.get("candle_started_at"), "candle_started_at")
        candle_closed_at = _require_str(row.get("candle_closed_at"), "candle_closed_at")
        thresholds_json = _require_str(row.get("thresholds_json"), "thresholds_json")
        metrics_json = _require_str(row.get("metrics_json"), "metrics_json")
        metrics_snapshot_json = _optional_str(row.get("metrics_snapshot_json"))
        metadata_json = _require_str(row.get("metadata_json"), "metadata_json")

        thresholds_mapping = _load_json_dict(thresholds_json)
        metrics_raw = _load_json_dict_list(metrics_json)
        metadata_mapping = _load_json_dict(metadata_json)
        snapshot_raw = _load_json_value(metrics_snapshot_json)
        metrics_snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else None
        thresholds = ThresholdPayload.from_mapping(thresholds_mapping)
        metrics: list[SignalMetricPayload] = []
        for entry in metrics_raw:
            metric = SignalMetricPayload.from_mapping(entry)
            if metric is not None:
                metrics.append(metric)
        candle = CandlePayload(
            id=candle_id,
            symbol=symbol,
            exchange=exchange,
            timeframe=candle_timeframe or timeframe,
            open=candle_open,
            high=candle_high,
            low=candle_low,
            close=candle_close,
            volume=candle_volume,
            quote_volume=candle_quote_volume,
            started_at=candle_started_at,
            closed_at=candle_closed_at,
        )
        return SignalPayload(
            id=identifier,
            candle_id=candle_id,
            candle=candle,
            side=side,
            direction=direction,
            score=score,
            triggered_at=triggered_at,
            created_at=created_at,
            updated_at=updated_at,
            timeframe=timeframe,
            allow_long=allow_long,
            allow_short=allow_short,
            thresholds=thresholds,
            metrics=metrics,
            metrics_snapshot=metrics_snapshot,
            metadata=metadata_mapping,
        )

    def to_payload(self) -> SignalPayload:
        thresholds_mapping = _load_json_dict(self.thresholds_json)
        metrics_raw = _load_json_dict_list(self.metrics_json)
        metadata_mapping = _load_json_dict(self.metadata_json)
        snapshot_raw = _load_json_value(self.metrics_snapshot_json)
        metrics_snapshot = snapshot_raw if isinstance(snapshot_raw, dict) else None
        thresholds = ThresholdPayload.from_mapping(thresholds_mapping)
        metrics: list[SignalMetricPayload] = []
        for entry in metrics_raw:
            metric = SignalMetricPayload.from_mapping(entry)
            if metric is not None:
                metrics.append(metric)
        candle = CandlePayload(
            id=self.candle_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.candle_timeframe or self.timeframe,
            open=self.candle_open,
            high=self.candle_high,
            low=self.candle_low,
            close=self.candle_close,
            volume=self.candle_volume,
            quote_volume=self.candle_quote_volume,
            started_at=self.candle_started_at,
            closed_at=self.candle_closed_at,
        )
        return SignalPayload(
            id=self.id,
            candle_id=self.candle_id,
            candle=candle,
            side=self.side,
            direction=self.direction,
            score=self.score,
            triggered_at=self.triggered_at,
            created_at=self.created_at,
            updated_at=self.updated_at,
            timeframe=self.timeframe,
            allow_long=self.allow_long,
            allow_short=self.allow_short,
            thresholds=thresholds,
            metrics=metrics,
            metrics_snapshot=metrics_snapshot,
            metadata=metadata_mapping,
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
    def from_excel_row(cls, row: Mapping[str, object]) -> TradePayload:
        identifier = _require_str(row.get("id"), "id")
        signal_id = _require_str(row.get("signal_id"), "signal_id")
        source_signal_id = _optional_str(row.get("source_signal_id"))
        exchange = _require_str(row.get("exchange"), "exchange")
        symbol = _require_str(row.get("symbol"), "symbol")
        timeframe = _optional_str(row.get("timeframe"))
        side = _require_str(row.get("side"), "side")
        status = _require_str(row.get("status"), "status")
        entry_price = _require_float(row.get("entry_price"), "entry_price")
        size = _require_float(row.get("size"), "size")
        used_margin_raw = (
            row.get("used_margin")
            if row.get("used_margin") is not None
            else row.get("used_amount", row.get("size"))
        )
        used_margin = _require_float(used_margin_raw, "used_margin")
        exit_price = _optional_float(row.get("exit_price"))
        tp_price = _optional_float(row.get("tp_price"))
        sl_price = _optional_float(row.get("sl_price"))
        tp_pct = _optional_float(row.get("tp_pct"))
        sl_pct = _optional_float(row.get("sl_pct"))
        opened_at = _optional_str(row.get("opened_at"))
        closed_at = _optional_str(row.get("closed_at"))
        pnl = _optional_float(row.get("pnl"))
        pnl_pct = _optional_float(row.get("pnl_pct"))
        created_at = _optional_str(row.get("created_at"))
        updated_at = _optional_str(row.get("updated_at"))
        allow_long = _ensure_bool(row.get("allow_long", True), default=True)
        allow_short = _ensure_bool(row.get("allow_short", True), default=True)
        thresholds_snapshot_json = _optional_str(row.get("thresholds_snapshot_json"))
        metadata_json = _require_str(row.get("metadata_json"), "metadata_json")

        snapshot_raw = _load_json_value(thresholds_snapshot_json)
        thresholds_snapshot = (
            ThresholdPayload.from_mapping(snapshot_raw)
            if isinstance(snapshot_raw, Mapping)
            else None
        )
        metadata_mapping = _load_json_dict(metadata_json)
        return TradePayload(
            id=identifier,
            signal_id=signal_id,
            source_signal_id=source_signal_id,
            exchange=exchange,
            symbol=symbol,
            timeframe=timeframe,
            side=side,
            status=status,
            entry_price=entry_price,
            size=size,
            used_margin=used_margin,
            exit_price=exit_price,
            tp_price=tp_price,
            sl_price=sl_price,
            tp_pct=tp_pct,
            sl_pct=sl_pct,
            opened_at=opened_at,
            closed_at=closed_at,
            pnl=pnl,
            pnl_pct=pnl_pct,
            created_at=created_at,
            updated_at=updated_at,
            allow_long=allow_long,
            allow_short=allow_short,
            thresholds_snapshot=thresholds_snapshot,
            metadata=metadata_mapping,
        )

    def to_payload(self) -> TradePayload:
        snapshot_raw = _load_json_value(self.thresholds_snapshot_json)
        thresholds_snapshot = (
            ThresholdPayload.from_mapping(snapshot_raw)
            if isinstance(snapshot_raw, Mapping)
            else None
        )
        metadata_mapping = _load_json_dict(self.metadata_json)
        return TradePayload(
            id=self.id,
            signal_id=self.signal_id,
            source_signal_id=self.source_signal_id,
            exchange=self.exchange,
            symbol=self.symbol,
            timeframe=self.timeframe,
            side=self.side,
            status=self.status,
            entry_price=self.entry_price,
            size=self.size,
            used_margin=self.used_margin,
            exit_price=self.exit_price,
            tp_price=self.tp_price,
            sl_price=self.sl_price,
            tp_pct=self.tp_pct,
            sl_pct=self.sl_pct,
            opened_at=self.opened_at,
            closed_at=self.closed_at,
            pnl=self.pnl,
            pnl_pct=self.pnl_pct,
            created_at=self.created_at,
            updated_at=self.updated_at,
            allow_long=self.allow_long,
            allow_short=self.allow_short,
            thresholds_snapshot=thresholds_snapshot,
            metadata=metadata_mapping,
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
        key_value = _optional_str(row.get("key")) or "default"
        asset_value = _optional_str(row.get("asset")) or "USDT"
        deposit_amount = _optional_float(row.get("deposit_amount")) or 0.0
        deposit_updated_at = _optional_str(row.get("deposit_updated_at"))
        used_amount = _optional_float(row.get("used_amount")) or 0.0
        return cls(
            key=key_value,
            asset=asset_value,
            deposit_amount=deposit_amount,
            deposit_updated_at=deposit_updated_at,
            used_amount=used_amount,
        )
