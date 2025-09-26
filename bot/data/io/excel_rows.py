from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar, Iterable, Mapping, Sequence


JSONPrimitive = str | int | float | bool | None
JSONValue = JSONPrimitive | list["JSONValue"] | dict[str, "JSONValue"]
JSONList = list[JSONValue]
JSONDict = dict[str, JSONValue]


class _RowParser:
    """Utility mixin providing coercion helpers using duck typing."""

    @staticmethod
    def _require_text(value: object, field: str) -> str:
        if value is None:
            raise ValueError(f"Missing required value for '{field}'")
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # type: ignore[call-arg]
            except Exception:
                pass
        return f"{value}"

    @staticmethod
    def _optional_text(value: object | None) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # type: ignore[call-arg]
            except Exception:
                return f"{value}"
        return f"{value}"

    @staticmethod
    def _require_float(value: object, field: str) -> float:
        if value is None:
            raise ValueError(f"Missing required value for '{field}'")
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(f"Unable to convert '{field}' to float") from exc

    @staticmethod
    def _optional_float(value: object | None) -> float | None:
        if value is None:
            return None
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - defensive
            return None

    @staticmethod
    def _optional_int(value: object | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)  # type: ignore[arg-type]
        except Exception:  # pragma: no cover - defensive
            return None

    @staticmethod
    def _require_int(value: object, field: str) -> int:
        converted = _RowParser._optional_int(value)
        if converted is None:
            raise ValueError(f"Missing required value for '{field}'")
        return converted

    @staticmethod
    def _optional_bool(value: object | None) -> bool | None:
        if value is None:
            return None
        text = f"{value}".strip().lower()
        if text in {"true", "1", "yes"}:
            return True
        if text in {"false", "0", "no"}:
            return False
        return bool(value)

    @staticmethod
    def _require_bool(value: object, field: str) -> bool:
        converted = _RowParser._optional_bool(value)
        if converted is None:
            raise ValueError(f"Missing required value for '{field}'")
        return converted

    @classmethod
    def _parse_json(cls, value: object | None) -> JSONValue | None:
        if value in (None, ""):
            return None
        if hasattr(value, "items"):
            try:
                items = value.items()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                return None
            return {
                cls._require_text(key, "json_key"): cls._normalize_json(val)
                for key, val in items
            }
        if hasattr(value, "strip"):
            text = f"{value}".strip()
            if not text:
                return None
            try:
                loaded = json.loads(text)
            except Exception:
                return None
            return cls._normalize_json(loaded)
        try:
            iterable = iter(value)  # type: ignore[arg-type]
        except Exception:
            return cls._normalize_json(value)
        return [cls._normalize_json(item) for item in iterable]

    @classmethod
    def _normalize_json(cls, value: object | None) -> JSONValue:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            try:
                return value.isoformat()  # type: ignore[call-arg]
            except Exception:
                return f"{value}"
        if hasattr(value, "items"):
            try:
                items = value.items()  # type: ignore[attr-defined]
            except Exception:  # pragma: no cover - defensive
                return f"{value}"
            return {
                cls._require_text(key, "json_key"): cls._normalize_json(val)
                for key, val in items
            }
        if hasattr(value, "strip"):
            return f"{value}"
        try:
            iterable = iter(value)  # type: ignore[arg-type]
        except Exception:
            pass
        else:
            return [cls._normalize_json(item) for item in iterable]
        try:
            return float(value)  # type: ignore[arg-type]
        except Exception:
            return f"{value}"


@dataclass(frozen=True)
class ThresholdMetricPayload(_RowParser):
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "ThresholdMetricPayload" | None:
        try:
            name_value = raw["name"]
        except KeyError:
            return None
        name = cls._require_text(name_value, "name")
        minimum = cls._optional_float(raw.get("min_value"))
        maximum = cls._optional_float(raw.get("max_value"))
        min_abs = cls._optional_float(raw.get("min_abs_value"))
        return cls(name=name, min_value=minimum, max_value=maximum, min_abs_value=min_abs)

    def to_mapping(self) -> JSONDict:
        data: JSONDict = {"name": self.name}
        if self.min_value is not None:
            data["min_value"] = self.min_value
        if self.max_value is not None:
            data["max_value"] = self.max_value
        if self.min_abs_value is not None:
            data["min_abs_value"] = self.min_abs_value
        return data


@dataclass(frozen=True)
class SignalMetricPayload(_RowParser):
    name: str
    value: float
    passed: bool
    threshold: ThresholdMetricPayload | None

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> "SignalMetricPayload" | None:
        try:
            name_value = raw["name"]
            value_value = raw["value"]
        except KeyError:
            return None
        name = cls._require_text(name_value, "name")
        try:
            value = float(value_value)  # type: ignore[arg-type]
        except Exception:
            return None
        passed = bool(raw.get("passed", False))
        threshold_mapping = raw.get("threshold")
        threshold_payload: ThresholdMetricPayload | None = None
        try:
            mapping = dict(threshold_mapping)  # type: ignore[arg-type]
        except Exception:
            threshold_payload = None
        else:
            threshold_payload = ThresholdMetricPayload.from_mapping(mapping)
        return cls(name=name, value=value, passed=passed, threshold=threshold_payload)

    def to_mapping(self) -> JSONDict:
        data: JSONDict = {
            "name": self.name,
            "value": self.value,
            "passed": self.passed,
        }
        if self.threshold is not None:
            data["threshold"] = self.threshold.to_mapping()
        return data


@dataclass(frozen=True)
class ThresholdRow(_RowParser):
    raw: JSONDict
    metadata: JSONDict
    metrics: tuple[ThresholdMetricPayload, ...]
    created_at: str | None
    updated_at: str | None
    allow_long: bool | None
    allow_short: bool | None

    @classmethod
    def empty(cls) -> "ThresholdRow":
        return cls(
            raw={},
            metadata={},
            metrics=(),
            created_at=None,
            updated_at=None,
            allow_long=None,
            allow_short=None,
        )

    @classmethod
    def from_cell(cls, value: object | None) -> "ThresholdRow":
        parsed = cls._parse_json(value)
        if parsed is None:
            return cls.empty()
        try:
            mapping = dict(parsed)  # type: ignore[arg-type]
        except Exception:
            return cls.empty()
        return cls.from_mapping(mapping)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object] | JSONDict) -> "ThresholdRow":
        data: JSONDict = {
            cls._require_text(key, "threshold_key"): cls._normalize_json(value)
            for key, value in mapping.items()
        }
        metadata_raw = data.get("metadata")
        try:
            metadata_mapping = dict(metadata_raw)  # type: ignore[arg-type]
        except Exception:
            metadata_mapping = {}
        metadata: JSONDict = {
            cls._require_text(key, "metadata_key"): cls._normalize_json(value)
            for key, value in metadata_mapping.items()
        }
        metrics_raw = data.get("metrics")
        metrics_entries: list[ThresholdMetricPayload] = []
        try:
            iterator = iter(metrics_raw)  # type: ignore[arg-type]
        except Exception:
            iterator = iter(())
        for entry in iterator:
            try:
                metric_mapping = dict(entry)  # type: ignore[arg-type]
            except Exception:
                continue
            metric = ThresholdMetricPayload.from_mapping(metric_mapping)
            if metric is not None:
                metrics_entries.append(metric)
        data["metadata"] = metadata
        metrics_data = [metric.to_mapping() for metric in metrics_entries]
        data["metrics"] = metrics_data
        created_at = cls._optional_text(data.get("created_at"))
        updated_at = cls._optional_text(data.get("updated_at"))
        allow_long = cls._optional_bool(data.get("allow_long"))
        allow_short = cls._optional_bool(data.get("allow_short"))
        return cls(
            raw=data,
            metadata=metadata,
            metrics=tuple(metrics_entries),
            created_at=created_at,
            updated_at=updated_at,
            allow_long=allow_long,
            allow_short=allow_short,
        )

    def to_json(self) -> str:
        return json.dumps(self.raw, sort_keys=True)

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
    thresholds: ThresholdRow
    metrics: tuple[SignalMetricPayload, ...]
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
    thresholds_snapshot: ThresholdRow | None
    metadata: JSONDict


@dataclass(frozen=True)
class SignalRow(_RowParser):
    HEADERS: ClassVar[tuple[str, ...]] = (
        "id",
        "candle_id",
        "symbol",
        "exchange",
        "timeframe",
        "candle_timeframe",
        "side",
        "direction",
        "score",
        "triggered_at",
        "created_at",
        "updated_at",
        "allow_long",
        "allow_short",
        "candle_open",
        "candle_high",
        "candle_low",
        "candle_close",
        "candle_volume",
        "candle_quote_volume",
        "candle_started_at",
        "candle_closed_at",
        "thresholds_json",
        "metrics_json",
        "metrics_snapshot_json",
        "metadata_json",
    )

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
    thresholds: ThresholdRow
    metrics: tuple[SignalMetricPayload, ...]
    metrics_snapshot: JSONDict | None
    metadata: JSONDict

    def as_excel_values(self) -> list[object]:
        metrics_json = [metric.to_mapping() for metric in self.metrics]
        snapshot_json = None
        if self.metrics_snapshot is not None:
            snapshot_json = json.dumps(self.metrics_snapshot, sort_keys=True)
        return [
            self.id,
            self.candle_id,
            self.symbol,
            self.exchange,
            self.timeframe,
            self.candle_timeframe,
            self.side,
            self.direction,
            self.score,
            self.triggered_at,
            self.created_at,
            self.updated_at,
            self.allow_long,
            self.allow_short,
            self.candle_open,
            self.candle_high,
            self.candle_low,
            self.candle_close,
            self.candle_volume,
            self.candle_quote_volume,
            self.candle_started_at,
            self.candle_closed_at,
            self.thresholds.to_json(),
            json.dumps(metrics_json, sort_keys=True),
            snapshot_json,
            json.dumps(self.metadata, sort_keys=True),
        ]

    @classmethod
    def from_excel_row(cls, headers: Sequence[str], values: Sequence[object]) -> "SignalRow":
        mapping: dict[str, object] = {
            headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))
        }
        metrics_raw = cls._parse_json(mapping.get("metrics_json"))
        metrics_entries: list[SignalMetricPayload] = []
        try:
            iterator = iter(metrics_raw)  # type: ignore[arg-type]
        except Exception:
            iterator = iter(())
        for entry in iterator:
            try:
                metric_mapping = dict(entry)  # type: ignore[arg-type]
            except Exception:
                continue
            metric = SignalMetricPayload.from_mapping(metric_mapping)
            if metric is not None:
                metrics_entries.append(metric)
        snapshot_raw = cls._parse_json(mapping.get("metrics_snapshot_json"))
        snapshot_dict: JSONDict | None = None
        if snapshot_raw:
            try:
                snapshot_items = snapshot_raw.items()  # type: ignore[attr-defined]
            except Exception:
                snapshot_dict = None
            else:
                snapshot_dict = {
                    cls._require_text(key, "snapshot_key"): cls._normalize_json(value)
                    for key, value in snapshot_items
                }
        metadata_raw = cls._parse_json(mapping.get("metadata_json"))
        metadata_dict: JSONDict = {}
        if metadata_raw:
            try:
                metadata_mapping = dict(metadata_raw)  # type: ignore[arg-type]
            except Exception:
                metadata_mapping = {}
            metadata_dict = {
                cls._require_text(key, "metadata_key"): cls._normalize_json(value)
                for key, value in metadata_mapping.items()
            }
        thresholds = ThresholdRow.from_cell(mapping.get("thresholds_json"))
        return cls(
            id=cls._require_text(mapping.get("id"), "id"),
            candle_id=cls._optional_text(mapping.get("candle_id")),
            symbol=cls._require_text(mapping.get("symbol"), "symbol"),
            exchange=cls._require_text(mapping.get("exchange"), "exchange"),
            timeframe=cls._optional_text(mapping.get("timeframe")),
            candle_timeframe=cls._optional_text(mapping.get("candle_timeframe")),
            side=cls._require_text(mapping.get("side"), "side"),
            direction=cls._require_int(mapping.get("direction"), "direction"),
            score=cls._require_float(mapping.get("score"), "score"),
            triggered_at=cls._require_text(mapping.get("triggered_at"), "triggered_at"),
            created_at=cls._optional_text(mapping.get("created_at")),
            updated_at=cls._optional_text(mapping.get("updated_at")),
            allow_long=cls._require_bool(mapping.get("allow_long", True), "allow_long"),
            allow_short=cls._require_bool(mapping.get("allow_short", True), "allow_short"),
            candle_open=cls._require_float(mapping.get("candle_open"), "candle_open"),
            candle_high=cls._require_float(mapping.get("candle_high"), "candle_high"),
            candle_low=cls._require_float(mapping.get("candle_low"), "candle_low"),
            candle_close=cls._require_float(mapping.get("candle_close"), "candle_close"),
            candle_volume=cls._require_float(mapping.get("candle_volume"), "candle_volume"),
            candle_quote_volume=cls._optional_float(mapping.get("candle_quote_volume")),
            candle_started_at=cls._require_text(
                mapping.get("candle_started_at"), "candle_started_at"
            ),
            candle_closed_at=cls._require_text(
                mapping.get("candle_closed_at"), "candle_closed_at"
            ),
            thresholds=thresholds,
            metrics=tuple(metrics_entries),
            metrics_snapshot=snapshot_dict,
            metadata=metadata_dict,
        )

    def to_payload(self) -> SignalPayload:
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
            thresholds=self.thresholds,
            metrics=self.metrics,
            metrics_snapshot=self.metrics_snapshot,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class TradeRow(_RowParser):
    HEADERS: ClassVar[tuple[str, ...]] = (
        "id",
        "signal_id",
        "source_signal_id",
        "exchange",
        "symbol",
        "timeframe",
        "side",
        "status",
        "entry_price",
        "size",
        "used_margin",
        "exit_price",
        "tp_price",
        "sl_price",
        "tp_pct",
        "sl_pct",
        "opened_at",
        "closed_at",
        "pnl",
        "pnl_pct",
        "created_at",
        "updated_at",
        "allow_long",
        "allow_short",
        "thresholds_snapshot_json",
        "metadata_json",
    )

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
    thresholds_snapshot: ThresholdRow | None
    metadata: JSONDict

    def as_excel_values(self) -> list[object]:
        snapshot_json = (
            self.thresholds_snapshot.to_json()
            if self.thresholds_snapshot is not None
            else None
        )
        return [
            self.id,
            self.signal_id,
            self.source_signal_id,
            self.exchange,
            self.symbol,
            self.timeframe,
            self.side,
            self.status,
            self.entry_price,
            self.size,
            self.used_margin,
            self.exit_price,
            self.tp_price,
            self.sl_price,
            self.tp_pct,
            self.sl_pct,
            self.opened_at,
            self.closed_at,
            self.pnl,
            self.pnl_pct,
            self.created_at,
            self.updated_at,
            self.allow_long,
            self.allow_short,
            snapshot_json,
            json.dumps(self.metadata, sort_keys=True),
        ]

    @classmethod
    def from_excel_row(cls, headers: Sequence[str], values: Sequence[object]) -> "TradeRow":
        mapping: dict[str, object] = {
            headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))
        }
        thresholds_snapshot = ThresholdRow.from_cell(mapping.get("thresholds_snapshot_json"))
        if not thresholds_snapshot.raw:
            thresholds_snapshot_value: ThresholdRow | None = None
        else:
            thresholds_snapshot_value = thresholds_snapshot
        metadata_raw = cls._parse_json(mapping.get("metadata_json"))
        metadata: JSONDict = {}
        if metadata_raw:
            try:
                metadata_mapping = dict(metadata_raw)  # type: ignore[arg-type]
            except Exception:
                metadata_mapping = {}
            metadata = {
                cls._require_text(key, "metadata_key"): cls._normalize_json(value)
                for key, value in metadata_mapping.items()
            }
        used_margin_value = mapping.get("used_margin")
        if used_margin_value is None:
            used_margin_value = mapping.get("used_amount")
        if used_margin_value is None:
            used_margin_value = mapping.get("size")
        return cls(
            id=cls._require_text(mapping.get("id"), "id"),
            signal_id=cls._require_text(mapping.get("signal_id"), "signal_id"),
            source_signal_id=cls._optional_text(mapping.get("source_signal_id")),
            exchange=cls._require_text(mapping.get("exchange"), "exchange"),
            symbol=cls._require_text(mapping.get("symbol"), "symbol"),
            timeframe=cls._optional_text(mapping.get("timeframe")),
            side=cls._require_text(mapping.get("side"), "side"),
            status=cls._require_text(mapping.get("status"), "status"),
            entry_price=cls._require_float(mapping.get("entry_price"), "entry_price"),
            size=cls._require_float(mapping.get("size"), "size"),
            used_margin=cls._require_float(used_margin_value, "used_margin"),
            exit_price=cls._optional_float(mapping.get("exit_price")),
            tp_price=cls._optional_float(mapping.get("tp_price")),
            sl_price=cls._optional_float(mapping.get("sl_price")),
            tp_pct=cls._optional_float(mapping.get("tp_pct")),
            sl_pct=cls._optional_float(mapping.get("sl_pct")),
            opened_at=cls._optional_text(mapping.get("opened_at")),
            closed_at=cls._optional_text(mapping.get("closed_at")),
            pnl=cls._optional_float(mapping.get("pnl")),
            pnl_pct=cls._optional_float(mapping.get("pnl_pct")),
            created_at=cls._optional_text(mapping.get("created_at")),
            updated_at=cls._optional_text(mapping.get("updated_at")),
            allow_long=cls._require_bool(mapping.get("allow_long", True), "allow_long"),
            allow_short=cls._require_bool(mapping.get("allow_short", True), "allow_short"),
            thresholds_snapshot=thresholds_snapshot_value,
            metadata=metadata,
        )

    def to_payload(self) -> TradePayload:
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
            thresholds_snapshot=self.thresholds_snapshot,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class StateRow(_RowParser):
    HEADERS: ClassVar[tuple[str, ...]] = (
        "key",
        "asset",
        "deposit_amount",
        "deposit_updated_at",
        "used_amount",
    )

    key: str
    asset: str
    deposit_amount: float
    deposit_updated_at: str | None
    used_amount: float

    def as_excel_values(self) -> list[object]:
        return [
            self.key,
            self.asset,
            self.deposit_amount,
            self.deposit_updated_at,
            self.used_amount,
        ]

    @classmethod
    def from_excel_row(cls, headers: Sequence[str], values: Sequence[object]) -> "StateRow":
        mapping: dict[str, object] = {
            headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))
        }
        key_value = cls._optional_text(mapping.get("key")) or "default"
        asset_value = cls._optional_text(mapping.get("asset")) or "USDT"
        deposit_amount = cls._optional_float(mapping.get("deposit_amount")) or 0.0
        deposit_updated_at = cls._optional_text(mapping.get("deposit_updated_at"))
        used_amount = cls._optional_float(mapping.get("used_amount"))
        if used_amount is None:
            used_amount = cls._optional_float(mapping.get("used")) or 0.0
        return cls(
            key=key_value,
            asset=asset_value,
            deposit_amount=deposit_amount,
            deposit_updated_at=deposit_updated_at,
            used_amount=used_amount or 0.0,
        )
