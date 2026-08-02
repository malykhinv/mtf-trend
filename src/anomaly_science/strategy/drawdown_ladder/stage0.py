"""Build the IS-only Stage-0 drawdown/recovery event study.

Candidate membership and future outcomes are physically separate.  Session
anchors and limit fills are causal; recovery paths begin with the next full
minute whose prices become available strictly after the fill snapshot.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable, Iterator

import numpy as np
import pandas as pd
import polars as pl

from anomaly_science.binance_vision_cache import is_delivery_contract_symbol
from anomaly_science.future.recovery import RecoveryPathIndex, RecoveryPathMeasurement
from anomaly_science.market_context.sessions import (
    MS_PER_MINUTE,
    UTC_SESSION_BLOCKS,
    UTC_SESSION_BY_SEQ,
)
from anomaly_science.strategy.drawdown_ladder.data import (
    last_open_time_ms_exclusive,
    read_is_symbol_minutes,
)
from anomaly_science.strategy.drawdown_ladder.analysis import build_stage0_recovery_summaries
from anomaly_science.strategy.drawdown_ladder.spec import DrawdownLadderStage0Spec


class DrawdownLadderBuildError(ValueError):
    """Raised when Stage 0 cannot satisfy its frozen protocol."""


LEVEL_CANDIDATE_COLUMNS: tuple[str, ...] = (
    "candidate_schema_version",
    "protocol_version",
    "protocol_freeze_id",
    "research_partition",
    "parent_event_id",
    "candidate_id",
    "symbol",
    "utc_day",
    "session_seq",
    "session_name",
    "anchor_snapshot_time_ms",
    "anchor_price",
    "feature_cutoff_time_ms",
    "snapshot_time_ms",
    "order_active_time_ms",
    "order_expiry_time_ms",
    "level_depth_pct",
    "limit_price",
    "trade_through_bps",
    "fill_bar_open_time_ms",
    "fill_observed_time_ms",
    "minutes_from_order_activation",
    "same_bar_max_drawdown_pct",
    "session_complete",
    "untouched_2026_row_used",
)

LADDER_STATE_CANDIDATE_COLUMNS: tuple[str, ...] = (
    "candidate_schema_version",
    "protocol_version",
    "protocol_freeze_id",
    "research_partition",
    "parent_event_id",
    "candidate_id",
    "symbol",
    "utc_day",
    "session_seq",
    "session_name",
    "anchor_snapshot_time_ms",
    "anchor_price",
    "feature_cutoff_time_ms",
    "snapshot_time_ms",
    "order_active_time_ms",
    "order_expiry_time_ms",
    "grid_step_pct",
    "filled_level_count",
    "deepest_filled_level_pct",
    "equal_notional_average_entry_price",
    "fill_bar_open_time_ms",
    "fill_observed_time_ms",
    "minutes_from_order_activation",
    "same_bar_max_drawdown_pct",
    "session_complete",
    "untouched_2026_row_used",
)

MIRRORED_RALLY_LEVEL_CANDIDATE_COLUMNS: tuple[str, ...] = tuple(
    "same_bar_max_rally_pct" if column == "same_bar_max_drawdown_pct" else column
    for column in LEVEL_CANDIDATE_COLUMNS
)
MIRRORED_RALLY_STATE_CANDIDATE_COLUMNS: tuple[str, ...] = tuple(
    "same_bar_max_rally_pct" if column == "same_bar_max_drawdown_pct" else column
    for column in LADDER_STATE_CANDIDATE_COLUMNS
)

_OUTCOME_BASE_COLUMNS: tuple[str, ...] = (
    "outcome_schema_version",
    "protocol_version",
    "protocol_freeze_id",
    "research_partition",
    "candidate_kind",
    "parent_event_id",
    "candidate_id",
    "symbol",
    "snapshot_time_ms",
    "feature_cutoff_time_ms",
    "future_start_time_ms",
    "maximum_horizon_minutes",
    "available_future_minutes",
    "horizon_complete",
    "censor_reason",
    "gross_break_even_reached",
    "time_to_gross_break_even_minutes",
)


def outcome_columns(spec: DrawdownLadderStage0Spec) -> tuple[str, ...]:
    cost_columns = tuple(
        name
        for cost in spec.round_trip_cost_bps
        for name in (
            f"break_even_{cost}bps_reached",
            f"time_to_break_even_{cost}bps_minutes",
        )
    )
    horizon_columns = tuple(
        name
        for horizon in spec.response_horizons_minutes
        for name in (
            f"label_available_{horizon}m",
            f"future_return_{horizon}m",
            f"future_max_return_{horizon}m",
            f"future_min_return_{horizon}m",
        )
    )
    return (*_OUTCOME_BASE_COLUMNS, *cost_columns, *horizon_columns, "untouched_2026_row_used")


def level_candidate_columns(spec: DrawdownLadderStage0Spec) -> tuple[str, ...]:
    return (
        LEVEL_CANDIDATE_COLUMNS
        if spec.study_side == "long"
        else MIRRORED_RALLY_LEVEL_CANDIDATE_COLUMNS
    )


def ladder_state_candidate_columns(
    spec: DrawdownLadderStage0Spec,
) -> tuple[str, ...]:
    return (
        LADDER_STATE_CANDIDATE_COLUMNS
        if spec.study_side == "long"
        else MIRRORED_RALLY_STATE_CANDIDATE_COLUMNS
    )


@dataclass(frozen=True, slots=True)
class DrawdownLadderStage0BuildConfig:
    source_dir: Path = Path(".output/market/binance_vision/um_futures/enriched_1m")
    output_dir: Path = Path(".output/research/drawdown_ladder/stage0_is")
    workers: int = 4
    max_inflight_symbols: int | None = None
    max_symbols: int | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.workers <= 8:
            raise DrawdownLadderBuildError("workers must be between 1 and 8")
        if self.max_inflight_symbols is not None and self.max_inflight_symbols < self.workers:
            raise DrawdownLadderBuildError(
                "max_inflight_symbols must be greater than or equal to workers"
            )
        if self.max_symbols is not None and self.max_symbols <= 0:
            raise DrawdownLadderBuildError("max_symbols must be positive when provided")


@dataclass(frozen=True, slots=True)
class SymbolStage0Stats:
    symbol: str
    minute_rows: int
    eligible_session_count: int
    incomplete_session_count: int
    parent_event_count: int
    level_candidate_count: int
    ladder_state_candidate_count: int
    first_snapshot_time_ms: int | None
    last_snapshot_time_ms: int | None


@dataclass(frozen=True, slots=True)
class Stage0BuildResult:
    output_dir: Path
    level_candidates_path: Path
    level_outcomes_path: Path
    ladder_state_candidates_path: Path
    ladder_state_outcomes_path: Path
    manifest_path: Path
    audit_path: Path


@dataclass(frozen=True, slots=True)
class _CandidateMeasurementRequest:
    candidate_id: str
    parent_event_id: str
    candidate_kind: str
    symbol: str
    snapshot_time_ms: int
    feature_cutoff_time_ms: int
    fill_bar_index: int
    entry_price: float


@dataclass(frozen=True, slots=True)
class SymbolStage0OutputPaths:
    level_candidates: Path
    level_outcomes: Path
    ladder_state_candidates: Path
    ladder_state_outcomes: Path


def _stable_id(prefix: str, *parts: object) -> str:
    payload = "|".join(str(part) for part in parts)
    digest = hashlib.blake2b(payload.encode("utf-8"), digest_size=12).hexdigest()
    return f"{prefix}_{digest}"


def _atomic_write_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_parquet(temporary, index=False, compression="zstd")
    temporary.replace(path)


def _frame(rows: list[dict[str, object]], columns: tuple[str, ...]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns)
    integer_columns = {
        "utc_day",
        "session_seq",
        "level_depth_pct",
        "trade_through_bps",
        "grid_step_pct",
        "filled_level_count",
        "deepest_filled_level_pct",
        "maximum_horizon_minutes",
        "available_future_minutes",
    }
    boolean_columns = {
        "session_complete",
        "untouched_2026_row_used",
        "horizon_complete",
        "gross_break_even_reached",
    }
    for column in columns:
        if (
            column in boolean_columns
            or column.startswith("label_available_")
            or column.startswith("break_even_") and column.endswith("_reached")
        ):
            frame[column] = frame[column].astype("bool")
        elif (
            column in integer_columns
            or column.endswith("_time_ms")
            or column.endswith("_time_ms_exclusive")
            or column.endswith("_minutes")
            or column.endswith("_ms")
        ):
            frame[column] = pd.array(frame[column], dtype="Int64")
        elif (
            column.endswith("_price")
            or "return_" in column
            or column.endswith("_drawdown_pct")
            or column.endswith("_rally_pct")
        ):
            frame[column] = pd.array(frame[column], dtype="Float64")
        else:
            frame[column] = frame[column].astype("string")
    return frame


def _effective_trade_through_depth(depth_pct: int, trade_through_bps: int) -> float:
    remaining = 1.0 - depth_pct / 100.0
    return 1.0 - remaining * (1.0 - trade_through_bps / 10_000.0)


def _effective_trade_through_rally(depth_pct: int, trade_through_bps: int) -> float:
    return (1.0 + depth_pct / 100.0) * (1.0 + trade_through_bps / 10_000.0) - 1.0


def _harmonic_equal_notional_entry(prices: np.ndarray) -> float:
    if len(prices) == 0 or bool((prices <= 0.0).any()):
        raise DrawdownLadderBuildError("equal-notional entry requires positive prices")
    return float(len(prices) / np.sum(1.0 / prices))


def _outcome_row(
    request: _CandidateMeasurementRequest,
    *,
    measurement: RecoveryPathMeasurement | None,
    censor_reason: str,
    spec: DrawdownLadderStage0Spec,
) -> dict[str, object]:
    if measurement is None:
        row: dict[str, object] = {
            "outcome_schema_version": spec.outcome_schema_version,
            "protocol_version": spec.protocol_version,
            "protocol_freeze_id": spec.protocol_freeze_id,
            "research_partition": spec.research_partition,
            "candidate_kind": request.candidate_kind,
            "parent_event_id": request.parent_event_id,
            "candidate_id": request.candidate_id,
            "symbol": request.symbol,
            "snapshot_time_ms": request.snapshot_time_ms,
            "feature_cutoff_time_ms": request.feature_cutoff_time_ms,
            "future_start_time_ms": request.snapshot_time_ms + MS_PER_MINUTE,
            "maximum_horizon_minutes": spec.maximum_horizon_minutes,
            "available_future_minutes": 0,
            "horizon_complete": False,
            "censor_reason": censor_reason,
            "gross_break_even_reached": False,
            "time_to_gross_break_even_minutes": None,
        }
        for cost in spec.round_trip_cost_bps:
            row[f"break_even_{cost}bps_reached"] = False
            row[f"time_to_break_even_{cost}bps_minutes"] = None
        for horizon in spec.response_horizons_minutes:
            row[f"label_available_{horizon}m"] = False
            row[f"future_return_{horizon}m"] = None
            row[f"future_max_return_{horizon}m"] = None
            row[f"future_min_return_{horizon}m"] = None
        row["untouched_2026_row_used"] = False
        return row

    row = {
        "outcome_schema_version": spec.outcome_schema_version,
        "protocol_version": spec.protocol_version,
        "protocol_freeze_id": spec.protocol_freeze_id,
        "research_partition": spec.research_partition,
        "candidate_kind": request.candidate_kind,
        "parent_event_id": request.parent_event_id,
        "candidate_id": request.candidate_id,
        "symbol": request.symbol,
        "snapshot_time_ms": request.snapshot_time_ms,
        "feature_cutoff_time_ms": request.feature_cutoff_time_ms,
        "future_start_time_ms": measurement.future_start_time_ms,
        "maximum_horizon_minutes": measurement.maximum_horizon_minutes,
        "available_future_minutes": measurement.available_future_minutes,
        "horizon_complete": measurement.horizon_complete,
        "censor_reason": censor_reason,
        "gross_break_even_reached": measurement.gross_break_even_reached,
        "time_to_gross_break_even_minutes": measurement.time_to_gross_break_even_minutes,
    }
    for cost, time_to_recovery in zip(
        spec.round_trip_cost_bps,
        measurement.cost_break_even_times_minutes,
        strict=True,
    ):
        row[f"break_even_{cost}bps_reached"] = time_to_recovery is not None
        row[f"time_to_break_even_{cost}bps_minutes"] = time_to_recovery
    for horizon in measurement.horizon_metrics:
        suffix = f"{horizon.horizon_minutes}m"
        row[f"label_available_{suffix}"] = horizon.label_available
        row[f"future_return_{suffix}"] = horizon.close_return
        row[f"future_max_return_{suffix}"] = horizon.maximum_return
        row[f"future_min_return_{suffix}"] = horizon.minimum_return
    row["untouched_2026_row_used"] = False
    return row


def build_symbol_stage0(
    source_path: str | Path,
    *,
    shard_paths: SymbolStage0OutputPaths,
    spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
) -> SymbolStage0Stats:
    source = Path(source_path)
    symbol = source.stem.upper()
    minute = read_is_symbol_minutes(source)
    if minute.empty:
        _atomic_write_parquet(
            _frame([], level_candidate_columns(spec)), shard_paths.level_candidates
        )
        _atomic_write_parquet(_frame([], outcome_columns(spec)), shard_paths.level_outcomes)
        _atomic_write_parquet(
            _frame([], ladder_state_candidate_columns(spec)),
            shard_paths.ladder_state_candidates,
        )
        _atomic_write_parquet(_frame([], outcome_columns(spec)), shard_paths.ladder_state_outcomes)
        return SymbolStage0Stats(symbol, 0, 0, 0, 0, 0, 0, None, None)

    timestamps = minute["timestamp"].to_numpy(dtype=np.int64)
    open_ = minute["open"].to_numpy(dtype=float)
    high = minute["high"].to_numpy(dtype=float)
    low = minute["low"].to_numpy(dtype=float)
    close = minute["close"].to_numpy(dtype=float)
    if bool((~np.isfinite(np.column_stack((open_, high, low, close)))).any()):
        raise DrawdownLadderBuildError(f"{symbol} contains non-finite OHLC values")
    if bool((np.column_stack((open_, high, low, close)) <= 0.0).any()):
        raise DrawdownLadderBuildError(f"{symbol} contains non-positive OHLC values")
    if bool((high < low).any()):
        raise DrawdownLadderBuildError(f"{symbol} has high below low")

    recovery = RecoveryPathIndex(
        timestamps_ms=timestamps,
        high=high,
        low=low,
        close=close,
    )
    start_minutes = np.asarray(
        [block.start_hour * 60 for block in UTC_SESSION_BLOCKS], dtype=np.int64
    )
    minute_of_day = (timestamps % (24 * 60 * MS_PER_MINUTE)) // MS_PER_MINUTE
    session_start_indices = np.flatnonzero(np.isin(minute_of_day, start_minutes))
    level_rows: list[dict[str, object]] = []
    state_rows: list[dict[str, object]] = []
    level_requests: list[_CandidateMeasurementRequest] = []
    state_requests: list[_CandidateMeasurementRequest] = []
    eligible_sessions = 0
    incomplete_sessions = 0
    parent_ids: set[str] = set()
    levels = np.asarray(spec.measurement_levels_pct, dtype=np.int64)
    if spec.study_side == "long":
        fill_thresholds = np.asarray(
            [
                _effective_trade_through_depth(int(depth), spec.trade_through_bps)
                for depth in levels
            ],
            dtype=float,
        )
        same_bar_excursion_column = "same_bar_max_drawdown_pct"
    else:
        fill_thresholds = np.asarray(
            [
                _effective_trade_through_rally(int(depth), spec.trade_through_bps)
                for depth in levels
            ],
            dtype=float,
        )
        same_bar_excursion_column = "same_bar_max_rally_pct"

    for start_index_raw in session_start_indices:
        start_index = int(start_index_raw)
        session_start_ms = int(timestamps[start_index])
        if start_index == 0 or timestamps[start_index - 1] != session_start_ms - MS_PER_MINUTE:
            continue
        session_seq = next(
            block.seq
            for block in UTC_SESSION_BLOCKS
            if block.start_hour * 60 == int(minute_of_day[start_index])
        )
        block = UTC_SESSION_BY_SEQ[session_seq]
        expected_end_ms = session_start_ms + block.duration_minutes * MS_PER_MINUTE
        expected_stop = int(np.searchsorted(timestamps, expected_end_ms, side="left"))
        contiguous_stop = recovery.contiguous_end_exclusive(start_index)
        observed_stop = min(expected_stop, contiguous_stop)
        session_complete = bool(
            observed_stop - start_index == block.duration_minutes
            and observed_stop > start_index
            and timestamps[observed_stop - 1] == expected_end_ms - MS_PER_MINUTE
        )
        if not session_complete:
            incomplete_sessions += 1
        active_index = start_index + spec.order_activation_delay_minutes
        active_time_ms = session_start_ms + spec.order_activation_delay_minutes * MS_PER_MINUTE
        if active_index >= observed_stop or timestamps[active_index] != active_time_ms:
            continue
        eligible_sessions += 1
        anchor_price = float(close[start_index - 1])
        if not np.isfinite(anchor_price) or anchor_price <= 0.0:
            raise DrawdownLadderBuildError(f"{symbol} has an invalid session anchor price")
        if spec.study_side == "long":
            observed_excursion = 1.0 - low[active_index:observed_stop] / anchor_price
        else:
            observed_excursion = high[active_index:observed_stop] / anchor_price - 1.0
        running_max_excursion = np.maximum.accumulate(observed_excursion)
        fill_offsets = np.searchsorted(running_max_excursion, fill_thresholds, side="left")
        filled_mask = fill_offsets < len(running_max_excursion)
        if not bool(filled_mask.any()):
            continue

        utc_day = session_start_ms // (24 * 60 * MS_PER_MINUTE)
        parent_event_id = _stable_id(
            "ddlparent" if spec.study_side == "long" else "mrlparent",
            symbol,
            session_start_ms,
        )
        parent_ids.add(parent_event_id)
        filled_levels = levels[filled_mask]
        filled_indices = active_index + fill_offsets[filled_mask]
        direction_sign = -1.0 if spec.study_side == "long" else 1.0
        limit_prices = anchor_price * (
            1.0 + direction_sign * filled_levels.astype(float) / 100.0
        )
        fill_index_by_level = {
            int(depth): int(fill_index)
            for depth, fill_index in zip(filled_levels, filled_indices, strict=True)
        }
        limit_by_level = {
            int(depth): float(price)
            for depth, price in zip(filled_levels, limit_prices, strict=True)
        }

        for depth, fill_index, limit_price in zip(
            filled_levels,
            filled_indices,
            limit_prices,
            strict=True,
        ):
            fill_index = int(fill_index)
            fill_open_ms = int(timestamps[fill_index])
            fill_observed_ms = fill_open_ms + MS_PER_MINUTE
            candidate_id = _stable_id(
                "ddllevel" if spec.study_side == "long" else "mrllevel",
                parent_event_id,
                int(depth),
            )
            level_rows.append(
                {
                    "candidate_schema_version": spec.candidate_schema_version,
                    "protocol_version": spec.protocol_version,
                    "protocol_freeze_id": spec.protocol_freeze_id,
                    "research_partition": spec.research_partition,
                    "parent_event_id": parent_event_id,
                    "candidate_id": candidate_id,
                    "symbol": symbol,
                    "utc_day": int(utc_day),
                    "session_seq": session_seq,
                    "session_name": block.name,
                    "anchor_snapshot_time_ms": session_start_ms,
                    "anchor_price": anchor_price,
                    "feature_cutoff_time_ms": session_start_ms,
                    "snapshot_time_ms": fill_observed_ms,
                    "order_active_time_ms": active_time_ms,
                    "order_expiry_time_ms": expected_end_ms,
                    "level_depth_pct": int(depth),
                    "limit_price": float(limit_price),
                    "trade_through_bps": spec.trade_through_bps,
                    "fill_bar_open_time_ms": fill_open_ms,
                    "fill_observed_time_ms": fill_observed_ms,
                    "minutes_from_order_activation": int(
                        (fill_observed_ms - active_time_ms) // MS_PER_MINUTE
                    ),
                    same_bar_excursion_column: float(
                        100.0
                        * (
                            1.0 - low[fill_index] / anchor_price
                            if spec.study_side == "long"
                            else high[fill_index] / anchor_price - 1.0
                        )
                    ),
                    "session_complete": session_complete,
                    "untouched_2026_row_used": False,
                }
            )
            level_requests.append(
                _CandidateMeasurementRequest(
                    candidate_id=candidate_id,
                    parent_event_id=parent_event_id,
                    candidate_kind="level",
                    symbol=symbol,
                    snapshot_time_ms=fill_observed_ms,
                    feature_cutoff_time_ms=session_start_ms,
                    fill_bar_index=fill_index,
                    entry_price=float(limit_price),
                )
            )

        distinct_fill_indices = sorted(set(fill_index_by_level.values()))
        for grid_step in spec.registered_grid_steps_pct:
            previous_count = 0
            grid_levels = tuple(
                depth
                for depth in range(grid_step, spec.maximum_level_depth_pct + 1, grid_step)
                if depth in fill_index_by_level
            )
            for fill_index in distinct_fill_indices:
                levels_asof = tuple(
                    depth
                    for depth in grid_levels
                    if fill_index_by_level[depth] <= fill_index
                )
                if len(levels_asof) == previous_count:
                    continue
                previous_count = len(levels_asof)
                if not levels_asof:
                    continue
                prices = np.asarray([limit_by_level[depth] for depth in levels_asof], dtype=float)
                average_entry = _harmonic_equal_notional_entry(prices)
                fill_open_ms = int(timestamps[fill_index])
                fill_observed_ms = fill_open_ms + MS_PER_MINUTE
                state_id = _stable_id(
                    "ddlstate" if spec.study_side == "long" else "mrlstate",
                    parent_event_id,
                    grid_step,
                    fill_observed_ms,
                    levels_asof[-1],
                )
                state_rows.append(
                    {
                        "candidate_schema_version": spec.ladder_state_schema_version,
                        "protocol_version": spec.protocol_version,
                        "protocol_freeze_id": spec.protocol_freeze_id,
                        "research_partition": spec.research_partition,
                        "parent_event_id": parent_event_id,
                        "candidate_id": state_id,
                        "symbol": symbol,
                        "utc_day": int(utc_day),
                        "session_seq": session_seq,
                        "session_name": block.name,
                        "anchor_snapshot_time_ms": session_start_ms,
                        "anchor_price": anchor_price,
                        "feature_cutoff_time_ms": session_start_ms,
                        "snapshot_time_ms": fill_observed_ms,
                        "order_active_time_ms": active_time_ms,
                        "order_expiry_time_ms": expected_end_ms,
                        "grid_step_pct": grid_step,
                        "filled_level_count": len(levels_asof),
                        "deepest_filled_level_pct": levels_asof[-1],
                        "equal_notional_average_entry_price": average_entry,
                        "fill_bar_open_time_ms": fill_open_ms,
                        "fill_observed_time_ms": fill_observed_ms,
                        "minutes_from_order_activation": int(
                            (fill_observed_ms - active_time_ms) // MS_PER_MINUTE
                        ),
                        same_bar_excursion_column: float(
                            100.0
                            * (
                                1.0 - low[fill_index] / anchor_price
                                if spec.study_side == "long"
                                else high[fill_index] / anchor_price - 1.0
                            )
                        ),
                        "session_complete": session_complete,
                        "untouched_2026_row_used": False,
                    }
                )
                state_requests.append(
                    _CandidateMeasurementRequest(
                        candidate_id=state_id,
                        parent_event_id=parent_event_id,
                        candidate_kind=f"grid_{grid_step}pct_equal_notional_state",
                        symbol=symbol,
                        snapshot_time_ms=fill_observed_ms,
                        feature_cutoff_time_ms=session_start_ms,
                        fill_bar_index=fill_index,
                        entry_price=average_entry,
                    )
                )

    source_reaches_is_boundary = bool(
        len(timestamps)
        and timestamps[-1] >= last_open_time_ms_exclusive() - MS_PER_MINUTE
    )

    def materialize_outcomes(
        requests: list[_CandidateMeasurementRequest],
    ) -> list[dict[str, object]]:
        rows: list[dict[str, object]] = []
        for request in requests:
            first_future = request.fill_bar_index + 1
            if first_future >= len(timestamps):
                rows.append(
                    _outcome_row(
                        request,
                        measurement=None,
                        censor_reason=(
                            "is_boundary" if source_reaches_is_boundary else "symbol_history_ended"
                        ),
                        spec=spec,
                    )
                )
                continue
            measurement = recovery.measure(
                entry_price=request.entry_price,
                snapshot_time_ms=request.snapshot_time_ms,
                first_future_bar_index=first_future,
                maximum_horizon_minutes=spec.maximum_horizon_minutes,
                horizons_minutes=spec.response_horizons_minutes,
                round_trip_cost_bps=spec.round_trip_cost_bps,
                direction=spec.study_side,
            )
            if measurement.horizon_complete:
                censor_reason = "none"
            elif measurement.ended_at_data_gap:
                censor_reason = "path_gap"
            else:
                censor_reason = (
                    "is_boundary" if source_reaches_is_boundary else "symbol_history_ended"
                )
            rows.append(
                _outcome_row(
                    request,
                    measurement=measurement,
                    censor_reason=censor_reason,
                    spec=spec,
                )
            )
        return rows

    level_outcomes = materialize_outcomes(level_requests)
    state_outcomes = materialize_outcomes(state_requests)
    _atomic_write_parquet(
        _frame(level_rows, level_candidate_columns(spec)), shard_paths.level_candidates
    )
    _atomic_write_parquet(
        _frame(level_outcomes, outcome_columns(spec)), shard_paths.level_outcomes
    )
    _atomic_write_parquet(
        _frame(state_rows, ladder_state_candidate_columns(spec)),
        shard_paths.ladder_state_candidates,
    )
    _atomic_write_parquet(
        _frame(state_outcomes, outcome_columns(spec)), shard_paths.ladder_state_outcomes
    )
    snapshots = [int(row["snapshot_time_ms"]) for row in (*level_rows, *state_rows)]
    return SymbolStage0Stats(
        symbol=symbol,
        minute_rows=len(minute),
        eligible_session_count=eligible_sessions,
        incomplete_session_count=incomplete_sessions,
        parent_event_count=len(parent_ids),
        level_candidate_count=len(level_rows),
        ladder_state_candidate_count=len(state_rows),
        first_snapshot_time_ms=min(snapshots) if snapshots else None,
        last_snapshot_time_ms=max(snapshots) if snapshots else None,
    )


def _resolve_inflight(config: DrawdownLadderStage0BuildConfig) -> int:
    return config.max_inflight_symbols or config.workers * 2


def _shard_paths(output_dir: Path, symbol: str) -> SymbolStage0OutputPaths:
    return SymbolStage0OutputPaths(
        level_candidates=output_dir / "shards" / "level_candidates" / f"{symbol}.parquet",
        level_outcomes=output_dir / "shards" / "level_outcomes" / f"{symbol}.parquet",
        ladder_state_candidates=output_dir
        / "shards"
        / "ladder_state_candidates"
        / f"{symbol}.parquet",
        ladder_state_outcomes=output_dir
        / "shards"
        / "ladder_state_outcomes"
        / f"{symbol}.parquet",
    )


def _build_symbol_task(
    source_path: Path,
    output_dir: Path,
    spec: DrawdownLadderStage0Spec,
) -> SymbolStage0Stats:
    return build_symbol_stage0(
        source_path,
        shard_paths=_shard_paths(output_dir, source_path.stem.upper()),
        spec=spec,
    )


def _iter_bounded_builds(
    paths: list[Path],
    *,
    output_dir: Path,
    spec: DrawdownLadderStage0Spec,
    workers: int,
    max_inflight: int,
) -> Iterator[SymbolStage0Stats]:
    if workers == 1:
        for path in paths:
            yield _build_symbol_task(path, output_dir, spec)
        return
    executor = ProcessPoolExecutor(max_workers=workers)
    pending: dict[Future[SymbolStage0Stats], int] = {}
    completed: dict[int, SymbolStage0Stats] = {}
    next_submit = 0
    next_emit = 0

    def submit_until_capacity() -> None:
        nonlocal next_submit
        while next_submit < len(paths) and len(pending) + len(completed) < max_inflight:
            future = executor.submit(_build_symbol_task, paths[next_submit], output_dir, spec)
            pending[future] = next_submit
            next_submit += 1

    try:
        submit_until_capacity()
        while next_emit < len(paths):
            while next_emit not in completed:
                if not pending:
                    raise DrawdownLadderBuildError("bounded Stage-0 worker pool stalled")
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    completed[pending.pop(future)] = future.result()
                submit_until_capacity()
            yield completed.pop(next_emit)
            next_emit += 1
    finally:
        executor.shutdown(wait=True, cancel_futures=True)


def _consolidate(shard_dir: Path, output_path: Path) -> None:
    paths = sorted(shard_dir.glob("*.parquet"))
    if not paths:
        raise DrawdownLadderBuildError(f"no Stage-0 shards found in {shard_dir}")
    pl.scan_parquet([str(path) for path in paths]).sink_parquet(
        output_path,
        compression="zstd",
        engine="streaming",
    )


def _audit_outputs(
    *,
    level_candidates_path: Path,
    level_outcomes_path: Path,
    ladder_state_candidates_path: Path,
    ladder_state_outcomes_path: Path,
    spec: DrawdownLadderStage0Spec,
) -> dict[str, object]:
    oos_start = spec.research_split.oos_start_time_ms
    report: dict[str, object] = {
        "protocol_freeze_id": spec.protocol_freeze_id,
        "research_partition": "is",
        "oos_start_time_ms": oos_start,
        "checks": {},
    }
    checks = report["checks"]
    assert isinstance(checks, dict)
    for kind, candidate_path, outcome_path in (
        ("level", level_candidates_path, level_outcomes_path),
        ("ladder_state", ladder_state_candidates_path, ladder_state_outcomes_path),
    ):
        candidates = pl.read_parquet(
            candidate_path,
            columns=[
                "candidate_id",
                "parent_event_id",
                "feature_cutoff_time_ms",
                "snapshot_time_ms",
                "untouched_2026_row_used",
            ],
        )
        outcomes = pl.read_parquet(
            outcome_path,
            columns=[
                "candidate_id",
                "parent_event_id",
                "feature_cutoff_time_ms",
                "snapshot_time_ms",
                "future_start_time_ms",
                "untouched_2026_row_used",
            ],
        )
        if candidates["candidate_id"].n_unique() != len(candidates):
            raise DrawdownLadderBuildError(f"{kind} candidate_id is not unique")
        if outcomes["candidate_id"].n_unique() != len(outcomes):
            raise DrawdownLadderBuildError(f"{kind} outcome candidate_id is not unique")
        if len(candidates) != len(outcomes):
            raise DrawdownLadderBuildError(f"{kind} candidate/outcome row count mismatch")
        if candidates["candidate_id"].sort().to_list() != outcomes["candidate_id"].sort().to_list():
            raise DrawdownLadderBuildError(f"{kind} candidate/outcome keys differ")
        if len(candidates) and not bool(
            candidates["feature_cutoff_time_ms"].le(candidates["snapshot_time_ms"]).all()
        ):
            raise DrawdownLadderBuildError(f"{kind} feature cutoff exceeds snapshot")
        if len(outcomes) and not bool(
            outcomes["future_start_time_ms"].gt(outcomes["snapshot_time_ms"]).all()
        ):
            raise DrawdownLadderBuildError(f"{kind} future path does not start after snapshot")
        if len(candidates) and int(candidates["snapshot_time_ms"].max()) >= oos_start:
            raise DrawdownLadderBuildError(f"{kind} candidate crossed into OOS")
        if bool(candidates["untouched_2026_row_used"].any()) or bool(
            outcomes["untouched_2026_row_used"].any()
        ):
            raise DrawdownLadderBuildError(f"{kind} reports OOS access")
        checks[kind] = {
            "candidate_rows": len(candidates),
            "outcome_rows": len(outcomes),
            "unique_parent_events": candidates["parent_event_id"].n_unique(),
            "candidate_keys_match": True,
            "feature_cutoff_le_snapshot": True,
            "future_start_gt_snapshot": True,
            "untouched_2026_rows_used": 0,
        }
    report["status"] = "PASS"
    return report


def build_stage0_is(
    config: DrawdownLadderStage0BuildConfig = DrawdownLadderStage0BuildConfig(),
    *,
    spec: DrawdownLadderStage0Spec = DrawdownLadderStage0Spec(),
    progress: Callable[[str], None] | None = print,
) -> Stage0BuildResult:
    if not config.source_dir.is_dir():
        raise FileNotFoundError(config.source_dir)
    manifest_path = config.output_dir / "manifest.json"
    if manifest_path.exists():
        raise DrawdownLadderBuildError(
            f"refusing to mix a new run with existing Stage-0 output: {manifest_path}"
        )
    all_paths = sorted(config.source_dir.glob("*.parquet"), key=lambda path: path.stem)
    excluded_delivery = tuple(
        path.stem for path in all_paths if is_delivery_contract_symbol(path.stem)
    )
    paths = [path for path in all_paths if not is_delivery_contract_symbol(path.stem)]
    if config.max_symbols is not None:
        paths = paths[: config.max_symbols]
    if not paths:
        raise DrawdownLadderBuildError("no perpetual symbol cache files found")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    stats: list[SymbolStage0Stats] = []
    for completed, result in enumerate(
        _iter_bounded_builds(
            paths,
            output_dir=config.output_dir,
            spec=spec,
            workers=config.workers,
            max_inflight=_resolve_inflight(config),
        ),
        start=1,
    ):
        stats.append(result)
        if progress and (completed % 25 == 0 or completed == len(paths)):
            progress(
                f"{spec.event_family} Stage 0 {completed}/{len(paths)}; "
                f"levels={sum(item.level_candidate_count for item in stats):,}; "
                f"states={sum(item.ladder_state_candidate_count for item in stats):,}"
            )

    level_candidates_path = config.output_dir / "level_candidates.parquet"
    level_outcomes_path = config.output_dir / "level_outcomes.parquet"
    ladder_state_candidates_path = config.output_dir / "ladder_state_candidates.parquet"
    ladder_state_outcomes_path = config.output_dir / "ladder_state_outcomes.parquet"
    for shard_name, output_path in (
        ("level_candidates", level_candidates_path),
        ("level_outcomes", level_outcomes_path),
        ("ladder_state_candidates", ladder_state_candidates_path),
        ("ladder_state_outcomes", ladder_state_outcomes_path),
    ):
        _consolidate(config.output_dir / "shards" / shard_name, output_path)

    audit = _audit_outputs(
        level_candidates_path=level_candidates_path,
        level_outcomes_path=level_outcomes_path,
        ladder_state_candidates_path=ladder_state_candidates_path,
        ladder_state_outcomes_path=ladder_state_outcomes_path,
        spec=spec,
    )
    audit_path = config.output_dir / "temporal_audit.json"
    audit_path.write_text(json.dumps(audit, indent=2, sort_keys=True), encoding="utf-8")
    level_summary_path, session_summary_path, state_summary_path = (
        build_stage0_recovery_summaries(stage0_dir=config.output_dir, spec=spec)
    )
    manifest = {
        "protocol": {
            **asdict(spec),
            "study_side": spec.study_side,
            "event_family": spec.event_family,
        },
        "research_split": {
            "split_version": spec.research_split.split_version,
            "is_start_time_ms": spec.research_split.is_start_time_ms,
            "oos_start_time_ms": spec.research_split.oos_start_time_ms,
            "physical_last_open_time_ms_exclusive": last_open_time_ms_exclusive(),
        },
        "source_dir": str(config.source_dir),
        "output_dir": str(config.output_dir),
        "workers": config.workers,
        "max_inflight_symbols": _resolve_inflight(config),
        "worker_scheduling_contract": "bounded_inflight_symbol_pool_v1",
        "source_symbol_count": len(paths),
        "excluded_delivery_symbols": list(excluded_delivery),
        "level_candidate_count": sum(item.level_candidate_count for item in stats),
        "ladder_state_candidate_count": sum(
            item.ladder_state_candidate_count for item in stats
        ),
        "unique_parent_event_count": sum(item.parent_event_count for item in stats),
        "untouched_2026_rows_used": 0,
        "symbol_stats": [asdict(item) for item in sorted(stats, key=lambda row: row.symbol)],
        "artifacts": {
            "level_candidates": str(level_candidates_path),
            "level_outcomes": str(level_outcomes_path),
            "ladder_state_candidates": str(ladder_state_candidates_path),
            "ladder_state_outcomes": str(ladder_state_outcomes_path),
            "temporal_audit": str(audit_path),
            "level_recovery_summary": str(level_summary_path),
            "level_session_recovery_summary": str(session_summary_path),
            "ladder_state_recovery_summary": str(state_summary_path),
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return Stage0BuildResult(
        output_dir=config.output_dir,
        level_candidates_path=level_candidates_path,
        level_outcomes_path=level_outcomes_path,
        ladder_state_candidates_path=ladder_state_candidates_path,
        ladder_state_outcomes_path=ladder_state_outcomes_path,
        manifest_path=manifest_path,
        audit_path=audit_path,
    )


def main() -> None:
    defaults = DrawdownLadderStage0BuildConfig()
    parser = argparse.ArgumentParser(
        description="Build the frozen IS-only blind drawdown-ladder Stage-0 event study."
    )
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=defaults.source_dir,
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=defaults.output_dir,
    )
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-inflight-symbols", type=int, default=None)
    parser.add_argument("--max-symbols", type=int, default=None)
    args = parser.parse_args()
    result = build_stage0_is(
        DrawdownLadderStage0BuildConfig(
            source_dir=args.source_dir,
            output_dir=args.output_dir,
            workers=args.workers,
            max_inflight_symbols=args.max_inflight_symbols,
            max_symbols=args.max_symbols,
        ),
        progress=lambda message: print(message, flush=True),
    )
    print(f"drawdown-ladder Stage 0 written: {result.output_dir}")


if __name__ == "__main__":
    main()


__all__ = [
    "DrawdownLadderBuildError",
    "DrawdownLadderStage0BuildConfig",
    "LADDER_STATE_CANDIDATE_COLUMNS",
    "LEVEL_CANDIDATE_COLUMNS",
    "MIRRORED_RALLY_LEVEL_CANDIDATE_COLUMNS",
    "MIRRORED_RALLY_STATE_CANDIDATE_COLUMNS",
    "Stage0BuildResult",
    "SymbolStage0OutputPaths",
    "SymbolStage0Stats",
    "build_stage0_is",
    "build_symbol_stage0",
    "ladder_state_candidate_columns",
    "level_candidate_columns",
    "outcome_columns",
]
