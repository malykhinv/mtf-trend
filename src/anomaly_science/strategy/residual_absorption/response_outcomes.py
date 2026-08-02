"""Future residual-response outcomes; never imported into online feature code."""

from __future__ import annotations

import numpy as np
import pandas as pd

from anomaly_science.strategy.residual_absorption.residual_response import (
    ResidualResponseProfileRow,
)
from anomaly_science.strategy.residual_absorption.response_memory import ResponseMemoryRecord
from anomaly_science.strategy.residual_absorption.spec import (
    RESIDUAL_ABSORPTION_RESEARCH_SPLIT,
    ResponseOutcomeSpec,
)


class ResponseOutcomeContractError(ValueError):
    """Raised when future response paths cannot be built without contamination."""


def build_response_outcome_records(
    prices_5m: pd.DataFrame,
    *,
    profiles: list[ResidualResponseProfileRow],
    spec: ResponseOutcomeSpec = ResponseOutcomeSpec(),
) -> list[ResponseMemoryRecord]:
    """Resolve future residual paths using event-time-frozen factor membership/beta."""

    required = {
        "symbol",
        "snapshot_time_ms",
        "feature_cutoff_time_ms",
        "close",
        "core_eligible",
    }
    missing = sorted(required.difference(prices_5m.columns))
    if missing:
        raise ResponseOutcomeContractError(f"5m prices missing columns: {missing}")
    if not profiles:
        return []
    frame = prices_5m.loc[:, sorted(required)].copy()
    frame["symbol"] = frame["symbol"].astype(str)
    for column in ("snapshot_time_ms", "feature_cutoff_time_ms", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["core_eligible"] = _explicit_bool(frame["core_eligible"])
    if bool(frame.duplicated(["symbol", "snapshot_time_ms"]).any()):
        raise ResponseOutcomeContractError("symbol/snapshot prices must be unique")
    if bool((frame["feature_cutoff_time_ms"] > frame["snapshot_time_ms"]).any()):
        raise ResponseOutcomeContractError("future price feature cutoff exceeds snapshot")
    if bool((frame["close"] <= 0.0).any()):
        raise ResponseOutcomeContractError("future prices must be positive")
    for timestamp in frame["snapshot_time_ms"].unique():
        RESIDUAL_ABSORPTION_RESEARCH_SPLIT.require_is_timestamp_ms(int(timestamp))

    records: list[ResponseMemoryRecord] = []
    interval_ms = spec.path_interval_minutes * 60_000
    max_horizon = max(spec.horizons_minutes)
    for profile in sorted(profiles, key=lambda item: (item.snapshot_time_ms, item.symbol)):
        initial = profile.direction_adjusted_underreaction_15m
        if profile.beta_15m is None or initial is None:
            records.append(_unresolved_record(profile))
            continue
        resolution_time = profile.snapshot_time_ms + max_horizon * 60_000
        if resolution_time >= RESIDUAL_ABSORPTION_RESEARCH_SPLIT.oos_start_time_ms:
            records.append(_unresolved_record(profile))
            continue
        event_rows = frame.loc[frame["snapshot_time_ms"].eq(profile.snapshot_time_ms)]
        event_prices = dict(zip(event_rows["symbol"], event_rows["close"], strict=True))
        frozen_core = tuple(
            sorted(
                event_rows.loc[
                    event_rows["core_eligible"]
                    & ~event_rows["symbol"].isin(spec.reference_symbols),
                    "symbol",
                ].astype(str)
            )
        )
        if profile.symbol not in event_prices:
            records.append(_unresolved_record(profile))
            continue

        path_times = np.arange(
            profile.snapshot_time_ms + interval_ms,
            resolution_time + interval_ms,
            interval_ms,
            dtype=np.int64,
        )
        catchup_by_time: dict[int, float] = {}
        terminal_by_time: dict[int, float] = {}
        complete = True
        for path_time in path_times:
            path_rows = frame.loc[frame["snapshot_time_ms"].eq(int(path_time))]
            path_prices = dict(zip(path_rows["symbol"], path_rows["close"], strict=True))
            if profile.symbol not in path_prices:
                complete = False
                break
            factor_returns = [
                float(path_prices[symbol] / event_prices[symbol] - 1.0)
                for symbol in frozen_core
                if symbol != profile.symbol
                and symbol in path_prices
                and symbol in event_prices
            ]
            if len(factor_returns) < spec.minimum_factor_symbols:
                complete = False
                break
            symbol_return = float(
                path_prices[profile.symbol] / event_prices[profile.symbol] - 1.0
            )
            factor_return = float(np.median(factor_returns))
            catchup = profile.impulse_direction * (
                symbol_return - profile.beta_15m * factor_return
            )
            catchup_by_time[int(path_time)] = catchup
            initial_adjusted_residual = -initial
            terminal_by_time[int(path_time)] = initial_adjusted_residual + catchup
        if not complete or len(catchup_by_time) != len(path_times):
            records.append(_unresolved_record(profile))
            continue

        horizon_values = {
            horizon: catchup_by_time[profile.snapshot_time_ms + horizon * 60_000]
            for horizon in spec.horizons_minutes
        }
        terminal_values = np.asarray(list(terminal_by_time.values()), dtype=float)
        catchup_values = np.asarray(list(catchup_by_time.values()), dtype=float)
        half_threshold = 0.5 * initial
        half_times = [
            (timestamp - profile.snapshot_time_ms) / 60_000.0
            for timestamp, value in catchup_by_time.items()
            if initial > 0.0 and value >= half_threshold
        ]
        initial_adjusted_residual = -initial
        zero_crossed = bool(
            np.any(terminal_values >= 0.0)
            if initial_adjusted_residual < 0.0
            else np.any(terminal_values <= 0.0)
        )
        records.append(
            ResponseMemoryRecord(
                event_id=profile.event_id,
                symbol=profile.symbol,
                event_snapshot_time_ms=profile.snapshot_time_ms,
                impulse_direction=profile.impulse_direction,
                session_seq=profile.session_seq,
                initial_direction_adjusted_underreaction_15m=initial,
                resolution_time_ms=resolution_time,
                catchup_residual_change_15m=horizon_values[15],
                catchup_residual_change_30m=horizon_values[30],
                catchup_residual_change_60m=horizon_values[60],
                catchup_residual_change_120m=horizon_values[120],
                terminal_direction_adjusted_residual_120m=terminal_values[-1],
                maximum_catchup_residual_change_120m=float(np.max(catchup_values)),
                zero_crossed_expected_response_120m=zero_crossed,
                time_to_half_catchup_minutes=(min(half_times) if half_times else None),
            )
        )
    return records


def _unresolved_record(profile: ResidualResponseProfileRow) -> ResponseMemoryRecord:
    initial = profile.direction_adjusted_underreaction_15m
    return ResponseMemoryRecord(
        event_id=profile.event_id,
        symbol=profile.symbol,
        event_snapshot_time_ms=profile.snapshot_time_ms,
        impulse_direction=profile.impulse_direction,
        session_seq=profile.session_seq,
        initial_direction_adjusted_underreaction_15m=initial,
        resolution_time_ms=None,
        catchup_residual_change_15m=None,
        catchup_residual_change_30m=None,
        catchup_residual_change_60m=None,
        catchup_residual_change_120m=None,
        terminal_direction_adjusted_residual_120m=None,
        maximum_catchup_residual_change_120m=None,
        zero_crossed_expected_response_120m=None,
        time_to_half_catchup_minutes=None,
    )


def _explicit_bool(series: pd.Series) -> pd.Series:
    if pd.api.types.is_bool_dtype(series.dtype):
        return series.astype(bool)
    normalized = series.astype(str).str.strip().str.lower()
    if not normalized.isin({"true", "false"}).all():
        raise ResponseOutcomeContractError("core_eligible must contain explicit booleans")
    return normalized.eq("true")


__all__ = [
    "ResponseOutcomeContractError",
    "build_response_outcome_records",
]
