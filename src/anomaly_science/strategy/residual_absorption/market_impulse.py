"""Causal broad-market factor and impulse detection without ML or PnL."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from anomaly_science.contracts.time import SnapshotTiming
from anomaly_science.market_context.sessions import session_instance_for_ms
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    MarketImpulseSpec,
)


class MarketImpulseContractError(ValueError):
    """Raised when market impulse inputs violate their point-in-time contract."""


@dataclass(frozen=True, slots=True)
class MarketFactorSnapshotRow:
    schema_version: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    utc_day: int
    session_seq: int
    session_name: str
    cross_section_symbol_count: int
    alt_median_return_5m: float
    alt_median_return_15m: float
    alt_median_return_30m: float
    directional_breadth_up_15m: float
    directional_breadth_down_15m: float
    btc_return_15m: float | None
    eth_return_15m: float | None
    baseline_snapshot_count: int
    factor_baseline_median_15m: float | None
    factor_baseline_mad_15m: float | None
    factor_robust_z_15m: float | None
    direction: int
    reference_support_count: int
    market_impulse_candidate: bool
    reference_confirmed_candidate: bool

    def __post_init__(self) -> None:
        SnapshotTiming(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if self.direction not in (-1, 0, 1):
            raise MarketImpulseContractError("direction must be -1, 0, or 1")
        if not 0.0 <= self.directional_breadth_up_15m <= 1.0:
            raise MarketImpulseContractError("up breadth must be in [0, 1]")
        if not 0.0 <= self.directional_breadth_down_15m <= 1.0:
            raise MarketImpulseContractError("down breadth must be in [0, 1]")
        if self.market_impulse_candidate and self.direction == 0:
            raise MarketImpulseContractError("an impulse candidate requires direction")
        if self.reference_confirmed_candidate and not self.market_impulse_candidate:
            raise MarketImpulseContractError("reference confirmation requires an impulse")


@dataclass(frozen=True, slots=True)
class MarketImpulseEvent:
    event_id: str
    schema_version: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    utc_day: int
    session_seq: int
    session_name: str
    direction: int
    factor_return_15m: float
    factor_robust_z_15m: float
    directional_breadth_15m: float
    cross_section_symbol_count: int
    reference_support_count: int
    reference_confirmed: bool

    def __post_init__(self) -> None:
        if not self.event_id:
            raise MarketImpulseContractError("event_id is required")
        SnapshotTiming(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if self.direction not in (-1, 1):
            raise MarketImpulseContractError("market impulse event direction must be -1 or 1")


def build_market_factor_snapshots(
    cross_section_returns: pd.DataFrame,
    *,
    spec: MarketImpulseSpec = MarketImpulseSpec(),
) -> list[MarketFactorSnapshotRow]:
    """Build a broad alt median factor with a prior-session-only robust baseline."""

    required = {
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "core_eligible",
        *{f"return_{window}m" for window in spec.factor_return_windows_minutes},
    }
    missing = sorted(required.difference(cross_section_returns.columns))
    if missing:
        raise MarketImpulseContractError(f"cross-section returns missing columns: {missing}")
    if cross_section_returns.empty:
        return []
    frame = cross_section_returns.loc[:, sorted(required)].copy()
    frame["symbol"] = frame["symbol"].astype(str)
    for column in required.difference({"symbol", "core_eligible"}):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    if bool(frame.duplicated(["symbol", "snapshot_time_ms"]).any()):
        raise MarketImpulseContractError("symbol/snapshot rows must be unique")
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise MarketImpulseContractError("feature cutoff exceeds snapshot")
    frame["core_eligible"] = _explicit_bool(frame["core_eligible"])

    raw_rows: list[dict[str, object]] = []
    references = set(spec.reference_symbols)
    for snapshot, group in frame.groupby("snapshot_time_ms", sort=True):
        snapshot_ms = int(snapshot)
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(snapshot_ms)
        core = group.loc[group["core_eligible"] & ~group["symbol"].isin(references)].copy()
        finite = np.ones(len(core), dtype=bool)
        for window in spec.factor_return_windows_minutes:
            finite &= np.isfinite(core[f"return_{window}m"].to_numpy(dtype=float))
        core = core.loc[finite]
        if core.empty:
            continue
        primary = core[f"return_{spec.primary_return_window_minutes}m"].to_numpy(dtype=float)
        instance = session_instance_for_ms(snapshot_ms)
        factor = {
            window: float(core[f"return_{window}m"].median())
            for window in spec.factor_return_windows_minutes
        }
        reference_returns = {
            symbol: _symbol_value(
                group,
                symbol=symbol,
                column=f"return_{spec.primary_return_window_minutes}m",
            )
            for symbol in spec.reference_symbols
        }
        raw_rows.append(
            {
                "snapshot_time_ms": snapshot_ms,
                "feature_cutoff_time_ms": int(group["feature_cutoff_time_ms"].max()),
                "utc_day": instance.utc_day,
                "session_seq": instance.block.seq,
                "session_name": instance.block.name,
                "cross_section_symbol_count": len(core),
                "factor": factor,
                "breadth_up": float(np.mean(primary > 0.0)),
                "breadth_down": float(np.mean(primary < 0.0)),
                "reference_returns": reference_returns,
            }
        )

    history_by_seq: dict[int, OrderedDict[int, list[float]]] = {
        seq: OrderedDict() for seq in range(5)
    }
    output: list[MarketFactorSnapshotRow] = []
    primary_window = spec.primary_return_window_minutes
    for raw in raw_rows:
        seq = int(raw["session_seq"])
        day = int(raw["utc_day"])
        factor = dict(raw["factor"])
        current_factor = float(factor[primary_window])
        day_history = history_by_seq[seq]
        prior_values = [
            value
            for history_day, values in day_history.items()
            if history_day < day
            for value in values
        ]
        baseline_count = len(prior_values)
        baseline_median, baseline_mad, robust_z = _robust_z(
            current_factor,
            prior_values,
            minimum_count=spec.minimum_baseline_snapshots,
        )
        direction = 0 if robust_z is None or robust_z == 0.0 else (1 if robust_z > 0.0 else -1)
        directional_breadth = (
            float(raw["breadth_up"]) if direction > 0 else float(raw["breadth_down"])
        )
        candidate = bool(
            robust_z is not None
            and abs(robust_z) >= spec.primary_absolute_robust_z
            and int(raw["cross_section_symbol_count"]) >= spec.minimum_cross_section_symbols
            and directional_breadth >= spec.primary_directional_breadth
        )
        reference_returns = dict(raw["reference_returns"])
        support_count = sum(
            value is not None and direction * value > 0.0
            for value in reference_returns.values()
        ) if direction else 0
        output.append(
            MarketFactorSnapshotRow(
                schema_version=spec.schema_version,
                snapshot_time_ms=int(raw["snapshot_time_ms"]),
                feature_cutoff_time_ms=int(raw["feature_cutoff_time_ms"]),
                utc_day=day,
                session_seq=seq,
                session_name=str(raw["session_name"]),
                cross_section_symbol_count=int(raw["cross_section_symbol_count"]),
                alt_median_return_5m=float(factor[5]),
                alt_median_return_15m=float(factor[15]),
                alt_median_return_30m=float(factor[30]),
                directional_breadth_up_15m=float(raw["breadth_up"]),
                directional_breadth_down_15m=float(raw["breadth_down"]),
                btc_return_15m=reference_returns.get("BTCUSDT"),
                eth_return_15m=reference_returns.get("ETHUSDT"),
                baseline_snapshot_count=baseline_count,
                factor_baseline_median_15m=baseline_median,
                factor_baseline_mad_15m=baseline_mad,
                factor_robust_z_15m=robust_z,
                direction=direction,
                reference_support_count=support_count,
                market_impulse_candidate=candidate,
                reference_confirmed_candidate=candidate and support_count > 0,
            )
        )
        day_history.setdefault(day, []).append(current_factor)
        while len(day_history) > spec.same_type_baseline_sessions + 1:
            day_history.popitem(last=False)
    return output


def detect_market_impulse_events(
    snapshots: list[MarketFactorSnapshotRow],
    *,
    spec: MarketImpulseSpec = MarketImpulseSpec(),
) -> list[MarketImpulseEvent]:
    """Collapse causally adjacent trigger snapshots into one event chain."""

    events: list[MarketImpulseEvent] = []
    last_candidate_time: int | None = None
    active_direction: int | None = None
    cooldown_ms = spec.event_cooldown_minutes * 60_000
    for row in sorted(snapshots, key=lambda item: item.snapshot_time_ms):
        if not row.market_impulse_candidate:
            continue
        same_chain = bool(
            last_candidate_time is not None
            and active_direction == row.direction
            and row.snapshot_time_ms - last_candidate_time <= cooldown_ms
        )
        last_candidate_time = row.snapshot_time_ms
        if same_chain:
            continue
        active_direction = row.direction
        breadth = (
            row.directional_breadth_up_15m
            if row.direction > 0
            else row.directional_breadth_down_15m
        )
        assert row.factor_robust_z_15m is not None
        events.append(
            MarketImpulseEvent(
                event_id=f"market_impulse:{row.snapshot_time_ms}:{row.direction:+d}",
                schema_version=spec.schema_version,
                snapshot_time_ms=row.snapshot_time_ms,
                feature_cutoff_time_ms=row.feature_cutoff_time_ms,
                utc_day=row.utc_day,
                session_seq=row.session_seq,
                session_name=row.session_name,
                direction=row.direction,
                factor_return_15m=row.alt_median_return_15m,
                factor_robust_z_15m=row.factor_robust_z_15m,
                directional_breadth_15m=breadth,
                cross_section_symbol_count=row.cross_section_symbol_count,
                reference_support_count=row.reference_support_count,
                reference_confirmed=row.reference_confirmed_candidate,
            )
        )
    return events


def _robust_z(
    value: float,
    history: list[float],
    *,
    minimum_count: int,
) -> tuple[float | None, float | None, float | None]:
    finite = np.asarray([item for item in history if math.isfinite(item)], dtype=float)
    if len(finite) < minimum_count:
        return None, None, None
    median = float(np.median(finite))
    mad = float(np.median(np.abs(finite - median)))
    scale = 1.4826 * mad
    if scale <= 0.0:
        return median, mad, None
    return median, mad, float((value - median) / scale)


def _symbol_value(frame: pd.DataFrame, *, symbol: str, column: str) -> float | None:
    values = frame.loc[frame["symbol"].eq(symbol), column]
    if len(values) != 1:
        return None
    value = float(values.iloc[0])
    return value if math.isfinite(value) else None


def _explicit_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin({"true", "false"}).all():
        raise MarketImpulseContractError("core_eligible must contain explicit booleans")
    return normalized.eq("true")


__all__ = [
    "MarketFactorSnapshotRow",
    "MarketImpulseContractError",
    "MarketImpulseEvent",
    "build_market_factor_snapshots",
    "detect_market_impulse_events",
]
