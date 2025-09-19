from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping, Type, TypeVar

from ..enums import TradeStatus


T = TypeVar("T", bound="_SerializableDataclass")


class _SerializableDataclass:
    """Utility mixin for metadata dataclasses."""

    extra: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:  # pragma: no cover - overridden
        raise NotImplementedError

    @classmethod
    def from_mapping(cls: Type[T], raw: Mapping[str, Any] | None) -> T | None:
        raise NotImplementedError

@dataclass(slots=True)
class EvaluationMetadata(_SerializableDataclass):
    name: str
    value: float | None = None
    passed: bool | None = None
    threshold: Dict[str, Any] | None = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {"name": self.name}
        if self.value is not None:
            data["value"] = self.value
        if self.passed is not None:
            data["passed"] = self.passed
        if self.threshold is not None:
            data["threshold"] = self.threshold
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "EvaluationMetadata" | None:
        if not raw or "name" not in raw:
            return None
        known = {"name", "value", "passed", "threshold"}
        extra = {key: raw[key] for key in raw.keys() - known}
        threshold = raw.get("threshold")
        threshold_dict = dict(threshold) if isinstance(threshold, Mapping) else None
        value = raw.get("value")
        try:
            value_f = float(value) if value is not None else None
        except (TypeError, ValueError):
            value_f = None
        passed = raw.get("passed")
        passed_bool = None
        if isinstance(passed, bool):
            passed_bool = passed
        elif passed is not None:
            passed_bool = bool(passed)
        return cls(
            name=str(raw["name"]),
            value=value_f,
            passed=passed_bool,
            threshold=threshold_dict,
            extra=extra,
        )


@dataclass(slots=True)
class DepositSnapshotMetadata(_SerializableDataclass):
    asset: str | None = None
    balance: float | None = None
    updated_at: datetime | None = None
    raw: Dict[str, Any] | None = None
    extra: Dict[str, Any] = field(default_factory=dict)

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
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any] | None
    ) -> "DepositSnapshotMetadata" | None:
        if not raw:
            return None
        known = {"asset", "balance", "updated_at", "raw"}
        extra = {key: raw[key] for key in raw.keys() - known}
        updated_at_value = raw.get("updated_at")
        updated_at = None
        if isinstance(updated_at_value, str):
            try:
                updated_at = datetime.fromisoformat(updated_at_value)
            except ValueError:
                updated_at = None
        balance = raw.get("balance")
        try:
            balance_value = float(balance) if balance is not None else None
        except (TypeError, ValueError):
            balance_value = None
        asset_value = raw.get("asset")
        asset = str(asset_value) if asset_value is not None else None
        raw_snapshot = raw.get("raw")
        raw_dict = dict(raw_snapshot) if isinstance(raw_snapshot, Mapping) else None
        return cls(
            asset=asset,
            balance=balance_value,
            updated_at=updated_at,
            raw=raw_dict,
            extra=extra,
        )


@dataclass(slots=True)
class BacktestMetadata(_SerializableDataclass):
    result_pct: float | None = None
    pct_to_high_break: float | None = None
    pct_to_low_break: float | None = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.result_pct is not None:
            data["result_pct"] = self.result_pct
        if self.pct_to_high_break is not None:
            data["pct_to_high_break"] = self.pct_to_high_break
        if self.pct_to_low_break is not None:
            data["pct_to_low_break"] = self.pct_to_low_break
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "BacktestMetadata" | None:
        if not raw:
            return None
        known = {"result_pct", "pct_to_high_break", "pct_to_low_break"}
        extra = {key: raw[key] for key in raw.keys() - known}
        def _to_float(value: Any) -> float | None:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None

        return cls(
            result_pct=_to_float(raw.get("result_pct")),
            pct_to_high_break=_to_float(raw.get("pct_to_high_break")),
            pct_to_low_break=_to_float(raw.get("pct_to_low_break")),
            extra=extra,
        )


@dataclass(slots=True)
class LiveMetadata(_SerializableDataclass):
    result_pct: float | None = None
    closed_status: TradeStatus | None = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data: Dict[str, Any] = {}
        if self.result_pct is not None:
            data["result_pct"] = self.result_pct
        if self.closed_status is not None:
            data["closed_status"] = self.closed_status.value
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "LiveMetadata" | None:
        if not raw:
            return None
        known = {"result_pct", "closed_status"}
        extra = {key: raw[key] for key in raw.keys() - known}
        result_pct = raw.get("result_pct")
        try:
            result_pct_value = float(result_pct) if result_pct is not None else None
        except (TypeError, ValueError):
            result_pct_value = None
        status_raw = raw.get("closed_status")
        closed_status = None
        if isinstance(status_raw, str):
            try:
                closed_status = TradeStatus(status_raw)
            except ValueError:
                closed_status = None
        return cls(
            result_pct=result_pct_value,
            closed_status=closed_status,
            extra=extra,
        )


@dataclass(slots=True)
class SignalMetadata(_SerializableDataclass):
    symbol: str | None = None
    timeframe: str | None = None
    source_signal_id: str | None = None
    evaluations: list[EvaluationMetadata] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

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
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "SignalMetadata" | None:
        if not raw:
            return None
        symbol = raw.get("symbol")
        timeframe = raw.get("timeframe")
        source_signal_id = raw.get("source_signal_id")
        evaluations_raw = raw.get("evaluations")
        evaluations: list[EvaluationMetadata] = []
        if isinstance(evaluations_raw, list):
            for evaluation_raw in evaluations_raw:
                if isinstance(evaluation_raw, Mapping):
                    evaluation = EvaluationMetadata.from_mapping(evaluation_raw)
                    if evaluation is not None:
                        evaluations.append(evaluation)
        known = {"symbol", "timeframe", "source_signal_id", "evaluations"}
        extra = {key: raw[key] for key in raw.keys() - known}
        if (
            symbol is None
            and timeframe is None
            and source_signal_id is None
            and not evaluations
            and not extra
        ):
            return None
        return cls(
            symbol=str(symbol) if isinstance(symbol, str) else None,
            timeframe=str(timeframe) if isinstance(timeframe, str) else None,
            source_signal_id=str(source_signal_id) if isinstance(source_signal_id, str) else None,
            evaluations=evaluations,
            extra=extra,
        )


@dataclass(slots=True)
class TradeMetadata(_SerializableDataclass):
    mode: str | None = None
    timeframe: str | None = None
    deposit_snapshot: DepositSnapshotMetadata | None = None
    backtest: BacktestMetadata | None = None
    live: LiveMetadata | None = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def ensure_backtest(self) -> BacktestMetadata:
        if self.backtest is None:
            self.backtest = BacktestMetadata()
        return self.backtest

    def ensure_live(self) -> LiveMetadata:
        if self.live is None:
            self.live = LiveMetadata()
        return self.live

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
        if self.extra:
            data.update(self.extra)
        return data

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any] | None) -> "TradeMetadata" | None:
        if not raw:
            return None
        mode = raw.get("mode")
        timeframe = raw.get("timeframe")
        deposit_snapshot = DepositSnapshotMetadata.from_mapping(
            raw.get("deposit_snapshot"))
        backtest = BacktestMetadata.from_mapping(raw.get("backtest"))
        live = LiveMetadata.from_mapping(raw.get("live"))
        known = {"mode", "timeframe", "deposit_snapshot", "backtest", "live"}
        extra = {key: raw[key] for key in raw.keys() - known}
        if (
            mode is None
            and timeframe is None
            and deposit_snapshot is None
            and backtest is None
            and live is None
            and not extra
        ):
            return None
        return cls(
            mode=str(mode) if isinstance(mode, str) else None,
            timeframe=str(timeframe) if isinstance(timeframe, str) else None,
            deposit_snapshot=deposit_snapshot,
            backtest=backtest,
            live=live,
            extra=extra,
        )

