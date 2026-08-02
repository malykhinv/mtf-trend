"""Leave-one-out residual response profiles frozen at market-impulse time."""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from anomaly_science.contracts.time import SnapshotTiming
from anomaly_science.market_context.sessions import session_instance_for_ms
from anomaly_science.strategy.residual_absorption.market_impulse import MarketImpulseEvent
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    ResidualResponseSpec,
)


class ResidualResponseContractError(ValueError):
    """Raised when a residual response would use an invalid factor or time view."""


@dataclass(frozen=True, slots=True)
class ResidualResponseProfileRow:
    schema_version: str
    event_id: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    session_seq: int
    session_name: str
    impulse_direction: int
    history_observation_count: int
    short_history_observation_count: int
    beta_15m: float | None
    beta_short_15m: float | None
    beta_recent_minus_long: float | None
    alpha_15m: float | None
    factor_correlation_15m: float | None
    factor_r_squared_15m: float | None
    current_symbol_return_15m: float
    current_leave_one_out_factor_return_15m: float
    expected_symbol_return_15m: float | None
    response_residual_15m: float | None
    direction_adjusted_residual_15m: float | None
    direction_adjusted_underreaction_15m: float | None
    factor_excludes_symbol: bool

    def __post_init__(self) -> None:
        if not self.event_id or not self.symbol:
            raise ResidualResponseContractError("event_id and symbol are required")
        SnapshotTiming(
            snapshot_time_ms=self.snapshot_time_ms,
            feature_cutoff_time_ms=self.feature_cutoff_time_ms,
        )
        if self.impulse_direction not in (-1, 1):
            raise ResidualResponseContractError("impulse_direction must be -1 or 1")
        if not self.factor_excludes_symbol:
            raise ResidualResponseContractError("residual factor must exclude its own symbol")


def build_residual_response_profiles(
    cross_section_returns: pd.DataFrame,
    *,
    events: list[MarketImpulseEvent],
    spec: ResidualResponseSpec = ResidualResponseSpec(),
) -> list[ResidualResponseProfileRow]:
    """Estimate response profiles from prior completed same-type sessions only."""

    return_column = f"return_{spec.response_window_minutes}m"
    required = {
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "core_eligible",
        return_column,
    }
    missing = sorted(required.difference(cross_section_returns.columns))
    if missing:
        raise ResidualResponseContractError(f"cross-section returns missing columns: {missing}")
    if not events or cross_section_returns.empty:
        return []
    frame = cross_section_returns.copy()
    frame["symbol"] = frame["symbol"].astype(str)
    frame["snapshot_time_ms"] = pd.to_numeric(frame["snapshot_time_ms"], errors="raise").astype("int64")
    frame["feature_cutoff_time_ms"] = pd.to_numeric(
        frame["feature_cutoff_time_ms"], errors="raise"
    ).astype("int64")
    frame[return_column] = pd.to_numeric(frame[return_column], errors="coerce")
    frame["core_eligible"] = _explicit_bool(frame["core_eligible"], "core_eligible")
    if "candidate_eligible" in frame.columns:
        frame["candidate_eligible"] = _explicit_bool(frame["candidate_eligible"], "candidate_eligible")
    else:
        frame["candidate_eligible"] = frame["core_eligible"]
    if bool(frame.duplicated(["symbol", "snapshot_time_ms"]).any()):
        raise ResidualResponseContractError("symbol/snapshot rows must be unique")
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise ResidualResponseContractError("feature cutoff exceeds snapshot")
    for timestamp_ms in frame["snapshot_time_ms"].unique():
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(int(timestamp_ms))

    frame = _attach_session_coordinates(frame)
    factor_by_symbol_snapshot = _leave_one_out_factor(
        frame,
        return_column=return_column,
        reference_symbols=set(spec.reference_symbols),
    )
    work = frame.merge(
        factor_by_symbol_snapshot,
        on=["symbol", "snapshot_time_ms"],
        how="left",
        validate="one_to_one",
    )

    output: list[ResidualResponseProfileRow] = []
    for event in sorted(events, key=lambda item: item.snapshot_time_ms):
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(event.snapshot_time_ms)
        current = work.loc[
            work["snapshot_time_ms"].eq(event.snapshot_time_ms)
            & work["candidate_eligible"]
            & ~work["symbol"].isin(spec.reference_symbols)
        ]
        for raw in current.sort_values("symbol").itertuples(index=False):
            current_factor = float(raw.leave_one_out_factor_return)
            current_return = float(getattr(raw, return_column))
            if not (math.isfinite(current_factor) and math.isfinite(current_return)):
                continue
            symbol_history = work.loc[
                work["symbol"].eq(raw.symbol)
                & work["session_seq"].eq(event.session_seq)
                & work["utc_day"].lt(event.utc_day)
                & work["snapshot_time_ms"].lt(event.snapshot_time_ms)
            ].copy()
            long_history = _last_session_days(
                symbol_history,
                limit=spec.beta_history_same_type_sessions,
            )
            short_history = _last_session_days(
                symbol_history,
                limit=spec.beta_short_history_sessions,
            )
            long_fit = _linear_fit(
                long_history["leave_one_out_factor_return"].to_numpy(dtype=float),
                long_history[return_column].to_numpy(dtype=float),
                minimum=spec.minimum_beta_observations,
            )
            short_fit = _linear_fit(
                short_history["leave_one_out_factor_return"].to_numpy(dtype=float),
                short_history[return_column].to_numpy(dtype=float),
                minimum=spec.minimum_short_beta_observations,
            )
            beta, alpha, correlation, count = long_fit
            short_beta, _, _, short_count = short_fit
            expected = None if beta is None or alpha is None else alpha + beta * current_factor
            residual = None if expected is None else current_return - expected
            adjusted = None if residual is None else event.direction * residual
            output.append(
                ResidualResponseProfileRow(
                    schema_version=spec.schema_version,
                    event_id=event.event_id,
                    symbol=str(raw.symbol),
                    snapshot_time_ms=event.snapshot_time_ms,
                    feature_cutoff_time_ms=min(
                        event.snapshot_time_ms,
                        max(event.feature_cutoff_time_ms, int(raw.feature_cutoff_time_ms)),
                    ),
                    session_seq=event.session_seq,
                    session_name=event.session_name,
                    impulse_direction=event.direction,
                    history_observation_count=count,
                    short_history_observation_count=short_count,
                    beta_15m=beta,
                    beta_short_15m=short_beta,
                    beta_recent_minus_long=(
                        None if beta is None or short_beta is None else short_beta - beta
                    ),
                    alpha_15m=alpha,
                    factor_correlation_15m=correlation,
                    factor_r_squared_15m=(None if correlation is None else correlation**2),
                    current_symbol_return_15m=current_return,
                    current_leave_one_out_factor_return_15m=current_factor,
                    expected_symbol_return_15m=expected,
                    response_residual_15m=residual,
                    direction_adjusted_residual_15m=adjusted,
                    direction_adjusted_underreaction_15m=(
                        None if adjusted is None else -adjusted
                    ),
                    factor_excludes_symbol=True,
                )
            )
    return output


def _attach_session_coordinates(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    instances = [session_instance_for_ms(int(value)) for value in result["snapshot_time_ms"]]
    result["utc_day"] = [item.utc_day for item in instances]
    result["session_seq"] = [item.block.seq for item in instances]
    return result


def _leave_one_out_factor(
    frame: pd.DataFrame,
    *,
    return_column: str,
    reference_symbols: set[str],
) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for snapshot, group in frame.groupby("snapshot_time_ms", sort=True):
        core = group.loc[
            group["core_eligible"]
            & ~group["symbol"].isin(reference_symbols)
            & group[return_column].notna()
        ]
        core_values = dict(zip(core["symbol"], core[return_column], strict=True))
        for symbol in group["symbol"]:
            values = np.asarray(
                [float(value) for item, value in core_values.items() if item != symbol],
                dtype=float,
            )
            factor = float(np.median(values)) if len(values) else float("nan")
            rows.append(
                {
                    "symbol": str(symbol),
                    "snapshot_time_ms": int(snapshot),
                    "leave_one_out_factor_return": factor,
                }
            )
    return pd.DataFrame(rows)


def _last_session_days(frame: pd.DataFrame, *, limit: int) -> pd.DataFrame:
    if frame.empty:
        return frame
    days = sorted(frame["utc_day"].unique(), reverse=True)[:limit]
    return frame.loc[frame["utc_day"].isin(days)].copy()


def _linear_fit(
    factor: np.ndarray,
    response: np.ndarray,
    *,
    minimum: int,
) -> tuple[float | None, float | None, float | None, int]:
    valid = np.isfinite(factor) & np.isfinite(response)
    x = factor[valid]
    y = response[valid]
    count = len(x)
    if count < minimum:
        return None, None, None, count
    x_centered = x - np.mean(x)
    y_centered = y - np.mean(y)
    denominator = float(np.dot(x_centered, x_centered))
    y_variance = float(np.dot(y_centered, y_centered))
    if denominator <= 0.0 or y_variance <= 0.0:
        return None, None, None, count
    covariance_sum = float(np.dot(x_centered, y_centered))
    beta = covariance_sum / denominator
    alpha = float(np.mean(y) - beta * np.mean(x))
    correlation = covariance_sum / math.sqrt(denominator * y_variance)
    return float(beta), alpha, float(np.clip(correlation, -1.0, 1.0)), count


def _explicit_bool(series: pd.Series, name: str) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin({"true", "false"}).all():
        raise ResidualResponseContractError(f"{name} must contain explicit booleans")
    return normalized.eq("true")


__all__ = [
    "ResidualResponseContractError",
    "ResidualResponseProfileRow",
    "build_residual_response_profiles",
]
