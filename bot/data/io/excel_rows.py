from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar, Mapping, Sequence, TYPE_CHECKING

from ...domain.models.metadata import (
    SignalMetadata,
    SignalMetadataPayload,
    ThresholdsMetadata,
    ThresholdsMetadataPayload,
    TradeMetadata,
    TradeMetadataPayload,
)
from ...domain.services.metrics_service import SelectionMetricsSnapshot


if TYPE_CHECKING:  # pragma: no cover - imported only for type checking
    from ...domain.models.entities import (
        Candle,
        Signal,
        SignalMetric,
        ThresholdMetric,
        Thresholds,
        Trade,
    )


Primitive = str | int | float | bool | None
JSONValue = Primitive | list["JSONValue"] | dict[str, "JSONValue"]
JSONDict = dict[str, JSONValue]


def _require_text(value: object | None, field: str) -> str:
    if isinstance(value, str):
        text = value.strip()
        if text:
            return text
    if value is None:
        raise ValueError(f"Missing required value for '{field}'")
    text = str(value).strip()
    if not text:
        raise ValueError(f"Missing required value for '{field}'")
    return text


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return str(value)


def _require_float(value: object | None, field: str) -> float:
    if value is None:
        raise ValueError(f"Missing required value for '{field}'")
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unable to convert '{field}' to float") from exc


def _optional_float(value: object | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except Exception:
        return None


def _optional_bool(value: object | None, *, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    if default is not None:
        return default
    return bool(value)


def _dump_json(data: JSONValue) -> str:
    return json.dumps(data, sort_keys=True)


def _load_json(value: object | None) -> JSONValue | None:
    if value in (None, ""):
        return None
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _threshold_range_from_value(value: object) -> tuple[float | None, float | None] | None:
    if isinstance(value, Mapping):
        minimum = _optional_float(value.get("min"))
        if minimum is None:
            minimum = _optional_float(value.get("min_value"))
        maximum = _optional_float(value.get("max"))
        if maximum is None:
            maximum = _optional_float(value.get("max_value"))
        if minimum is None and maximum is None:
            return None
        return (minimum, maximum)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        sequence = list(value)
        if not sequence:
            return None
        minimum = _optional_float(sequence[0])
        maximum = _optional_float(sequence[1]) if len(sequence) > 1 else None
        if minimum is None and maximum is None:
            return None
        return (minimum, maximum)
    return None


def _threshold_ranges_from_value(value: object | None) -> tuple[tuple[float | None, float | None], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    ranges: list[tuple[float | None, float | None]] = []
    for entry in value:
        parsed = _threshold_range_from_value(entry)
        if parsed is not None:
            ranges.append(parsed)
    return tuple(ranges)


def _serialize_threshold_ranges(
    ranges: tuple[tuple[float | None, float | None], ...]
) -> list[dict[str, float | None]]:
    return [
        {"min": minimum, "max": maximum}
        for minimum, maximum in ranges
    ]


def _extract_float(mapping: Mapping[str, object], keys: Sequence[str]) -> float | None:
    for key in keys:
        if key not in mapping:
            continue
        converted = _optional_float(mapping.get(key))
        if converted is not None:
            return converted
    return None


def _extract_bool(mapping: Mapping[str, object], keys: Sequence[str]) -> bool | None:
    for key in keys:
        if key not in mapping:
            continue
        converted = _optional_bool(mapping.get(key))
        if converted is not None:
            return converted
    return None


def _serialize_metadata_payload(payload: ThresholdsMetadataPayload | None) -> JSONDict:
    metadata = ThresholdsMetadata.from_mapping(payload)
    return metadata.to_dict() if metadata is not None else {}


def _serialize_signal_metadata_payload(payload: SignalMetadataPayload | None) -> JSONDict:
    metadata = SignalMetadata.from_mapping(payload)
    return metadata.to_dict() if metadata is not None else {}


def _serialize_trade_metadata_payload(payload: TradeMetadataPayload | None) -> JSONDict:
    metadata = TradeMetadata.from_mapping(payload)
    return metadata.to_dict() if metadata is not None else {}


def _metrics_snapshot_to_mapping(
    snapshot: SelectionMetricsSnapshot | None,
) -> JSONDict:
    if snapshot is None:
        return {}
    mapping = snapshot.to_mapping()
    # BreakDirection is serialized as integer in SelectionMetricsSnapshot.to_mapping
    return {key: value for key, value in mapping.items()}


def _metrics_snapshot_from_mapping(mapping: Mapping[str, JSONValue] | None) -> SelectionMetricsSnapshot | None:
    if mapping is None:
        return None
    try:
        return SelectionMetricsSnapshot.from_mapping(mapping)
    except (KeyError, TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ThresholdMetricPayload:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_metric(cls, metric: ThresholdMetric) -> "ThresholdMetricPayload":
        return cls(
            name=metric.name,
            min_value=metric.min_value,
            max_value=metric.max_value,
            min_abs_value=metric.min_abs_value,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "ThresholdMetricPayload" | None:
        if raw is None:
            return None
        name_raw = raw.get("name")
        if not isinstance(name_raw, str):
            return None
        return cls(
            name=name_raw,
            min_value=_optional_float(raw.get("min_value")),
            max_value=_optional_float(raw.get("max_value")),
            min_abs_value=_optional_float(raw.get("min_abs_value")),
        )

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
class SignalMetricPayload:
    name: str
    value: float
    passed: bool
    threshold: ThresholdMetricPayload | None

    @classmethod
    def from_metric(cls, metric: SignalMetric) -> "SignalMetricPayload":
        threshold_payload = (
            ThresholdMetricPayload.from_metric(metric.threshold)
            if metric.threshold is not None
            else None
        )
        return cls(
            name=metric.name,
            value=metric.value,
            passed=metric.passed,
            threshold=threshold_payload,
        )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object] | None) -> "SignalMetricPayload" | None:
        if raw is None:
            return None
        name_raw = raw.get("name")
        value_raw = raw.get("value")
        passed_raw = raw.get("passed")
        if not isinstance(name_raw, str):
            return None
        value = _optional_float(value_raw)
        if value is None:
            return None
        passed = bool(passed_raw) if passed_raw is not None else False
        threshold_payload = None
        threshold_raw = raw.get("threshold")
        if isinstance(threshold_raw, Mapping):
            threshold_payload = ThresholdMetricPayload.from_mapping(threshold_raw)
        return cls(
            name=name_raw,
            value=value,
            passed=passed,
            threshold=threshold_payload,
        )

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
class ThresholdRow:
    id: str | None
    min_relative_volume: float | None
    max_relative_volume: float | None
    min_atr_mult: float | None
    min_pct_move: float | None
    max_pct_move: float | None
    max_upper_wick_pct: float | None
    max_lower_wick_pct: float | None
    short_pct_move_ranges: tuple[tuple[float | None, float | None], ...]
    short_relative_volume_ranges: tuple[tuple[float | None, float | None], ...]
    allow_long: bool
    allow_short: bool
    metrics: tuple[ThresholdMetricPayload, ...]
    metadata: ThresholdsMetadataPayload | None
    created_at: str | None
    updated_at: str | None

    @classmethod
    def empty(cls) -> "ThresholdRow":
        return cls(
            id=None,
            min_relative_volume=None,
            max_relative_volume=None,
            min_atr_mult=None,
            min_pct_move=None,
            max_pct_move=None,
            max_upper_wick_pct=None,
            max_lower_wick_pct=None,
            short_pct_move_ranges=(),
            short_relative_volume_ranges=(),
            allow_long=True,
            allow_short=True,
            metrics=(),
            metadata=None,
            created_at=None,
            updated_at=None,
        )

    @classmethod
    def from_thresholds(cls, thresholds: Thresholds) -> "ThresholdRow":
        metadata_payload = ThresholdsMetadataPayload.from_mapping(
            thresholds.metadata.to_dict() if thresholds.metadata else None
        )
        metrics = tuple(
            ThresholdMetricPayload.from_metric(metric) for metric in thresholds.metrics
        )
        created_at = thresholds.created_at.isoformat() if thresholds.created_at else None
        updated_at = thresholds.updated_at.isoformat() if thresholds.updated_at else None
        return cls(
            id=thresholds.id,
            min_relative_volume=thresholds.min_relative_volume,
            max_relative_volume=thresholds.max_relative_volume,
            min_atr_mult=thresholds.min_atr_mult,
            min_pct_move=thresholds.min_pct_move,
            max_pct_move=thresholds.max_pct_move,
            max_upper_wick_pct=thresholds.max_upper_wick_pct,
            max_lower_wick_pct=thresholds.max_lower_wick_pct,
            short_pct_move_ranges=tuple(thresholds.short_pct_move_ranges),
            short_relative_volume_ranges=tuple(thresholds.short_relative_volume_ranges),
            allow_long=thresholds.allow_long,
            allow_short=thresholds.allow_short,
            metrics=metrics,
            metadata=metadata_payload,
            created_at=created_at,
            updated_at=updated_at,
        )

    @classmethod
    def from_json(cls, value: object | None) -> "ThresholdRow":
        parsed = _load_json(value)
        if not isinstance(parsed, Mapping):
            return cls.empty()
        return cls.from_mapping(parsed)

    @classmethod
    def from_mapping(cls, mapping: Mapping[str, object]) -> "ThresholdRow":
        metrics_raw = mapping.get("metrics")
        metrics: list[ThresholdMetricPayload] = []
        if isinstance(metrics_raw, Sequence) and not isinstance(metrics_raw, (str, bytes, bytearray)):
            for entry in metrics_raw:
                if isinstance(entry, Mapping):
                    metric = ThresholdMetricPayload.from_mapping(entry)
                    if metric is not None:
                        metrics.append(metric)
        metadata_value = mapping.get("metadata")
        metadata_mapping = metadata_value if isinstance(metadata_value, Mapping) else None
        metadata_payload = ThresholdsMetadataPayload.from_mapping(metadata_mapping)
        short_pct_ranges = _threshold_ranges_from_value(
            mapping.get("short_pct_move_ranges")
        )
        if not short_pct_ranges:
            short_pct_ranges = _threshold_ranges_from_value(
                mapping.get("shortPctMoveRanges")
            )
        short_relative_ranges = _threshold_ranges_from_value(
            mapping.get("short_relative_volume_ranges")
        )
        if not short_relative_ranges:
            short_relative_ranges = _threshold_ranges_from_value(
                mapping.get("shortRelativeVolumeRanges")
            )
        allow_long = _extract_bool(mapping, ["allow_long", "allowLong"])
        allow_short = _extract_bool(mapping, ["allow_short", "allowShort"])
        return cls(
            id=_optional_text(mapping.get("id")),
            min_relative_volume=_extract_float(
                mapping, ["min_relative_volume", "minRelativeVolume", "S", "s"]
            ),
            max_relative_volume=_extract_float(
                mapping, ["max_relative_volume", "maxRelativeVolume", "T", "t"]
            ),
            min_atr_mult=_extract_float(mapping, ["min_atr_mult", "minAtrMult", "U", "u"]),
            min_pct_move=_extract_float(mapping, ["min_pct_move", "minPctMove", "V", "v"]),
            max_pct_move=_extract_float(mapping, ["max_pct_move", "maxPctMove", "W", "w"]),
            max_upper_wick_pct=_extract_float(
                mapping, ["max_upper_wick_pct", "maxUpperWickPct", "X", "x"]
            ),
            max_lower_wick_pct=_extract_float(
                mapping, ["max_lower_wick_pct", "maxLowerWickPct", "Y", "y"]
            ),
            short_pct_move_ranges=short_pct_ranges,
            short_relative_volume_ranges=short_relative_ranges,
            allow_long=allow_long if allow_long is not None else True,
            allow_short=allow_short if allow_short is not None else True,
            metrics=tuple(metrics),
            metadata=metadata_payload,
            created_at=_optional_text(
                mapping.get("created_at") or mapping.get("createdAt")
            ),
            updated_at=_optional_text(
                mapping.get("updated_at") or mapping.get("updatedAt")
            ),
        )

    def to_json(self) -> str:
        data: JSONDict = {}
        if self.id is not None:
            data["id"] = self.id
        if self.min_relative_volume is not None:
            data["min_relative_volume"] = self.min_relative_volume
        if self.max_relative_volume is not None:
            data["max_relative_volume"] = self.max_relative_volume
        if self.min_atr_mult is not None:
            data["min_atr_mult"] = self.min_atr_mult
        if self.min_pct_move is not None:
            data["min_pct_move"] = self.min_pct_move
        if self.max_pct_move is not None:
            data["max_pct_move"] = self.max_pct_move
        if self.max_upper_wick_pct is not None:
            data["max_upper_wick_pct"] = self.max_upper_wick_pct
        if self.max_lower_wick_pct is not None:
            data["max_lower_wick_pct"] = self.max_lower_wick_pct
        if self.short_pct_move_ranges:
            data["short_pct_move_ranges"] = _serialize_threshold_ranges(
                self.short_pct_move_ranges
            )
        if self.short_relative_volume_ranges:
            data["short_relative_volume_ranges"] = _serialize_threshold_ranges(
                self.short_relative_volume_ranges
            )
        data["allow_long"] = self.allow_long
        data["allow_short"] = self.allow_short
        if self.metrics:
            data["metrics"] = [metric.to_mapping() for metric in self.metrics]
        metadata_mapping = _serialize_metadata_payload(self.metadata)
        if metadata_mapping:
            data["metadata"] = metadata_mapping
        if self.created_at is not None:
            data["created_at"] = self.created_at
        if self.updated_at is not None:
            data["updated_at"] = self.updated_at
        return _dump_json(data)


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

    @classmethod
    def from_candle(cls, candle: Candle) -> "CandlePayload":
        return cls(
            id=candle.id,
            symbol=candle.symbol,
            exchange=candle.exchange.value,
            timeframe=candle.timeframe.value if candle.timeframe else None,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            quote_volume=candle.quote_volume,
            started_at=candle.started_at.isoformat(),
            closed_at=candle.closed_at.isoformat(),
        )


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
class SignalRow:
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
    metrics_snapshot: SelectionMetricsSnapshot | None
    metadata: SignalMetadataPayload | None

    @classmethod
    def from_signal(cls, signal: Signal) -> "SignalRow":
        candle = signal.candle
        thresholds_row = ThresholdRow.from_thresholds(signal.thresholds)
        metrics = tuple(SignalMetricPayload.from_metric(metric) for metric in signal.metrics)
        metadata_payload = SignalMetadataPayload.from_mapping(
            signal.metadata.to_dict() if signal.metadata else None
        )
        snapshot = signal.metrics_snapshot
        return cls(
            id=signal.id,
            candle_id=signal.candle_id or candle.id,
            symbol=candle.symbol,
            exchange=candle.exchange.value,
            timeframe=signal.timeframe.value if signal.timeframe else None,
            candle_timeframe=candle.timeframe.value if candle.timeframe else None,
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
            thresholds=thresholds_row,
            metrics=metrics,
            metrics_snapshot=snapshot,
            metadata=metadata_payload,
        )

    def as_excel_values(self) -> list[object]:
        metrics_json = [metric.to_mapping() for metric in self.metrics]
        metadata_json = _serialize_signal_metadata_payload(self.metadata)
        snapshot_json = _metrics_snapshot_to_mapping(self.metrics_snapshot)
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
            _dump_json(metrics_json),
            _dump_json(snapshot_json) if snapshot_json else None,
            _dump_json(metadata_json),
        ]

    @classmethod
    def from_excel_row(cls, headers: Sequence[str], values: Sequence[object]) -> "SignalRow":
        mapping = {headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))}
        thresholds = ThresholdRow.from_json(mapping.get("thresholds_json"))
        metrics_raw = _load_json(mapping.get("metrics_json"))
        metrics: list[SignalMetricPayload] = []
        if isinstance(metrics_raw, Sequence) and not isinstance(metrics_raw, (str, bytes, bytearray)):
            for entry in metrics_raw:
                if isinstance(entry, Mapping):
                    metric = SignalMetricPayload.from_mapping(entry)
                    if metric is not None:
                        metrics.append(metric)
        snapshot_raw = _load_json(mapping.get("metrics_snapshot_json"))
        snapshot = None
        if isinstance(snapshot_raw, Mapping):
            snapshot = _metrics_snapshot_from_mapping(snapshot_raw)
        metadata_raw = _load_json(mapping.get("metadata_json"))
        metadata_payload = None
        if isinstance(metadata_raw, Mapping):
            metadata_payload = SignalMetadataPayload.from_mapping(metadata_raw)
        allow_long = _optional_bool(mapping.get("allow_long"), default=True)
        allow_short = _optional_bool(mapping.get("allow_short"), default=True)
        return cls(
            id=_require_text(mapping.get("id"), "id"),
            candle_id=_optional_text(mapping.get("candle_id")),
            symbol=_require_text(mapping.get("symbol"), "symbol"),
            exchange=_require_text(mapping.get("exchange"), "exchange"),
            timeframe=_optional_text(mapping.get("timeframe")),
            candle_timeframe=_optional_text(mapping.get("candle_timeframe")),
            side=_require_text(mapping.get("side"), "side"),
            direction=_optional_int(mapping.get("direction")) or 0,
            score=_require_float(mapping.get("score"), "score"),
            triggered_at=_require_text(mapping.get("triggered_at"), "triggered_at"),
            created_at=_optional_text(mapping.get("created_at")),
            updated_at=_optional_text(mapping.get("updated_at")),
            allow_long=allow_long if allow_long is not None else True,
            allow_short=allow_short if allow_short is not None else True,
            candle_open=_require_float(mapping.get("candle_open"), "candle_open"),
            candle_high=_require_float(mapping.get("candle_high"), "candle_high"),
            candle_low=_require_float(mapping.get("candle_low"), "candle_low"),
            candle_close=_require_float(mapping.get("candle_close"), "candle_close"),
            candle_volume=_require_float(mapping.get("candle_volume"), "candle_volume"),
            candle_quote_volume=_optional_float(mapping.get("candle_quote_volume")),
            candle_started_at=_require_text(mapping.get("candle_started_at"), "candle_started_at"),
            candle_closed_at=_require_text(mapping.get("candle_closed_at"), "candle_closed_at"),
            thresholds=thresholds,
            metrics=tuple(metrics),
            metrics_snapshot=snapshot,
            metadata=metadata_payload,
        )

    def to_payload(self) -> SignalPayload:
        candle_payload = CandlePayload(
            id=self.candle_id,
            symbol=self.symbol,
            exchange=self.exchange,
            timeframe=self.candle_timeframe,
            open=self.candle_open,
            high=self.candle_high,
            low=self.candle_low,
            close=self.candle_close,
            volume=self.candle_volume,
            quote_volume=self.candle_quote_volume,
            started_at=self.candle_started_at,
            closed_at=self.candle_closed_at,
        )
        snapshot_mapping = _metrics_snapshot_to_mapping(self.metrics_snapshot)
        metadata_mapping = _serialize_signal_metadata_payload(self.metadata)
        return SignalPayload(
            id=self.id,
            candle_id=self.candle_id,
            candle=candle_payload,
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
            metrics_snapshot=snapshot_mapping or None,
            metadata=metadata_mapping,
        )


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
class TradeRow:
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
    metadata: TradeMetadataPayload | None

    @classmethod
    def from_trade(cls, trade: Trade) -> "TradeRow":
        thresholds_snapshot = (
            ThresholdRow.from_thresholds(trade.thresholds_snapshot)
            if trade.thresholds_snapshot is not None
            else None
        )
        metadata_payload = TradeMetadataPayload.from_mapping(
            trade.metadata.to_dict() if trade.metadata else None
        )
        return cls(
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
            thresholds_snapshot=thresholds_snapshot,
            metadata=metadata_payload,
        )

    def as_excel_values(self) -> list[object]:
        metadata_json = _serialize_trade_metadata_payload(self.metadata)
        thresholds_snapshot_json = (
            self.thresholds_snapshot.to_json() if self.thresholds_snapshot is not None else None
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
            thresholds_snapshot_json,
            _dump_json(metadata_json),
        ]

    @classmethod
    def from_excel_row(cls, headers: Sequence[str], values: Sequence[object]) -> "TradeRow":
        mapping = {headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))}
        thresholds_snapshot = ThresholdRow.from_json(mapping.get("thresholds_snapshot_json"))
        if thresholds_snapshot.id is None and not thresholds_snapshot.metrics:
            thresholds_snapshot_value: ThresholdRow | None = None
        else:
            thresholds_snapshot_value = thresholds_snapshot
        metadata_raw = _load_json(mapping.get("metadata_json"))
        metadata_payload = None
        if isinstance(metadata_raw, Mapping):
            metadata_payload = TradeMetadataPayload.from_mapping(metadata_raw)
        used_margin_value = mapping.get("used_margin")
        if used_margin_value is None:
            used_margin_value = mapping.get("used_amount")
        if used_margin_value is None:
            used_margin_value = mapping.get("size")
        allow_long = _optional_bool(mapping.get("allow_long"), default=True)
        allow_short = _optional_bool(mapping.get("allow_short"), default=True)
        return cls(
            id=_require_text(mapping.get("id"), "id"),
            signal_id=_require_text(mapping.get("signal_id"), "signal_id"),
            source_signal_id=_optional_text(mapping.get("source_signal_id")),
            exchange=_require_text(mapping.get("exchange"), "exchange"),
            symbol=_require_text(mapping.get("symbol"), "symbol"),
            timeframe=_optional_text(mapping.get("timeframe")),
            side=_require_text(mapping.get("side"), "side"),
            status=_require_text(mapping.get("status"), "status"),
            entry_price=_require_float(mapping.get("entry_price"), "entry_price"),
            size=_require_float(mapping.get("size"), "size"),
            used_margin=_require_float(used_margin_value, "used_margin"),
            exit_price=_optional_float(mapping.get("exit_price")),
            tp_price=_optional_float(mapping.get("tp_price")),
            sl_price=_optional_float(mapping.get("sl_price")),
            tp_pct=_optional_float(mapping.get("tp_pct")),
            sl_pct=_optional_float(mapping.get("sl_pct")),
            opened_at=_optional_text(mapping.get("opened_at")),
            closed_at=_optional_text(mapping.get("closed_at")),
            pnl=_optional_float(mapping.get("pnl")),
            pnl_pct=_optional_float(mapping.get("pnl_pct")),
            created_at=_optional_text(mapping.get("created_at")),
            updated_at=_optional_text(mapping.get("updated_at")),
            allow_long=allow_long if allow_long is not None else True,
            allow_short=allow_short if allow_short is not None else True,
            thresholds_snapshot=thresholds_snapshot_value,
            metadata=metadata_payload,
        )

    def to_payload(self) -> TradePayload:
        metadata_mapping = _serialize_trade_metadata_payload(self.metadata)
        thresholds_snapshot_value = self.thresholds_snapshot
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
            thresholds_snapshot=thresholds_snapshot_value,
            metadata=metadata_mapping,
        )


@dataclass(frozen=True)
class StateRow:
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
        mapping = {headers[idx]: values[idx] for idx in range(min(len(headers), len(values)))}
        key_value = _optional_text(mapping.get("key")) or "default"
        asset_value = _optional_text(mapping.get("asset")) or "USDT"
        deposit_amount = _optional_float(mapping.get("deposit_amount")) or 0.0
        deposit_updated_at = _optional_text(mapping.get("deposit_updated_at"))
        used_amount = _optional_float(mapping.get("used_amount"))
        if used_amount is None:
            used_amount = _optional_float(mapping.get("used")) or 0.0
        return cls(
            key=key_value,
            asset=asset_value,
            deposit_amount=deposit_amount,
            deposit_updated_at=deposit_updated_at,
            used_amount=used_amount,
        )
