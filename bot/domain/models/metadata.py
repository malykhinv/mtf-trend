from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, Mapping, Sequence, Type, TypeVar

from ..enums import Timeframe, TradeStatus

if TYPE_CHECKING:
    from .entities import ThresholdMetric


T = TypeVar("T", bound="_SerializableDataclass")


class _SerializableDataclass:
    """Utility mixin for metadata dataclasses."""

    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    @classmethod
    def from_mapping(cls: Type[T], raw: Any) -> T | None:  # pragma: no cover - overridden
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------

def _to_optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _parse_bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    return bool(value)


# ---------------------------------------------------------------------------
# Range metadata structures
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ThresholdRange:
    minimum: float | None = None
    maximum: float | None = None

    def is_empty(self) -> bool:
        return self.minimum is None and self.maximum is None

    def to_mapping(self) -> Dict[str, float | None]:
        mapping: Dict[str, float | None] = {}
        if self.minimum is not None:
            mapping["min"] = self.minimum
        if self.maximum is not None:
            mapping["max"] = self.maximum
        return mapping


def _parse_range_sequence(raw: Any) -> tuple[ThresholdRange, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        return ()
    ranges: list[ThresholdRange] = []
    for entry in raw:
        minimum: float | None = None
        maximum: float | None = None
        if isinstance(entry, Mapping):
            min_raw = entry.get("min", entry.get("min_value"))
            max_raw = entry.get("max", entry.get("max_value"))
            minimum = _to_optional_float(min_raw)
            maximum = _to_optional_float(max_raw)
        elif isinstance(entry, Sequence) and not isinstance(entry, (str, bytes, bytearray)):
            sequence_entry = list(entry)
            if sequence_entry:
                minimum = _to_optional_float(sequence_entry[0])
            if len(sequence_entry) > 1:
                maximum = _to_optional_float(sequence_entry[1])
        if minimum is None and maximum is None:
            continue
        ranges.append(ThresholdRange(minimum=minimum, maximum=maximum))
    return tuple(ranges)


# ---------------------------------------------------------------------------
# Payload dataclasses used for typed deserialization
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ThresholdsMetadataPayload:
    timeframe: Timeframe | None = None
    timeframe_raw: str | None = None
    short_pct_move_ranges: tuple[ThresholdRange, ...] = ()
    short_relative_volume_ranges: tuple[ThresholdRange, ...] = ()
    note: str | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "ThresholdsMetadataPayload" | None:
        if raw is None:
            return None
        timeframe: Timeframe | None = None
        timeframe_raw: str | None = None
        timeframe_value = raw.get("timeframe")
        if isinstance(timeframe_value, Timeframe):
            timeframe = timeframe_value
        elif isinstance(timeframe_value, str):
            try:
                timeframe = Timeframe(timeframe_value)
            except ValueError:
                timeframe_raw = timeframe_value
        note_value = raw.get("note")
        note = str(note_value) if isinstance(note_value, str) else None
        short_pct_move_ranges = _parse_range_sequence(raw.get("short_pct_move_ranges"))
        short_relative_volume_ranges = _parse_range_sequence(
            raw.get("short_relative_volume_ranges")
        )
        return cls(
            timeframe=timeframe,
            timeframe_raw=timeframe_raw,
            short_pct_move_ranges=short_pct_move_ranges,
            short_relative_volume_ranges=short_relative_volume_ranges,
            note=note,
        )


@dataclass(slots=True)
class ThresholdMetricMetadataPayload:
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "ThresholdMetricMetadataPayload" | None:
        if raw is None:
            return None
        name_value = raw.get("name")
        if not isinstance(name_value, str):
            return None
        return cls(
            name=name_value,
            min_value=_to_optional_float(raw.get("min_value")),
            max_value=_to_optional_float(raw.get("max_value")),
            min_abs_value=_to_optional_float(raw.get("min_abs_value")),
        )


@dataclass(slots=True)
class EvaluationMetadataPayload:
    name: str
    value: float | None = None
    passed: bool | None = None
    threshold: ThresholdMetricMetadataPayload | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "EvaluationMetadataPayload" | None:
        if raw is None:
            return None
        name_value = raw.get("name")
        if not isinstance(name_value, str):
            return None
        threshold_raw = raw.get("threshold")
        threshold_payload = None
        if isinstance(threshold_raw, Mapping):
            threshold_payload = ThresholdMetricMetadataPayload.from_mapping(threshold_raw)
        return cls(
            name=name_value,
            value=_to_optional_float(raw.get("value")),
            passed=_parse_bool(raw.get("passed")),
            threshold=threshold_payload,
        )


@dataclass(slots=True)
class DepositSnapshotMetadataPayload:
    asset: str | None = None
    balance: float | None = None
    updated_at: datetime | None = None
    raw: Dict[str, Any] | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "DepositSnapshotMetadataPayload" | None:
        if raw is None:
            return None
        asset_value = raw.get("asset")
        asset = str(asset_value) if isinstance(asset_value, str) else None
        balance = _to_optional_float(raw.get("balance"))
        updated_raw = raw.get("updated_at")
        updated_at = None
        if isinstance(updated_raw, str):
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except ValueError:
                updated_at = None
        raw_snapshot = raw.get("raw")
        raw_mapping = dict(raw_snapshot) if isinstance(raw_snapshot, Mapping) else None
        return cls(asset=asset, balance=balance, updated_at=updated_at, raw=raw_mapping)


@dataclass(slots=True)
class BacktestMetadataPayload:
    result_pct: float | None = None
    pct_to_high_break: float | None = None
    pct_to_low_break: float | None = None
    note: str | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "BacktestMetadataPayload" | None:
        if raw is None:
            return None
        note_value = raw.get("note")
        note = str(note_value) if isinstance(note_value, str) else None
        return cls(
            result_pct=_to_optional_float(raw.get("result_pct")),
            pct_to_high_break=_to_optional_float(raw.get("pct_to_high_break")),
            pct_to_low_break=_to_optional_float(raw.get("pct_to_low_break")),
            note=note,
        )


@dataclass(slots=True)
class LiveMetadataPayload:
    result_pct: float | None = None
    closed_status: TradeStatus | None = None
    note: str | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "LiveMetadataPayload" | None:
        if raw is None:
            return None
        status_raw = raw.get("closed_status")
        closed_status = None
        if isinstance(status_raw, TradeStatus):
            closed_status = status_raw
        elif isinstance(status_raw, str):
            try:
                closed_status = TradeStatus(status_raw)
            except ValueError:
                closed_status = None
        note_value = raw.get("note")
        note = str(note_value) if isinstance(note_value, str) else None
        return cls(
            result_pct=_to_optional_float(raw.get("result_pct")),
            closed_status=closed_status,
            note=note,
        )


@dataclass(slots=True)
class SignalMetadataPayload:
    symbol: str | None = None
    timeframe: str | None = None
    source_signal_id: str | None = None
    evaluations: tuple[EvaluationMetadataPayload, ...] = ()
    note: str | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "SignalMetadataPayload" | None:
        if raw is None:
            return None
        symbol_value = raw.get("symbol")
        timeframe_value = raw.get("timeframe")
        source_signal_value = raw.get("source_signal_id")
        note_value = raw.get("note")
        evaluations_raw = raw.get("evaluations")
        evaluations: list[EvaluationMetadataPayload] = []
        if isinstance(evaluations_raw, Sequence) and not isinstance(
            evaluations_raw, (str, bytes, bytearray)
        ):
            for entry in evaluations_raw:
                if isinstance(entry, Mapping):
                    evaluation = EvaluationMetadataPayload.from_mapping(entry)
                    if evaluation is not None:
                        evaluations.append(evaluation)
        return cls(
            symbol=str(symbol_value) if isinstance(symbol_value, str) else None,
            timeframe=str(timeframe_value) if isinstance(timeframe_value, str) else None,
            source_signal_id=(
                str(source_signal_value)
                if isinstance(source_signal_value, str)
                else None
            ),
            evaluations=tuple(evaluations),
            note=str(note_value) if isinstance(note_value, str) else None,
        )


@dataclass(slots=True)
class TradeMetadataPayload:
    mode: str | None = None
    timeframe: str | None = None
    note: str | None = None
    deposit_snapshot: DepositSnapshotMetadataPayload | None = None
    backtest: BacktestMetadataPayload | None = None
    live: LiveMetadataPayload | None = None

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "TradeMetadataPayload" | None:
        if raw is None:
            return None
        deposit_raw = raw.get("deposit_snapshot")
        backtest_raw = raw.get("backtest")
        live_raw = raw.get("live")
        note_value = raw.get("note")
        mode_value = raw.get("mode")
        timeframe_value = raw.get("timeframe")
        return cls(
            mode=str(mode_value) if isinstance(mode_value, str) else None,
            timeframe=str(timeframe_value) if isinstance(timeframe_value, str) else None,
            note=str(note_value) if isinstance(note_value, str) else None,
            deposit_snapshot=DepositSnapshotMetadataPayload.from_mapping(
                deposit_raw if isinstance(deposit_raw, Mapping) else None
            ),
            backtest=BacktestMetadataPayload.from_mapping(
                backtest_raw if isinstance(backtest_raw, Mapping) else None
            ),
            live=LiveMetadataPayload.from_mapping(
                live_raw if isinstance(live_raw, Mapping) else None
            ),
        )


# ---------------------------------------------------------------------------
# Domain metadata dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ThresholdsMetadata(_SerializableDataclass):
    timeframe: Timeframe | None = None
    timeframe_raw: str | None = None
    short_pct_move_ranges: tuple[ThresholdRange, ...] = field(default_factory=tuple)
    short_relative_volume_ranges: tuple[ThresholdRange, ...] = field(default_factory=tuple)
    note: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.timeframe_raw is not None:
            data["timeframe"] = self.timeframe_raw
        elif self.timeframe is not None:
            data["timeframe"] = self.timeframe.value
        if self.short_pct_move_ranges:
            data["short_pct_move_ranges"] = [
                range_value.to_mapping()
                for range_value in self.short_pct_move_ranges
                if not range_value.is_empty()
            ]
        if self.short_relative_volume_ranges:
            data["short_relative_volume_ranges"] = [
                range_value.to_mapping()
                for range_value in self.short_relative_volume_ranges
                if not range_value.is_empty()
            ]
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_mapping(
        cls, payload: ThresholdsMetadataPayload | None
    ) -> "ThresholdsMetadata" | None:
        if payload is None:
            return None
        if (
            payload.timeframe is None
            and payload.timeframe_raw is None
            and not payload.short_pct_move_ranges
            and not payload.short_relative_volume_ranges
            and payload.note is None
        ):
            return None
        return cls(
            timeframe=payload.timeframe,
            timeframe_raw=payload.timeframe_raw,
            short_pct_move_ranges=payload.short_pct_move_ranges,
            short_relative_volume_ranges=payload.short_relative_volume_ranges,
            note=payload.note,
        )


@dataclass(slots=True)
class ThresholdMetricMetadata(_SerializableDataclass):
    name: str
    min_value: float | None = None
    max_value: float | None = None
    min_abs_value: float | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name}
        if self.min_value is not None:
            data["min_value"] = self.min_value
        if self.max_value is not None:
            data["max_value"] = self.max_value
        if self.min_abs_value is not None:
            data["min_abs_value"] = self.min_abs_value
        return data

    @classmethod
    def from_mapping(
        cls, payload: ThresholdMetricMetadataPayload | None
    ) -> "ThresholdMetricMetadata" | None:
        if payload is None:
            return None
        return cls(
            name=payload.name,
            min_value=payload.min_value,
            max_value=payload.max_value,
            min_abs_value=payload.min_abs_value,
        )

    @classmethod
    def from_threshold(
        cls, threshold: "ThresholdMetric" | None
    ) -> "ThresholdMetricMetadata" | None:
        if threshold is None:
            return None
        return cls(
            name=threshold.name,
            min_value=threshold.min_value,
            max_value=threshold.max_value,
            min_abs_value=threshold.min_abs_value,
        )


@dataclass(slots=True)
class EvaluationMetadata(_SerializableDataclass):
    name: str
    value: float | None = None
    passed: bool | None = None
    threshold: ThresholdMetricMetadata | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name}
        if self.value is not None:
            data["value"] = self.value
        if self.passed is not None:
            data["passed"] = self.passed
        if self.threshold is not None:
            data["threshold"] = self.threshold.to_dict()
        return data

    @classmethod
    def from_mapping(
        cls, payload: EvaluationMetadataPayload | None
    ) -> "EvaluationMetadata" | None:
        if payload is None:
            return None
        return cls(
            name=payload.name,
            value=payload.value,
            passed=payload.passed,
            threshold=ThresholdMetricMetadata.from_mapping(payload.threshold),
        )


@dataclass(slots=True)
class DepositSnapshotMetadata(_SerializableDataclass):
    asset: str | None = None
    balance: float | None = None
    updated_at: datetime | None = None
    raw: Dict[str, Any] | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.asset is not None:
            data["asset"] = self.asset
        if self.balance is not None:
            data["balance"] = self.balance
        if self.updated_at is not None:
            data["updated_at"] = self.updated_at.isoformat()
        if self.raw is not None:
            data["raw"] = self.raw
        return data

    @classmethod
    def from_mapping(
        cls, payload: DepositSnapshotMetadataPayload | None
    ) -> "DepositSnapshotMetadata" | None:
        if payload is None:
            return None
        if (
            payload.asset is None
            and payload.balance is None
            and payload.updated_at is None
            and payload.raw is None
        ):
            return None
        return cls(
            asset=payload.asset,
            balance=payload.balance,
            updated_at=payload.updated_at,
            raw=payload.raw,
        )


@dataclass(slots=True)
class BacktestMetadata(_SerializableDataclass):
    result_pct: float | None = None
    pct_to_high_break: float | None = None
    pct_to_low_break: float | None = None
    note: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.result_pct is not None:
            data["result_pct"] = self.result_pct
        if self.pct_to_high_break is not None:
            data["pct_to_high_break"] = self.pct_to_high_break
        if self.pct_to_low_break is not None:
            data["pct_to_low_break"] = self.pct_to_low_break
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_mapping(
        cls, payload: BacktestMetadataPayload | None
    ) -> "BacktestMetadata" | None:
        if payload is None:
            return None
        if (
            payload.result_pct is None
            and payload.pct_to_high_break is None
            and payload.pct_to_low_break is None
            and payload.note is None
        ):
            return None
        return cls(
            result_pct=payload.result_pct,
            pct_to_high_break=payload.pct_to_high_break,
            pct_to_low_break=payload.pct_to_low_break,
            note=payload.note,
        )


@dataclass(slots=True)
class LiveMetadata(_SerializableDataclass):
    result_pct: float | None = None
    closed_status: TradeStatus | None = None
    note: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.result_pct is not None:
            data["result_pct"] = self.result_pct
        if self.closed_status is not None:
            data["closed_status"] = self.closed_status.value
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_mapping(
        cls, payload: LiveMetadataPayload | None
    ) -> "LiveMetadata" | None:
        if payload is None:
            return None
        if payload.result_pct is None and payload.closed_status is None and payload.note is None:
            return None
        return cls(
            result_pct=payload.result_pct,
            closed_status=payload.closed_status,
            note=payload.note,
        )


@dataclass(slots=True)
class SignalMetadata(_SerializableDataclass):
    symbol: str | None = None
    timeframe: str | None = None
    source_signal_id: str | None = None
    evaluations: list[EvaluationMetadata] = field(default_factory=list)
    note: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.symbol is not None:
            data["symbol"] = self.symbol
        if self.timeframe is not None:
            data["timeframe"] = self.timeframe
        if self.source_signal_id is not None:
            data["source_signal_id"] = self.source_signal_id
        if self.evaluations:
            data["evaluations"] = [evaluation.to_dict() for evaluation in self.evaluations]
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_mapping(
        cls, payload: SignalMetadataPayload | None
    ) -> "SignalMetadata" | None:
        if payload is None:
            return None
        evaluations = [
            evaluation
            for evaluation in (
                EvaluationMetadata.from_mapping(item)
                for item in payload.evaluations
            )
            if evaluation is not None
        ]
        if (
            payload.symbol is None
            and payload.timeframe is None
            and payload.source_signal_id is None
            and not evaluations
            and payload.note is None
        ):
            return None
        return cls(
            symbol=payload.symbol,
            timeframe=payload.timeframe,
            source_signal_id=payload.source_signal_id,
            evaluations=evaluations,
            note=payload.note,
        )


@dataclass(slots=True)
class TradeMetadata(_SerializableDataclass):
    mode: str | None = None
    timeframe: str | None = None
    deposit_snapshot: DepositSnapshotMetadata | None = None
    backtest: BacktestMetadata | None = None
    live: LiveMetadata | None = None
    note: str | None = None

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.mode is not None:
            data["mode"] = self.mode
        if self.timeframe is not None:
            data["timeframe"] = self.timeframe
        if self.deposit_snapshot is not None:
            data["deposit_snapshot"] = self.deposit_snapshot.to_dict()
        if self.backtest is not None:
            data["backtest"] = self.backtest.to_dict()
        if self.live is not None:
            data["live"] = self.live.to_dict()
        if self.note is not None:
            data["note"] = self.note
        return data

    @classmethod
    def from_mapping(
        cls, payload: TradeMetadataPayload | None
    ) -> "TradeMetadata" | None:
        if payload is None:
            return None
        deposit_snapshot = DepositSnapshotMetadata.from_mapping(payload.deposit_snapshot)
        backtest = BacktestMetadata.from_mapping(payload.backtest)
        live = LiveMetadata.from_mapping(payload.live)
        if (
            payload.mode is None
            and payload.timeframe is None
            and deposit_snapshot is None
            and backtest is None
            and live is None
            and payload.note is None
        ):
            return None
        return cls(
            mode=payload.mode,
            timeframe=payload.timeframe,
            deposit_snapshot=deposit_snapshot,
            backtest=backtest,
            live=live,
            note=payload.note,
        )
